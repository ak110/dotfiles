# 自動コードレビュー監査

`agent-toolkit:process-wi`における自動コードレビュー監査は、担当エージェントが対象となるGitHubリポジトリの成果物（Git追跡ファイルおよびそのコミット履歴）を変更しない前提で実行する。
Pull Requestのレビュースレッドへの返信、コメントの投稿、スレッドの解決とDependabotアラートの却下はリポジトリ成果物の変更には該当せず、本書が定める範囲において監査担当エージェントが実施する。
監査処理は呼び出し時点の状態を一度読み取ることで完了し、新たなレビューの生成を要求したり、レビューの到着を待機したりする動作は監査の範囲外とする。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 監査担当の返却の`pending取得結果`を入力に使う時 | `../../../share/copilot-review-audit.subagent.md` | 判断 | `read:agent-toolkit:*/share/copilot-review-audit.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../external-write-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/external-write-review/SKILL.md` |

## 対象

状態（open、closed、merged）を問わずすべてのPull Requestからレビューを列挙し、判定対象として残っているCopilot由来のレビュー本文を取得する（インラインコメントやスレッドを伴わない本文のみのレビューもあるため、未解決スレッドの有無にかかわらずレビュー全体を列挙する）。
Copilot由来のインラインコメントおよびレビュースレッドは、未解決のレビュースレッドが存在するPull Requestからのみ取得する。要修正としてAWIへ記録した指摘のスレッドは未解決のまま維持されるため、次回以降の監査でも取得対象に含まれる。
Copilot由来であるかどうかの判定は、authorの`__typename`が`Bot`であり、かつlogin名に大文字小文字を区別せず`copilot`が含まれていることを条件とする。login名のみで判定すると、その文字列を含む人間のアカウントによる指摘に対して返信や解決をしてしまう恐れがあるためである。

## 取得

通常は`${CLAUDE_PLUGIN_ROOT}/share/copilot-review-audit.subagent.md`の`pending取得結果`を入力データとして使用する。`reviews`には未判定のCopilot由来レビューのPR番号とdatabaseId、`threads`には未解決のCopilot由来スレッドを持つPR番号が含まれる。`reviews`の各本文は後述のREST APIで取得し、`threads`に含まれる各PRに対してのみインラインコメントを取得する。

pendingのJSONを受け取れない環境では、以下のGraphQLクエリを用いて監査対象を直接取得する。レビューの列挙とレビュースレッドの取得はそれぞれ独立して行い、`pageInfo.hasNextPage`がfalseになるまで直前の`endCursor`を`-F cursor=<END_CURSOR>`として渡しながら繰り返し取得する（初回実行時は`cursor`を指定しない）。

すべてのPull Requestの番号、ならびに`reviews`および`reviewThreads`の先頭ページを取得するクエリ:

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -f query='query($owner:String!,$name:String!,$cursor:String){repository(owner:$owner,name:$name){pullRequests(first:100,after:$cursor,states:[OPEN,CLOSED,MERGED]){nodes{number reviews(first:20){nodes{databaseId author{__typename login}} pageInfo{hasNextPage}} reviewThreads(first:100){nodes{id isResolved comments(first:1){nodes{databaseId author{__typename login}}}} pageInfo{hasNextPage}}} pageInfo{hasNextPage endCursor}}}}'
```

`reviews`の`hasNextPage`がtrueであるPull Requestの追加ページを取得するクエリ:

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -F number=<PR> -f query='query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviews(first:100,after:$cursor){nodes{databaseId author{__typename login}} pageInfo{hasNextPage endCursor}}}}}'
```

`reviewThreads`の`hasNextPage`がtrueのPull Requestの追加ページを取得するクエリ（未解決スレッドの有無は末尾までページングして確定する。GraphQLではスレッドID、解決状態、RESTコメントと紐づくdatabaseId、先頭コメントのauthorのみ取得する）:

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -F number=<PR> -f query='query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{id isResolved comments(first:1){nodes{databaseId author{__typename login}}}} pageInfo{hasNextPage endCursor}}}}}'
```

未解決のレビュースレッドを持つPull Request（pendingのJSONにおける`threads`のPR番号）について、インラインコメントをすべてのページから取得する:

```sh
gh api --paginate 'repos/{owner}/{repo}/pulls/<PR>/comments?per_page=100'
```

各ページおよびREST APIの標準出力はmanaged-temp内の個別のJSONファイルへ保存し、終端への到達、取得件数、databaseIdは保存したファイルから確認する。監査の完了とみなす条件は、実行したすべてのREST APIが終了コード0で終了し、pendingコマンドの成功またはGraphQLのページネーション終端への到達を確認できた場合に限定する。

## 判定

GraphQLで代替取得した場合に限り、判定に入る前に`atk review-audit list --repo <OWNER>/<REPO>`を実行して判定済みレビュー本文のdatabaseIdを取得し、列挙されたCopilot由来のレビュー一覧から除外する。除外したdatabaseIdの一覧と件数は委譲元エージェントへ報告する。
判定対象となるレビュー本文は、以下のコマンドで1件ずつ取得し、レスポンスの`id`が予定していたdatabaseIdと一致することを確認する。列挙したCopilot由来レビューのID集合は、除外した集合と本文を取得した集合の2つに過不足なく分類する。

```sh
gh api 'repos/{owner}/{repo}/pulls/<PR>/reviews/<REVIEW_ID>' > <managed-tempの中のJSONファイル>
```

各指摘事項を現行の成果物、過去の採否判断、およびその根拠と比較し、「要修正」「是正済み」「根拠付き対応不要」のいずれかへ分類する。レビュー本文が全体の概要や進行状況のみを述べており具体的な対処を求めていない場合は、レビュー本文全体を「指摘なし」と判定する（個別の指摘分類とは別の枠組みとして扱う）。
「要修正」と判定した項目は指摘箇所と対処案を報告し、実際の是正やAWIへの記録の要否は委譲元が判断する。「是正済み」や「根拠付き対応不要」と判定した項目は、「判定結果のGitHubへの記録」の手順に従ってGitHub上へ記録する。すべてのレビュー本文とインラインコメントの所在、分類、処置内容と、インラインコメントの取得対象から外れたPull Request番号をまとめて報告する。

## 判定結果のGitHubへの記録

「是正済み」または「根拠付き対応不要」と分類した指摘に限り、監査担当エージェントがその分類と根拠を対象GitHubリポジトリへ書き込み、スレッドを解決済みに変更する。「要修正」のスレッドは未解決のまま維持し、「指摘なし」のレビュー本文には書き込みを行わない。判定の根拠は投稿する本文自体に記載し、`review-audit.json`は索引として活用する。
この書き込み操作は、操作内容、対象、範囲を明記した公開済みのユーザー発言および確認回答によって包括的に承認されており、監査ごとの個別確認は不要である。該当するユーザー発言は以下のとおりである。

```text
判断結果がどこにも残らないのはいまいちだね。PRのレビュー指摘に対して「コメントしてCloseする」みたいな操作は無いの？
```

返信やコメントの文面は一度ファイルへ保存し、投稿直前に`agent-toolkit:external-write-review`を起動して、レビュー結果を反映した適切な文面のみを投稿する。本文には対象指摘の識別子（レビュー本文ならdatabaseId、スレッドなら対象ファイルと該当行）、判定分類、および判定根拠（確認した現行成果物の位置、あるいは対応不要と判断した理由）を記載する。

未解決のレビュースレッドに対しては、返信が正常に完了したことを確認した上でスレッドを解決する。

```sh
gh api graphql -F threadId=<THREAD_ID> -F body=@<BODY_FILE> -f query='mutation($threadId:ID!,$body:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$threadId,body:$body}){comment{url}}}'
gh api graphql -F threadId=<THREAD_ID> -f reason=<RESOLUTION_REASON> -f query='mutation($threadId:ID!,$reason:PullRequestReviewThreadResolutionReason!){resolveReviewThread(input:{threadId:$threadId,resolutionReason:$reason}){thread{isResolved}}}'
```

`<RESOLUTION_REASON>`には、分類に応じて以下の値を指定する。読み取り用の型である`PullRequestReviewThread`は`resolutionReason`を返却しないため、後続の判定処理は解決状態と本テーブルの対応に基づいて行う。

| 分類 | `<RESOLUTION_REASON>` |
| --- | --- |
| 是正済み | `ADDRESSED` |
| 根拠付き対応不要 | `WONT_FIX` |

スレッドを伴わないレビュー本文に対しては、Pull Requestへ通常のコメントとして投稿する。

```sh
gh pr comment <PR> --repo <OWNER>/<REPO> --body-file <BODY_FILE>
```

記録完了とみなすのは書き込みが終了コード0で成功した指摘のみであり、非0で失敗した指摘は後述の「判定済みの記録」の対象からも除外する。

## 判定済みの記録

書き込みに成功したレビュー本文、および「指摘なし」と分類したレビュー本文のdatabaseIdを、`atk review-audit mark --repo <OWNER>/<REPO> <ID>...`を用いて記録する（今後の監査で同一の本文を重複して読み直さないようにするため）。
「要修正」と判定したレビュー本文は、関連するAWIが終了するまで判定対象として残す（修正完了後の次回以降の監査において「是正済み」としてGitHubへ記録するため）。記録対象とするのはレビュー本文のdatabaseIdのみであり、インラインコメントについてはスレッドの解決状態が同様の管理役割を果たす。
判定記録の読み書きは必ず`atk review-audit`を通じて行う（保存パスと形式はこのコマンドの仕様に準拠する）。記録先は対象リポジトリ外の状態ディレクトリであり、成果物を変更しない制約の対象外として扱う。成果物の変更に伴って再判定が必要となる指摘については、新たなレビューが別のdatabaseIdで届くため、既存の記録を削除しなくても自然に再判定の対象となる。

## Dependabotアラート

対象GitHubリポジトリにおいてopen状態にあるDependabotアラートのうち、まだ判定済みとして記録されていないものを判定し、すでに削除されたマニフェストファイルに紐づく誤検知アラートを却下（Dismiss）する。

### 取得と判定区分

通常は`atk review-audit pending`のJSONレスポンスに含まれる`dependabot`と`counts.dependabot`を入力として利用する。`dependabot.alerts`の各要素には、番号、`manifest_path`、パッケージ名、エコシステム、修正版（`first_patched_version`。存在しない場合はnull）、および`category`が含まれる。`category`は、対象マニフェストが`default_branch`に存在しない場合は`inaccurate`、存在する場合は`manifest_present`となる。なお、`dependabot.status`が`disabled`（機能が無効）または`unauthorized`（権限不足。`message`に応答本文が含まれる）の場合は判定せず、その状態をそのまま返す。

pendingのJSONを受け取れない環境では直接取得する。各レスポンスはmanaged-temp内のJSONファイルへ保存し、件数や値は保存したファイルから確認する。

1. `gh api --paginate --slurp 'repos/<OWNER>/<REPO>/dependabot/alerts?state=open&per_page=100'`を実行して全ページを取得する。HTTP 403のエラー本文が「Dependabot alerts are disabled for this repository.」であれば機能無効、それ以外の403であれば権限不足と判断し、判定しない
2. `atk review-audit list --repo <OWNER>/<REPO>`が返却する`dependabot:<番号>`を判定済みとして一覧から除外する
3. `gh api 'repos/<OWNER>/<REPO>'`の`default_branch`フィールドからデフォルトブランチ名を取得する
4. 残った各アラートの`dependency.manifest_path`について`gh api 'repos/<OWNER>/<REPO>/contents/<manifest_path>?ref=<branch名>'`を実行し、HTTP 404であれば不在、終了コード0であれば実在と判定する。それ以外のエラーが発生した場合は監査を未完了として扱う

ベースブランチとは、対象リポジトリ（起動時の作業ディレクトリ`cwd`）においてprocess-wiが処理対象としているブランチを指す。

| 区分 | 条件 | 処置 |
| --- | --- | --- |
| 誤検知 | `manifest_path`が`default_branch`で指定されたブランチに実在しない（`category`が`inaccurate`） | 後述の却下処理を行う |
| 是正済み | マニフェストは`default_branch`で指定されたブランチに実在するが、process-wiの実行対象であるベースブランチの同一マニフェストにおいて対象パッケージが修正版以上へ更新済みであるか、あるいはそのマニフェスト自体がベースブランチに存在しない | GitHub上へは書き込まない。`default_branch`で指定されたブランチへの反映に伴い、アラートが自動的に`fixed`へ遷移するのを待つ |
| 要修正 | 上記のいずれの条件にも該当しない。修正版が提供されていない場合もこの区分とする | アラート番号、マニフェスト、パッケージ、修正版、および対処案（依存関係の更新。修正版がない場合はその旨）を委譲元へ報告する |

是正済みかどうかの確認では、`git show <ベースbranch>:<manifest_path>`を実行してマニフェスト内のバージョンを修正版と比較し、比較できない場合は「要修正」として扱う。

### 却下

誤検知と判定したアラートは`state=dismissed`、`dismissed_reason=inaccurate`を指定して却下し、`dismissed_comment`（280文字以内）に`default_branch`のブランチに対象マニフェストが存在しないことを根拠として記載する。

```sh
gh api --method PATCH 'repos/<OWNER>/<REPO>/dependabot/alerts/<番号>' -f state=dismissed -f dismissed_reason=inaccurate -F dismissed_comment=@<COMMENT_FILE>
```

`dismissed_comment`は第三者が閲覧するため、文面をファイルへ保存した上で送信直前に`agent-toolkit:external-write-review`を起動し、レビュー結果を反映した適切な文面のみを送信する。コメント文面にはキュー項目の識別子を含めず、アラート却下の根拠のみを簡潔に記載する。非0で失敗した却下操作は判定済みの記録から除外して結果を報告する（次回の実行サイクルで再び取得される）。なお、誤って却下したアラートは`state=open`へ更新することで再オープンできる。

この却下操作は、公開済みのユーザー発言および確認回答によって承認されており、監査ごとの個別確認は不要である。承認範囲は`default_branch`のブランチに実在しないマニフェストに紐づくアラートを`inaccurate`として却下することに限定され、「是正済み」や「要修正」のアラートに対してGitHub上へ書き込みを行うことはない。該当するユーザー発言と確認回答は以下のとおりである。

```text
dotfiles向けDependabot alertsが出てる気がするんだけどこれって自動で対応されないんだっけ？ (対応して＆対応する仕掛け入れて）
```

<!-- agent-doc-tone: example-mask:start -->
```text
PRレビュー指摘を拾うスクリプトにそっちもチェックして拾う感じにまとめるのがいい気がする。
```
<!-- agent-doc-tone: example-mask:end -->

```text
回答: review-auditへ集約 (Recommended)
```

### 判定したアラートの記録

却下が成功したアラート、是正済みのアラート、および要修正として報告したアラートの番号を、`atk review-audit mark --repo <OWNER>/<REPO> dependabot:<番号>...`を用いて記録する。記録されたアラートは次回以降のpending一覧から除外され、`atk wi process-loop`が同一のアラートを理由にprocess-wiを再起動することを防止する。「要修正」のアラートも記録対象に含める理由は、修正内容が`default_branch`へ反映されればGitHub側で自動的にアラートが`fixed`状態となるため再判定が不要であり、記録から除外するとprocess-loopが同じアラートを検知して起動し続けてしまうためである。「要修正」と判定されたアラートについては、委譲元エージェントが同一セッション内で修正を完了するか、あるいは新たなAWIとして記録する。
