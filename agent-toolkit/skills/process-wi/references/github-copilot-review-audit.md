# GitHub Copilotレビュー監査

`agent-toolkit:process-wi`の自動コードレビュー監査を担当する主体が、対象GitHubリポジトリの成果物を変更せずに実行する。
本書でいう成果物は、対象リポジトリの追跡ファイルとその履歴を指す。
Pull Requestのreview threadへの返信、Pull Requestへのコメント投稿及びthreadの解決は成果物の変更に当たらず、本書が定める範囲で監査担当が実行する。
監査は呼び出し時点の保存結果を1回読むだけで完了する。新しいレビューの生成要求と到着の能動的な待機は、本監査の範囲の外に置く。

## 対象

Copilot由来のreview本文は、状態（open・closed・merged）を問わず全Pull Requestを対象に取得する。
inline commentもreview threadも伴わずreview本文だけが到着する場合があり、未解決threadの有無で対象を限定すると、その本文が漏れるためである。
Copilot由来のinline commentとreview threadは、未解決のreview threadを持つPull Requestだけを対象に取得する。
解決済みのthreadだけを持つPull Requestは、その時点で未処置のinline commentを持たない。
要修正としてAWIへ記録した指摘のthreadは未解決のまま残り、以降も対象に入り続ける。
Copilot由来の判定条件は、authorの`__typename`が`Bot`であることと、authorのloginに`copilot`を大文字小文字を区別せず含むことの双方が成立することとする。login名だけで判定すると、その文字列を含む人間のアカウントの指摘へ自動返信と解決を書き込む。

## 取得

Copilot由来のreview本文の取得と、review threadの解決状態による対象判定は、独立した接続として扱い、それぞれpaginationの終端まで取得する。
いずれの接続も初回は`cursor`を渡さず、`pageInfo.hasNextPage`が真の場合は直前の`pageInfo.endCursor`を`-F cursor=<END_CURSOR>`で渡して偽になるまで取得する。
Pull Request単位のクエリーは横断クエリーの結果とは独立した取得として扱う。

全Pull Requestの番号、`reviews`の先頭ページ及び`reviewThreads`の先頭ページを、次の横断GraphQLクエリーで取得する。

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -f query='query($owner:String!,$name:String!,$cursor:String){repository(owner:$owner,name:$name){pullRequests(first:100,after:$cursor,states:[OPEN,CLOSED,MERGED]){nodes{number reviews(first:20){nodes{databaseId body author{__typename login}} pageInfo{hasNextPage}} reviewThreads(first:100){nodes{id isResolved comments(first:1){nodes{databaseId author{__typename login}}}} pageInfo{hasNextPage}}} pageInfo{hasNextPage endCursor}}}}'
```

`reviews`の`pageInfo.hasNextPage`が真のPull Requestは、次のクエリーでreview本文を終端まで取得し直す。

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -F number=<PR> -f query='query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviews(first:100,after:$cursor){nodes{databaseId body author{__typename login}} pageInfo{hasNextPage endCursor}}}}}'
```

`reviewThreads`の`pageInfo.hasNextPage`が真のPull Requestは、次のクエリーでreview threadを終端まで取得し直す。
未解決threadの有無は、その取得が終端へ到達した時点で確定する。
thread内のcomment本文は後掲のREST APIから取得し、GraphQLではthread ID、解決状態及び
REST commentとの対応に使うdatabaseIdだけを取得する。
横断クエリーの`comments`のauthorは、Copilot由来の判定にだけ用いる。

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -F number=<PR> -f query='query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{id isResolved comments(first:1){nodes{databaseId}}} pageInfo{hasNextPage endCursor}}}}}'
```

両方の接続が終端へ到達した後、未解決のreview threadを持つPull Request番号を`<PR>`へ置換し、inline commentを全ページ取得する。

```sh
gh api --paginate 'repos/{owner}/{repo}/pulls/<PR>/comments?per_page=100'
```

監査を完了と判定できるのは、REST APIが終了コード0で終わり、GraphQLの`pageInfo`を取得でき、かつpaginationが終端へ到達した場合とする。

## 判定

判定の前に、`atk review-audit list --repo <OWNER>/<REPO>`で判定済みのreview本文のdatabaseIdを取得する。
取得したdatabaseIdと一致するCopilot由来のreview本文を判定の対象から除き、除いたdatabaseIdの一覧と件数を呼び出し元へ返す。
「取得」の手順は本記録の有無で変えず、全Pull Requestのreview本文を毎回取得する。

判定の対象に残った各指摘を現行成果物、過去の採否及び根拠と比べ、要修正、是正済み、根拠付き対応不要のいずれかへ分類する。
review本文が概要と進行状況だけを述べ、成果物への処置を求める記述を1つも含まない場合は、その本文を指摘なしと分類する。
指摘なしの分類は本文全体に対して行い、本文へ含まれる個々の指摘の分類とは別の単位として扱う。
要修正は所在と対処案を返し、同一セッションの是正とAWIへの記録は呼び出し元が確定する。
是正済み又は根拠付き対応不要と分類した指摘は、「判定結果のGitHubへの記録」に従って分類と根拠をGitHubへ残す。
全Pull RequestのCopilot由来のreview本文と、未解決threadを持つPull RequestのCopilot由来のinline commentについて、所在、分類及び処置をメインへ返す。inline commentの取得対象へ入らなかったPull Request番号も併せて返す。

## 判定結果のGitHubへの記録

是正済み又は根拠付き対応不要と分類した指摘は、監査担当が分類と根拠を対象GitHubリポジトリへ書き込む。
判定根拠は本節が投稿する本文が保持し、`atk review-audit`のローカル記録は索引として扱う。
この書き込みは、操作、対象及び範囲を明示した人間由来のWI `20260908-090053-001.md`で承認済みであり、監査のたびの確認は不要である。
同WIが保持するユーザー発言は次のとおりである。

```text
判断結果がどこにも残らないのはいまいちだね。PRのレビュー指摘に対して「コメントしてCloseする」みたいな操作は無いの？
```

書き込みと解決の対象は、是正済みと根拠付き対応不要に分類した指摘に限る。要修正と分類した指摘のthreadは未解決のまま残す。
指摘なしと分類したreview本文も書き込みの対象から外す。書き込む根拠が本文に存在せず、投稿してもPull Requestを読む主体の判断材料が増えないためである。

監査担当はreview threadへの返信とPull Requestへのコメントのそれぞれについて、文面を保存した後、投稿の直前に`agent-toolkit:external-write-review`をSkill機能で起動する。
レビュー結果を反映した文面だけを投稿する。

未解決のreview threadでは、解決の前に分類と根拠をそのthreadへ返信し、返信の成功を確認してからthreadを解決する。
返信の本文はファイルへ保存して渡し、コマンド文字列とは別の入力として扱う。

```sh
gh api graphql -F threadId=<THREAD_ID> -F body=@<BODY_FILE> -f query='mutation($threadId:ID!,$body:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$threadId,body:$body}){comment{url}}}'
gh api graphql -F threadId=<THREAD_ID> -f reason=<RESOLUTION_REASON> -f query='mutation($threadId:ID!,$reason:PullRequestReviewThreadResolutionReason!){resolveReviewThread(input:{threadId:$threadId,resolutionReason:$reason}){thread{isResolved}}}'
```

`<RESOLUTION_REASON>`へ渡す値は分類ごとに次の表で一意に定まる。値の定義は本表に従い、実行例は書式の参考として読む。

| 分類 | `<RESOLUTION_REASON>` |
| --- | --- |
| 是正済み | `ADDRESSED` |
| 根拠付き対応不要 | `WONT_FIX` |

読み取り型`PullRequestReviewThread`は`resolutionReason`を返さないため、後続の判定は解決状態と本表から導く。

threadを伴わないreview本文では、分類と根拠をそのPull Requestへコメントとして投稿する。

```sh
gh pr comment <PR> --repo <OWNER>/<REPO> --body-file <BODY_FILE>
```

返信とコメントの本文には、対象の指摘を一意に示す識別子、確定した分類、及びその分類の根拠を書く。
識別子はreview本文ではdatabaseId、review threadでは対象ファイルと行とする。
根拠には確認した現行成果物の位置、又は対応不要と判断した理由を書く。

記録済みとして扱うのは書き込みが終了コード0で終わった指摘に限り、非0で終了した指摘は「判定済みの記録」の対象からも外す。

## 判定済みの記録

「判定結果のGitHubへの記録」の書き込みが成功したreview本文のdatabaseIdと、指摘なしと分類したreview本文のdatabaseIdを、`atk review-audit mark --repo <OWNER>/<REPO> <ID>...`で記録する。
指摘なしの本文を記録すると、その本文が以降の監査で判定の対象から外れ、同じ本文の読解の反復を避けられる。
要修正と分類したreview本文のdatabaseIdは、その指摘を記録したAWIが終端するまで判定の対象に残す。
記録するdatabaseIdはreview本文のものに限る。inline commentは未解決threadの解決状態が同じ役割を果たす。
記録の読み書きは`atk review-audit`だけで行う。記録ファイルのパス解決と保存形式をこのコマンドが定め、別の手段で同じファイルを読み書きすると形式が分岐するためである。
記録先は対象GitHubリポジトリの外にある状態ディレクトリであり、本記録は成果物を変更しない制約の対象に当たらない。
本記録は分類の再導出を省く索引であり、分類と根拠はGitHubへ残す記録が保持する。成果物の変更により再判定が必要になった指摘は、その変更に対する新しいreviewが別のdatabaseIdで到着するため、記録の無効化を経ずに再判定できる。
