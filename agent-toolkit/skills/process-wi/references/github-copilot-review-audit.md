# GitHub Copilotレビュー監査

`agent-toolkit:process-wi`のセッション終了工程から起動された読み取り専用の探索委譲先が、対象GitHubリポジトリで実行する。
新しいレビューの生成を要求せず、到着を能動的に待機せず、呼び出し時点の保存結果を1回監査する。

## 対象

Copilot由来のreview本文は、状態（open・closed・merged）を問わず全Pull Requestを対象に取得する。
inline commentもreview threadも伴わずreview本文だけが到着する場合があり、未解決threadの有無で対象を限定すると当該本文が漏れるためである。
Copilot由来のinline commentとreview threadは、未解決のreview threadを持つPull Requestだけを対象に取得する。
本監査は是正済みと根拠付き対応不要の未解決threadを解決するため、解決済みのthreadだけを持つPull Requestは当該時点で未処置のinline commentを持たない。
要修正としてAWIへ記録した指摘のthreadは未解決のまま残り、以降も対象に入り続ける。
authorのloginに`copilot`を大文字小文字を区別せず含むことをCopilot由来の判定条件とする。

## 取得

Copilot由来のreview本文の取得と、review threadの解決状態による対象判定は、独立した接続として扱い、それぞれpaginationの終端まで取得する。
いずれの接続も、初回は`cursor`を渡さず、`pageInfo.hasNextPage`が真の場合は、直前の`pageInfo.endCursor`を
`-F cursor=<END_CURSOR>`で渡して偽になるまで取得する。
Pull Request単位のクエリーは`cursor`を渡さない状態から取得し直し、横断クエリーの結果を継続しない。

全Pull Requestの番号、`reviews`の先頭ページ及び`reviewThreads`の先頭ページを、次の横断GraphQLクエリーで取得する。

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -f query='query($owner:String!,$name:String!,$cursor:String){repository(owner:$owner,name:$name){pullRequests(first:25,after:$cursor,states:[OPEN,CLOSED,MERGED]){nodes{number reviews(first:20){nodes{databaseId body author{login}} pageInfo{hasNextPage}} reviewThreads(first:100){nodes{id isResolved comments(first:1){nodes{databaseId author{login}}}} pageInfo{hasNextPage}}} pageInfo{hasNextPage endCursor}}}}'
```

`reviews`の`pageInfo.hasNextPage`が真のPull Requestは、次のクエリーでreview本文を終端まで取得し直す。

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -F number=<PR> -f query='query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviews(first:100,after:$cursor){nodes{databaseId body author{login}} pageInfo{hasNextPage endCursor}}}}}'
```

`reviewThreads`の`pageInfo.hasNextPage`が真のPull Requestは、次のクエリーでreview threadを終端まで取得し直す。
未解決threadの有無は、当該取得が終端へ到達するまで確定しない。
thread内のcomment本文は後掲のREST APIを正本とし、GraphQLではthread ID、解決状態及び
REST commentとの対応に使うdatabaseIdだけを取得する。

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -F number=<PR> -f query='query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{id isResolved comments(first:1){nodes{databaseId}}} pageInfo{hasNextPage endCursor}}}}}'
```

両方の接続が終端へ到達した後、未解決のreview threadを持つPull Request番号を`<PR>`へ置換し、inline commentを全ページ取得する。

```sh
gh api --paginate 'repos/{owner}/{repo}/pulls/<PR>/comments?per_page=100'
```

REST APIが非0で終了した場合、GraphQLの`pageInfo`を取得できない場合又は
pagination終端へ到達できない場合は、監査を完了として扱わない。

## 判定

各指摘を現行成果物、過去の採否及び根拠へ照合し、要修正、是正済み、根拠付き対応不要のいずれかへ分類する。
要修正は所在と対処案を返し、同一セッションの是正とAWIへの記録は呼び出し元が確定する。
是正済み又は根拠付き対応不要で未解決のthreadは、解決対象として呼び出し元へ返す。
全Pull RequestのCopilot由来のreview本文と、未解決threadを持つPull RequestのCopilot由来のinline commentについて、所在、分類及び処置をメインへ返す。inline commentの取得対象へ入らなかったPull Request番号と、その判定に用いたクエリーの結果も併せて返す。
