# 自動コードレビュー監査

作業`process-wi`のWI選定と並行して、GitHub CopilotのレビューとDependabotアラートを取得し、判定してGitHubへ記録し、必要な対応を確定する手順書である。
選定メインが未判定の件数を確かめて監査担当を起動・受領する（本書「自動コードレビュー監査担当の起動と受領」まで）。
監査担当は本書「対象」から「判定済みの記録」までと、`agent-toolkit:atk-mode/playbooks/process-wi-dependabot-audit.md`を行う。
監査担当は対象リポジトリの成果物（Git追跡ファイルとそのcommit履歴）を変更せずに読み取りだけで扱い、レビューthreadへの返信・コメントの投稿・threadの解決とDependabotアラートの却下は本書の範囲で行う。
監査は呼び出した時点の状態を一度読み取って終わり、新しいレビューの生成の要求や到着の待機は範囲の外とする。
系統を登録したリポジトリで監査する系統は`agent-toolkit:atk-mode/playbooks/process-wi.md`「系統を登録したリポジトリ」に従う。
終了条件は、選定メインが監査担当の返却を受領し、要修正の指摘とアラートを同じセッションで直すかAWIで後続へ回すかを確定した時点である。

1. WI選定の前に、対象リポジトリがGitHub上にあれば`atk review-audit pending --repo <OWNER>/<REPO>`を実行する。
   終了コード0で`counts.reviews`、`counts.threads`、`counts.dependabot`がいずれも0なら監査担当は起動せず、3つの件数を監査を省いた根拠として完了報告へ引き継ぐ。
   1件以上ある場合、終了コードが0以外の場合、JSONや件数を解釈できない場合は、本書「自動コードレビュー監査担当の起動と受領」で監査担当を起動し、選定と並行して実行する。
   `dependabot.status`が`disabled`か`unauthorized`なら、その状態を完了報告へ引き継ぐ。
   監査の処置と公開工程の開始の関係は`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`の冒頭にあり、新しく届いたレビューは次回の監査で扱う。
2. `atk wi process-loop`は、キューが空でも未判定のDependabotアラートがあれば、AWIを起票せずに`process-wi`を起動する。
   この場合の候補0件の選定結果は正常として保存し、レーンを起動せずに監査の対応を確定して、`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開対象が無い場合の短絡」を経て終える。
   修正が要る項目を同じセッションで直す場合は`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「即時対応の手順」に従う。

## 自動コードレビュー監査担当の起動と受領

```text
起動対象: copilot-review-audit.subagent.md
```

<!-- input-contract:copilot-review-audit:start -->
<!-- 生成元: skills/atk-mode/roles/copilot-review-audit.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->
入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。
役割固有の入力（`extra_params`）はこの契約の入力一覧で完結する。
通常起動（`mode: delegate`）の共通の準備: 起動の前に`agent-toolkit:atk-mode/references/work-contract.md`に従う全体作業契約を用意し、`extra_params`とは別の公開入力`whole_contract_path`へ絶対パスを渡す。

- 必須入力名: `pending取得結果`、`引き継ぎ記録先`

<!-- input-contract:copilot-review-audit:end -->

選定メインが監査を委譲する時に、本節で監査担当を起動して結果を受け取る。
監査担当の役割文書は`agent-toolkit:atk-mode/roles/copilot-review-audit.subagent.md`にあり（所在参照）、返却の項目は本書「監査担当の返却契約」に従う。

### 起動

1. `agents_server`の`start`を次の引数で呼び、監査担当を1件起動する。

   | 引数 | 値 |
   | --- | --- |
   | `cwd` | 対象リポジトリの絶対パス |
   | `subagent_md_path` | `copilot-review-audit` |
   | `extra_params` | 次の名前付き入力だけ |

   - `pending取得結果`: `atk review-audit pending`の出力の`保存先:`が示す、生成元のJSONファイルの絶対パス。
     エージェント環境では量によらずファイルへ保存されるため、件数とDependabotの取得状態を併記して保存先のパスをそのまま渡し、モデルがJSONを保存し直す手間を省く。
     コマンドが0以外で終わった場合と、JSONや件数を解釈できない場合は`なし`を渡す。
   - `引き継ぎ記録先`: 省略と継続の扱いは`agent-toolkit:atk-mode/references/delegation-prompt.md`「引き継ぎ記録先」に従う。

### 受領

1. 結果は引数なしの`atk agents wait`で受け取り、本書「監査担当の返却契約」の固定形式を満たすことを確かめる。
2. Dependabotの判定対象があれば、通常取得・代替取得とも`default_branch`と確認SHAを受け取り、取得・比較・markが同じSHAに基づくことを確かめる。
   - 判定対象が0件なら版の情報は要らず、判定欄の「なし」と取得状態を受領する。
     機能無効・権限不足の場合は、その状態と理由を受領する。
   - 却下は、APIの終了コード0、応答の`number`の一致、`state=dismissed`を確かめた番号だけを処置済みとする。
   - 反映待ち・要修正の確認版付きのmarkと、却下の失敗・未判定の未markを区別する。
     未markのアラートは単一・all-reposの既存の確認間隔で取得し直し、古い番号だけの無進捗の記録で抑止することは避ける。
   - 同じ版の判定済みの除外は`atk review-audit`が担い、GitHub APIの`default_branch`が指すブランチの更新後は同じ番号も監査し直す。
     WI部分の無進捗の抑止は保ち、未観測（None）を取得成功の0件と読み替えることは避ける。
3. 修正を要する指摘とDependabotアラートは、同じセッションで直すか、AWIに記録して後続へ回すかを確定する。
   機能の無効化や権限不足でアラートを取得できなかった状態は完了報告へ引き継ぐ。

<!-- return-contract:copilot-review-audit:start -->
<!-- 生成元: skills/atk-mode/roles/copilot-review-audit.subagent.md「出力」。手動編集しない。 -->
### 監査担当の返却契約

取得状態と処置結果を確かめ、監査が完了した場合は次の形式のみを返す。

```text
状態: completed
指摘の分類: <全Pull RequestのCopilot由来のreview本文と、未解決threadを持つPull RequestのCopilot由来のinline commentごとの所在、分類および処置>
要修正の指摘: <要修正と分類した指摘の所在と対処案。無い場合は「なし」>
GitHubへの書き込み: <返信、threadの解決およびコメント投稿の結果と、非0で終了した書き込み。無い場合は「なし」>
記録したreview本文: <`atk review-audit mark`で記録したreview本文のdatabaseId。無い場合は「なし」>
取得対象外のPull Request: <inline commentの取得対象へ入らなかったPull Request番号。無い場合は「なし」>
代替取得で除いた判定済み: <GraphQLで代替取得した場合に、判定済みとして除いたdatabaseIdの一覧と件数。代替取得しなかった場合は「なし」>
Dependabotアラートの判定: <番号、default_branch、確認SHA、manifest/package、解決済み版・修正版、判定区分、処置。API終了コードと応答number/state、未処置の理由と次回再取得を含む。無い場合は「なし」>
要修正のDependabotアラート: <番号、マニフェスト、パッケージ、修正版と対処案。無い場合は「なし」>
記録したDependabotアラート: <版付きmarkが成功した番号と確認SHA。却下失敗・未判定は含めない。無い場合は「なし」>
Dependabotアラートの取得状態: <取得できた場合は「取得済み」。機能無効または権限不足で取得できなかった場合はその状態と理由>
```

続行不能と追加報告行は、共通契約`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」に従う。
取得不能の理由には、失敗したコマンド、終了コードと取得できなかった範囲を記す。
<!-- return-contract:copilot-review-audit:end -->

## 対象

1. 状態（open、closed、merged）を問わず全てのPull Requestからレビューを列挙し、判定対象として残るCopilot由来のレビュー本文を取得する。
   インラインコメントやthreadを伴わない本文だけのレビューもあるため、未解決threadの有無にかかわらずレビュー全体を列挙する。
2. Copilot由来のインラインコメントとレビューthreadは、未解決のレビューthreadがあるPull Requestからだけ取得する。
   要修正としてAWIへ記録した指摘のthreadは未解決のまま残るため、次回以降の監査でも取得対象に入る。
3. Copilot由来かどうかは、authorの`__typename`が`Bot`で、かつlogin名に大文字小文字を区別せず`copilot`を含むことで判定する。
   login名だけで判定すると、その文字列を含む人間のアカウントの指摘へ返信や解決をしてしまうためである。

## 取得

1. 通常は、起動入力`pending取得結果`（`atk review-audit pending`の標準出力を保存したJSON）を使う。
   `reviews`には未判定のCopilot由来レビューのPR番号とdatabaseId、`threads`には未解決のCopilot由来threadを持つPR番号が入る。
   `reviews`の各本文は本書「判定」のREST APIで取得し、インラインコメントは`threads`の各PRに対してだけ取得する。
2. pendingのJSONを受け取れない環境では、次のGraphQLクエリで監査対象を直接取得する。
   レビューの列挙とレビューthreadの取得は別々に行い、`pageInfo.hasNextPage`がfalseになるまで直前の`endCursor`を`-F cursor=<END_CURSOR>`で渡して繰り返す（初回は`cursor`を指定しない）。

   全てのPull Requestの番号と、`reviews`・`reviewThreads`の先頭ページを取得するクエリ:

   ```sh
   gh api graphql -F owner=<OWNER> -F name=<REPO> -f query='query($owner:String!,$name:String!,$cursor:String){repository(owner:$owner,name:$name){pullRequests(first:100,after:$cursor,states:[OPEN,CLOSED,MERGED]){nodes{number reviews(first:20){nodes{databaseId author{__typename login}} pageInfo{hasNextPage}} reviewThreads(first:100){nodes{id isResolved comments(first:1){nodes{databaseId author{__typename login}}}} pageInfo{hasNextPage}}} pageInfo{hasNextPage endCursor}}}}'
   ```

   `reviews`の`hasNextPage`がtrueのPull Requestの追加ページを取得するクエリ:

   ```sh
   gh api graphql -F owner=<OWNER> -F name=<REPO> -F number=<PR> -f query='query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviews(first:100,after:$cursor){nodes{databaseId author{__typename login}} pageInfo{hasNextPage endCursor}}}}}'
   ```

   `reviewThreads`の`hasNextPage`がtrueのPull Requestの追加ページは、次のクエリで取得する。
   未解決threadの有無は末尾までページングして確定し、GraphQLではthreadのID、解決状態、RESTのコメントと結び付くdatabaseId、先頭コメントのauthorだけを取得する。

   ```sh
   gh api graphql -F owner=<OWNER> -F name=<REPO> -F number=<PR> -f query='query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{id isResolved comments(first:1){nodes{databaseId author{__typename login}}}} pageInfo{hasNextPage endCursor}}}}}'
   ```

   各クエリの出力は手順4のとおり保存する。

3. 未解決のレビューthreadを持つPull Request（pendingのJSONの`threads`のPR番号）は、インラインコメントを全てのページから取得する。

   ```sh
   gh api --paginate 'repos/{owner}/{repo}/pulls/<PR>/comments?per_page=100'
   ```

   取得した各ページも手順4のとおり保存する。

4. 各ページとREST APIの標準出力はmanaged-tempの中の個別のJSONファイルへ保存し、終端への到達、取得件数、databaseIdは保存したファイルから確かめる。
   監査の完了とするのは、実行した全てのREST APIが終了コード0で終わり、pendingコマンドの成功かGraphQLのページングの終端への到達を確かめられた場合に限る。

## 判定

1. GraphQLで代替取得した場合に限り、判定の前に`atk review-audit list --repo <OWNER>/<REPO>`で判定済みのレビュー本文のdatabaseIdを取得し、列挙したCopilot由来のレビューから除く。
   除いたdatabaseIdの一覧と件数は委譲元へ報告する。
2. 判定対象のレビュー本文は次のコマンドで1件ずつ取得し、応答の`id`が予定したdatabaseIdと一致することを確かめる。
   列挙したCopilot由来レビューのIDの集合は、除いた集合と本文を取得した集合の2つに過不足なく分ける。

   ```sh
   gh api 'repos/{owner}/{repo}/pulls/<PR>/reviews/<REVIEW_ID>' > <managed-tempの中のJSONファイル>
   ```

   保存したJSONで`id`の一致を確かめてから手順3へ進む。

3. 各指摘を現行の成果物、過去の採否の判断とその根拠と比べ、「要修正」「是正済み」「根拠付き対応不要」のいずれかに分ける。
   レビュー本文が全体の概要や進行状況だけを述べて具体的な対処を求めていない場合は、レビュー本文全体を「指摘なし」とする（個別の指摘の分類とは別の枠組み）。
4. 「要修正」の項目は指摘の箇所と対処案を報告し、是正やAWIへの記録の要否は委譲元が判断する。
   「是正済み」と「根拠付き対応不要」の項目は本書「判定結果のGitHubへの記録」でGitHubへ記録する。
   全てのレビュー本文とインラインコメントの所在、分類、処置と、インラインコメントの取得対象から外れたPull Request番号をまとめて報告する。

## 判定結果のGitHubへの記録

1. 「是正済み」か「根拠付き対応不要」に分けた指摘に限り、監査担当がその分類と根拠を対象リポジトリへ書き込み、threadを解決済みにする。
   「要修正」のthreadは未解決のまま残し、「指摘なし」のレビュー本文へは書き込みを避ける。
   判定の根拠は投稿する本文に書き、`review-audit.json`は索引として使う。
2. この書き込みは、操作の内容・対象・範囲を明記した公開済みのユーザーの発言と確認回答で包括的に承認されている。
   該当するユーザーの発言は次のとおりである。

   ```text
   判断結果がどこにも残らないのはいまいちだね。PRのレビュー指摘に対して「コメントしてCloseする」みたいな操作は無いの？
   ```

   そのため、監査ごとの個別の確認は省く。

3. 返信やコメントの文面はいったんファイルへ保存し、投稿の直前に`agent-toolkit:atk-mode/playbooks/review-external-writes.md`を読んで、レビュー結果を反映した文面だけを投稿する。
   本文には、対象の指摘の識別子（レビュー本文ならdatabaseId、threadなら対象ファイルと該当行）、判定の分類、判定の根拠（確かめた現行の成果物の位置か、対応不要と判断した理由）を書く。
4. 未解決のレビューthreadは、返信の成功を確かめてからthreadを解決する。

   ```sh
   gh api graphql -F threadId=<THREAD_ID> -F body=@<BODY_FILE> -f query='mutation($threadId:ID!,$body:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$threadId,body:$body}){comment{url}}}'
   gh api graphql -F threadId=<THREAD_ID> -f reason=<RESOLUTION_REASON> -f query='mutation($threadId:ID!,$reason:PullRequestReviewThreadResolutionReason!){resolveReviewThread(input:{threadId:$threadId,resolutionReason:$reason}){thread{isResolved}}}'
   ```

   `<RESOLUTION_REASON>`は分類に応じて次の値にする。
   読み取り用の型`PullRequestReviewThread`は`resolutionReason`を返さないため、後続の判定は解決状態とこの表の対応で行う。

   | 分類 | `<RESOLUTION_REASON>` |
   | --- | --- |
   | 是正済み | `ADDRESSED` |
   | 根拠付き対応不要 | `WONT_FIX` |

   返信かthreadの解決が0以外で終わった指摘は、手順6のとおり記録の完了から除く。

5. threadを伴わないレビュー本文は、Pull Requestへ通常のコメントとして投稿する。

   ```sh
   gh pr comment <PR> --repo <OWNER>/<REPO> --body-file <BODY_FILE>
   ```

   投稿の成否は手順6で判定する。

6. 記録の完了とするのは書き込みが終了コード0で成功した指摘だけであり、0以外で失敗した指摘は本書「判定済みの記録」の対象からも除く。

## 判定済みの記録

1. 書き込みに成功したレビュー本文と「指摘なし」と分けたレビュー本文のdatabaseIdを、`atk review-audit mark --repo <OWNER>/<REPO> <ID>...`で記録する（今後の監査で同じ本文を読み直さないため）。
2. 「要修正」のレビュー本文は、関連するAWIが終わるまで判定対象に残す（修正後の監査で「是正済み」としてGitHubへ記録するため）。
   記録するのはレビュー本文のdatabaseIdだけであり、インラインコメントはthreadの解決状態が同じ役割を果たす。
3. 判定の記録の読み書きは必ず`atk review-audit`を通す（保存のパスと形式はこのコマンドの仕様に従う）。
   記録先は対象リポジトリの外の状態ディレクトリであり、成果物を変更しない制約の対象の外にある。
   成果物の変更で再判定が要る指摘は、新しいレビューが別のdatabaseIdで届くため、既存の記録を消さずに再判定の対象になる。
4. Copilotのレビューの監査を終えたら、同じ実行で`agent-toolkit:atk-mode/playbooks/process-wi-dependabot-audit.md`「Dependabotアラート」へ進む。

返す報告に含める項目: 監査担当は本書「監査担当の返却契約」の形式で返す。
選定メインは、監査を省いた根拠の3つの件数か受領した返却と、要修正の項目の処置（今回の修正かAWI）を完了報告へ含める。
