# 自動コードレビュー監査

`agent-toolkit:process-wi`の自動コードレビュー監査は、担当するエージェントが対象GitHubリポジトリの成果物（追跡ファイルとその履歴）を変更せずに行う。
Pull Requestのreview threadへの返信、コメント投稿、threadの解決、Dependabotアラートの却下は成果物の変更に当たらず、本書が定める範囲で監査担当が行う。
監査は呼び出し時点の状態を1回読んで完了し、新しいレビューの生成要求や到着の待機は監査の範囲外とする。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 監査担当の返却の`pending取得結果`を入力に使う時 | `../../../share/copilot-review-audit.subagent.md` | 判断 | `read:agent-toolkit:*/share/copilot-review-audit.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../external-write-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/external-write-review/SKILL.md` |

## 対象

状態（open・closed・merged）を問わず全Pull Requestのreviewを列挙し、判定対象に残るCopilot由来のreview本文を取得する（inline commentもthreadも伴わない本文だけのreviewがあるため、列挙は未解決threadの有無によらず行う）。
Copilot由来のinline commentとreview threadは、未解決のreview threadを持つPull Requestだけから取得する。要修正としてAWIへ記録した指摘のthreadは未解決のまま残り、以降も対象に入る。
Copilot由来とするのは、authorの`__typename`が`Bot`で、かつloginが大文字小文字を区別せず`copilot`を含む場合とする。login名だけで判定すると、その文字列を含む人間のアカウントの指摘へ返信と解決を書き込む。

## 取得

通常は`${CLAUDE_PLUGIN_ROOT}/share/copilot-review-audit.subagent.md`の`pending取得結果`を入力とする。`reviews`には未判定のCopilot由来reviewのPR番号とdatabaseId、`threads`には未解決のCopilot由来threadを持つPR番号が入る。`reviews`の各本文を後掲のREST APIで取得し、`threads`の各PRだけinline commentを取得する。

pendingのJSONを受け取れない場合は、次のGraphQLクエリーで監査対象を直接取得する。reviewの列挙とreview threadの取得はそれぞれ独立に、`pageInfo.hasNextPage`が偽になるまで直前の`endCursor`を`-F cursor=<END_CURSOR>`で渡して取得する（初回は`cursor`を渡さない）。

全Pull Requestの番号と`reviews`・`reviewThreads`の先頭ページ:

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -f query='query($owner:String!,$name:String!,$cursor:String){repository(owner:$owner,name:$name){pullRequests(first:100,after:$cursor,states:[OPEN,CLOSED,MERGED]){nodes{number reviews(first:20){nodes{databaseId author{__typename login}} pageInfo{hasNextPage}} reviewThreads(first:100){nodes{id isResolved comments(first:1){nodes{databaseId author{__typename login}}}} pageInfo{hasNextPage}}} pageInfo{hasNextPage endCursor}}}}'
```

`reviews`の`hasNextPage`が真のPull Request:

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -F number=<PR> -f query='query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviews(first:100,after:$cursor){nodes{databaseId author{__typename login}} pageInfo{hasNextPage endCursor}}}}}'
```

`reviewThreads`の`hasNextPage`が真のPull Request（未解決threadの有無は終端まで取得して確定する。GraphQLではthread ID、解決状態、REST commentと対応付けるdatabaseId、先頭commentのauthorだけを得る）:

```sh
gh api graphql -F owner=<OWNER> -F name=<REPO> -F number=<PR> -f query='query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{id isResolved comments(first:1){nodes{databaseId author{__typename login}}}} pageInfo{hasNextPage endCursor}}}}}'
```

未解決のreview threadを持つPull Request（pendingのJSONでは`threads`のPR番号）について、inline commentを全ページ取得する。

```sh
gh api --paginate 'repos/{owner}/{repo}/pulls/<PR>/comments?per_page=100'
```

各ページとREST APIの標準出力はmanaged-temp内の別々のJSONファイルへ保存し、終端、件数、databaseIdは保存したファイルから確かめる。監査の完了は、実行した全REST APIが終了コード0で、pendingコマンドの成功かGraphQLのpagination終端を確かめられた場合に限る。

## 判定

GraphQLで代替した場合だけ、判定前に`atk review-audit list --repo <OWNER>/<REPO>`で判定済みreview本文のdatabaseIdを取得し、列挙したCopilot由来のreviewから除く。除いたdatabaseIdの一覧と件数を委譲元へ返す。
判定対象のreview本文は1件ずつ次で取得し、応答の`id`が予定のdatabaseIdと一致することを確かめる。列挙したCopilot由来reviewのID集合は、除外した集合と本文を取得した集合へ過不足なく分ける。

```sh
gh api 'repos/{owner}/{repo}/pulls/<PR>/reviews/<REVIEW_ID>' > <managed-tempの中のJSONファイル>
```

各指摘を現行成果物、過去の採否、根拠と比べ、要修正、是正済み、根拠付き対応不要のいずれかへ分類する。review本文が概要と進行状況だけで処置を求める記述を含まない場合は、本文全体を指摘なしとする（個々の指摘の分類とは別の単位）。
要修正は所在と対処案を返し、是正とAWIへの記録は委譲元が決める。是正済みと根拠付き対応不要は「判定結果のGitHubへの記録」に従ってGitHubへ残す。全review本文とinline commentの所在、分類、処置と、inline commentの取得対象へ入らなかったPull Request番号を返す。

## 判定結果のGitHubへの記録

是正済みまたは根拠付き対応不要と分類した指摘だけ、監査担当が分類と根拠を対象GitHubリポジトリへ書き込み、threadを解決する。要修正のthreadは未解決のまま残し、指摘なしのreview本文には書き込まない。判定根拠は投稿する本文が持ち、`review-audit.json`は索引として扱う。
この書き込みは、操作、対象、範囲を明示した公開済みのユーザー発言と確認回答で承認済みであり、監査ごとの確認は不要である。ユーザー発言は次のとおりである。

```text
判断結果がどこにも残らないのはいまいちだね。PRのレビュー指摘に対して「コメントしてCloseする」みたいな操作は無いの？
```

返信とコメントは文面をファイルへ保存し、投稿の直前に`agent-toolkit:external-write-review`を起動して、レビュー結果を反映した文面だけを投稿する。本文には対象の指摘の識別子（review本文はdatabaseId、threadは対象ファイルと行）、分類、根拠（確認した現行成果物の位置か対応不要の理由）を書く。

未解決のreview threadでは、返信の成功を確かめてから解決する。

```sh
gh api graphql -F threadId=<THREAD_ID> -F body=@<BODY_FILE> -f query='mutation($threadId:ID!,$body:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$threadId,body:$body}){comment{url}}}'
gh api graphql -F threadId=<THREAD_ID> -f reason=<RESOLUTION_REASON> -f query='mutation($threadId:ID!,$reason:PullRequestReviewThreadResolutionReason!){resolveReviewThread(input:{threadId:$threadId,resolutionReason:$reason}){thread{isResolved}}}'
```

`<RESOLUTION_REASON>`は分類ごとに次の値とする。読み取り型`PullRequestReviewThread`は`resolutionReason`を返さないため、後続の判定は解決状態と本表から導く。

| 分類 | `<RESOLUTION_REASON>` |
| --- | --- |
| 是正済み | `ADDRESSED` |
| 根拠付き対応不要 | `WONT_FIX` |

threadを伴わないreview本文では、Pull Requestへコメントとして投稿する。

```sh
gh pr comment <PR> --repo <OWNER>/<REPO> --body-file <BODY_FILE>
```

記録済みとして扱うのは書き込みが終了コード0で終わった指摘に限り、非0の指摘は「判定済みの記録」からも外す。

## 判定済みの記録

書き込みが成功したreview本文と、指摘なしと分類したreview本文のdatabaseIdを、`atk review-audit mark --repo <OWNER>/<REPO> <ID>...`で記録する（以降の監査で同じ本文を読み直さないため）。
要修正のreview本文は、そのAWIが終端するまで判定対象に残す（是正後の監査で是正済みとしてGitHubへ記録するため）。記録するのはreview本文のdatabaseIdに限り、inline commentはthreadの解決状態が同じ役割を果たす。
記録の読み書きは`atk review-audit`だけで行う（パスと保存形式はこのコマンドが定める）。記録先は対象リポジトリの外の状態ディレクトリであり、成果物を変更しない制約の対象外とする。成果物の変更で再判定が要る指摘は、新しいreviewが別のdatabaseIdで届くため、記録を無効化しなくても再判定される。

## Dependabotアラート

対象GitHubリポジトリでopenのDependabotアラートのうち判定済みとして記録されていないものを判定し、削除済みマニフェストに紐づく誤検知を却下する。

### 取得と判定区分

通常は`atk review-audit pending`のJSONの`dependabot`と`counts.dependabot`を入力とする。`dependabot.alerts`の各要素は番号、`manifest_path`、パッケージ、エコシステム、修正版（`first_patched_version`。無ければnull）、`category`を持つ。`category`はマニフェストが`default_branch`のbranchに無ければ`inaccurate`、あれば`manifest_present`である。`dependabot.status`が`disabled`（機能が無効）か`unauthorized`（権限不足。`message`に応答本文）なら判定せず、その状態を返す。

pendingのJSONを受け取れない場合は直接取得する。各応答はmanaged-temp内のJSONファイルへ保存し、件数と値は保存したファイルから確かめる。

1. `gh api --paginate --slurp 'repos/<OWNER>/<REPO>/dependabot/alerts?state=open&per_page=100'`で全ページを取得する。HTTP 403の本文が「Dependabot alerts are disabled for this repository.」なら機能無効、それ以外の403なら権限不足として判定しない
2. `atk review-audit list --repo <OWNER>/<REPO>`が返す`dependabot:<番号>`を判定済みとして除く
3. `gh api 'repos/<OWNER>/<REPO>'`の`default_branch`からbranch名を得る
4. 残る各アラートの`dependency.manifest_path`について`gh api 'repos/<OWNER>/<REPO>/contents/<manifest_path>?ref=<branch名>'`を実行し、HTTP 404なら不在、終了コード0なら実在とする。それ以外の失敗は監査を未完了にする

ベースbranchは、対象リポジトリ（起動時の`cwd`）でprocess-wiが処理対象としているbranchを指す。

| 区分 | 条件 | 処置 |
| --- | --- | --- |
| 誤検知 | `manifest_path`が`default_branch`で指定されたbranchに実在しない（`category`が`inaccurate`） | 後掲の却下を行う |
| 是正済み | マニフェストは`default_branch`で指定されたbranchに実在するが、process-wiの実行のベースbranchの同じマニフェストが対象パッケージを修正版以上へ更新済みか、そのマニフェストがベースbranchに無い | GitHubへは書き込まない。`default_branch`で指定されたbranchへの反映でアラートが`fixed`になるのを待つ |
| 要修正 | 上記のいずれにも当たらない。修正版が無い場合もこの区分とする | 番号、マニフェスト、パッケージ、修正版、対処案（依存更新。修正版が無い場合はその旨）を委譲元へ返す |

是正済みの判定では`git show <ベースbranch>:<manifest_path>`で版を修正版と比べ、比べられなければ要修正とする。

### 却下

誤検知のアラートは`state=dismissed`、`dismissed_reason=inaccurate`で却下し、`dismissed_comment`（280文字以内）へ`default_branch`のbranchに該当マニフェストが無いことを根拠として書く。

```sh
gh api --method PATCH 'repos/<OWNER>/<REPO>/dependabot/alerts/<番号>' -f state=dismissed -f dismissed_reason=inaccurate -F dismissed_comment=@<COMMENT_FILE>
```

`dismissed_comment`は第三者が読むため、文面を保存して送信の直前に`agent-toolkit:external-write-review`を起動し、反映した文面だけを送る。文面にはキュー項目の識別子を含めず、アラートの根拠だけを書く。非0で終わった却下は判定済みの記録から外して結果を返す（次の実行で再び取得される）。誤って却下したアラートは`state=open`で戻せる。

この却下は公開済みのユーザー発言と確認回答で承認済みであり、監査ごとの確認は不要である。承認範囲は`default_branch`のbranchに実在しないマニフェストに紐づくアラートを`inaccurate`で却下することに限り、是正済みと要修正はGitHubへ書き込まない。ユーザー発言と確認回答は次のとおりである。

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

却下が成功したアラート、是正済みのアラート、要修正として返したアラートの番号を`atk review-audit mark --repo <OWNER>/<REPO> dependabot:<番号>...`で記録する。記録したアラートは以降のpendingから除かれ、`atk wi process-loop`も同じアラートでprocess-wiを起動しなくなる。要修正も記録するのは、是正が`default_branch`へ反映されればGitHubがアラートを`fixed`にするため再判定が要らず、記録しないとprocess-loopが同じアラートで起動し続けるためである。要修正のアラートは委譲元が同じセッションで是正するかAWIへ記録する。
