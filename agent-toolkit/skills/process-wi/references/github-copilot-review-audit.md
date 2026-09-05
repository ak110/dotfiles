# GitHub Copilotレビュー監査

`finish-session.md`から呼び出されたメイン又は読み取り専用の探索委譲先が、対象GitHubリポジトリで実行する。
新しいレビューの生成を要求せず、到着を能動的に待機せず、呼び出し時点の保存結果を1回監査する。

## 対象

状態を問わず全Pull Requestを母集団とする。
Copilot由来のreview本文、inline comment及びreview threadを取得し、未解決threadだけへ限定しない。
authorのloginに`copilot`を大文字小文字を区別せず含むことをCopilot由来の判定条件とする。

## 取得

全Pull Request番号は次のREST APIを全ページ取得して確定する。

```sh
gh api --paginate 'repos/{owner}/{repo}/pulls?state=all&per_page=100' --jq '.[].number'
```

取得した各Pull Request番号を`<PR>`へ置換し、review本文とinline commentをそれぞれ全ページ取得する。

```sh
gh api --paginate 'repos/{owner}/{repo}/pulls/<PR>/reviews?per_page=100'
gh api --paginate 'repos/{owner}/{repo}/pulls/<PR>/comments?per_page=100'
```

review threadの解決状態は次のGraphQLクエリーで取得する。
初回は`cursor`を渡さず、`pageInfo.hasNextPage`が真の場合は、直前の`pageInfo.endCursor`を
`-F cursor=<END_CURSOR>`で渡して偽になるまで取得する。
thread内のcomment本文は前記REST APIを正本とし、GraphQLではthread ID、解決状態及び
REST commentとの対応に使うdatabaseIdだけを取得する。

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -F number=<PR> -f query='query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{id isResolved comments(first:1){nodes{databaseId}}} pageInfo{hasNextPage endCursor}}}}}'
```

REST APIのいずれかが非0で終了した場合、GraphQLの`pageInfo`を取得できない場合又は
pagination終端へ到達できない場合は、監査を完了として扱わない。

## 判定

各指摘を現行成果物、過去の採否及び根拠へ照合し、要修正、是正済み、根拠付き対応不要のいずれかへ分類する。
要修正は同一セッションで是正するか`agent-toolkit:wi-standards`に従ってAWIへ記録する。
是正済み又は根拠付き対応不要で未解決のthreadは解決対象とする。
取得した全Pull Requestについて、Copilot由来の各review本文とinline commentの所在、分類及び処置をメインへ返す。
