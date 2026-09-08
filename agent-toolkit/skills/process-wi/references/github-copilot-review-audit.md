# GitHub Copilotレビュー監査

`agent-toolkit:process-wi`の自動コードレビュー監査を担当する主体が、対象GitHubリポジトリの成果物を変更せずに実行する。
本書でいう成果物は、対象リポジトリの追跡ファイルとその履歴を指す。
Pull Requestのreview threadへの返信、Pull Requestへのコメント投稿及びthreadの解決は成果物の変更に当たらず、本書が定める範囲で監査担当が実行する。
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

判定の前に、`atk review-audit list --repo <OWNER>/<REPO>`で判定済みのreview本文のdatabaseIdを取得する。
取得したdatabaseIdと一致するCopilot由来のreview本文を判定の対象から除き、除いたdatabaseIdの一覧と件数を呼び出し元へ返す。
「取得」の手順は本記録の有無で変えず、全Pull Requestのreview本文を毎回取得する。

判定の対象に残った各指摘を現行成果物、過去の採否及び根拠へ照合し、要修正、是正済み、根拠付き対応不要のいずれかへ分類する。
要修正は所在と対処案を返し、同一セッションの是正とAWIへの記録は呼び出し元が確定する。
是正済み又は根拠付き対応不要と分類した指摘は、「判定結果のGitHubへの記録」に従って分類と根拠をGitHubへ残す。
全Pull RequestのCopilot由来のreview本文と、未解決threadを持つPull RequestのCopilot由来のinline commentについて、所在、分類及び処置をメインへ返す。inline commentの取得対象へ入らなかったPull Request番号と、その判定に用いたクエリーの結果も併せて返す。

## 判定結果のGitHubへの記録

是正済み又は根拠付き対応不要と分類した指摘は、監査担当が分類と根拠を対象GitHubリポジトリへ書き込む。
判定根拠の正本は本節が投稿する本文とし、`atk review-audit`のローカル記録を根拠の保管先にしない。
当該書き込みは、操作、対象及び範囲を明示した人間由来のWIで承認済みであり、監査のたびに確認経路へ送らない。
要修正と分類した指摘へは書き込まず、当該threadを解決しない。

未解決のreview threadでは、解決の前に分類と根拠を当該threadへ返信し、返信の成功を確認してから当該threadを解決する。
返信の本文はファイルへ保存して渡し、コマンド文字列へ本文を連結しない。

```sh
gh api graphql -F threadId=<THREAD_ID> -F body=@<BODY_FILE> -f query='mutation($threadId:ID!,$body:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$threadId,body:$body}){comment{url}}}'
gh api graphql -F threadId=<THREAD_ID> -f reason=<RESOLUTION_REASON> -f query='mutation($threadId:ID!,$reason:PullRequestReviewThreadResolutionReason!){resolveReviewThread(input:{threadId:$threadId,resolutionReason:$reason}){thread{isResolved}}}'
```

`<RESOLUTION_REASON>`へ渡す値は分類ごとに次の表で一意に定まる。実行例へ特定の値を固定せず、本表を当該値の正本とする。

| 分類 | `<RESOLUTION_REASON>` |
| --- | --- |
| 是正済み | `ADDRESSED` |
| 根拠付き対応不要 | `WONT_FIX` |

読み取り型`PullRequestReviewThread`は`resolutionReason`を返さないため、当該値を後続の判定の入力にしない。

threadを伴わないreview本文では、分類と根拠を当該Pull Requestへコメントとして投稿する。

```sh
gh pr comment <PR> --repo <OWNER>/<REPO> --body-file <BODY_FILE>
```

返信とコメントの本文には、対象の指摘を一意に示す識別子、確定した分類、及び当該分類の根拠を書く。
識別子は、review本文ではdatabaseId、review threadでは対象ファイルと行とする。
根拠には、照合した現行成果物の位置、又は対応不要と判断した理由を書く。

書き込みが非0で終了した指摘は、記録済みとして扱わず「判定済みの記録」の記録も行わない。

## 判定済みの記録

「判定結果のGitHubへの記録」の書き込みが成功したreview本文のdatabaseIdを、`atk review-audit mark --repo <OWNER>/<REPO> <ID>...`で記録する。
要修正と分類したreview本文のdatabaseIdは記録せず、当該指摘を記録したAWIが終端するまで判定の対象に残す。
inline commentのdatabaseIdは記録の対象にしない。未解決threadの解決状態が同じ役割を果たすためである。
記録の読み書きは`atk review-audit`だけで行う。記録ファイルのパス解決と保存形式を当該コマンドが正本として持ち、別の手段で同じファイルを読み書きすると形式が分岐するためである。
記録先は対象GitHubリポジトリの外にある状態ディレクトリであり、本記録は成果物を変更しない制約の対象に当たらない。
記録は分類の再導出を省く目的だけに用いる。本記録は分類と根拠を保持しない索引であり、GitHubへ残す記録の代替にしない。成果物の変更により再判定が必要になった指摘は、当該変更に対する新しいreviewが別のdatabaseIdで到着するため、記録を無効化する手順を設けない。
