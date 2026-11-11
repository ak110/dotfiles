# 公開工程とセッション終了

公開工程は、選定時に確定したすべてのレーンが完了し、自動コードレビュー監査を起動した場合はその返却結果の受け取りと処置の確定を終えた時点で開始する。
レーンの完了とは、通常レーンではfast-forward（ff）マージ、`adopt`、および計画の保存が完了している状態を指す。
固有の指示によって`adopt`を延期したレーンや、`上流投入`が`混在`となっているレーンでは、ffマージと計画の保存が完了している状態（`混在`の場合は元項目を依存関係付きで`inbox`へ戻した状態）を指す。
各レーンのリソースは統合状態を記録した上で、セッション終了まで保持する。
公開工程の開始前に、選定メインが`immediate-fix.md`「開始後に追加されたWIの確認」を行う。
今回追加と判定した項目があれば、追加分の必要な工程を完了してから公開へ進む。
ready一覧は選定時点の情報を使用し、新着確認で今回追加とした項目は後述の合流手順で扱う。
公開対象は選定で確定した項目と、`selection-procedure-addition.md`「処理対象WIの追加」に従って合流した項目とする。
目標の明示がある場合の必要作業は`lanes-goals.md`「明示された目標からの実行」に従い、それ以外の新規項目は次回へ残す。

`agent-toolkit:process-wi all-repos`から委譲されたリポジトリ担当も、自身の対象ディレクトリ（cwd）、処理対象WI、およびベースブランチに対して公開工程を適用する。
担当ごとの処理所有権は公開、CI、確認、再開まで維持し、成功根拠には自身が担当するリポジトリの成果と公開結果だけを用いる。
リポジトリ担当は公開結果、対象リポジトリ、担当セッション、公開コミット、CI結果、未完了工程、再開記録を統括へ返す。
作業完了報告と報告用UWIは統括が集約して1件を届ける。
report-completionと振り返りは委譲先の終了工程の対象外とする。
ある担当が回答待ちや続行不能になった場合も、独立した他の担当は公開工程を続ける。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 合流した公開対象を判断する前 | `selection-procedure-addition.md` |
| 目標起点の公開対象を判断する前 | `lanes-goals.md` |
| 未達条件の延期とユーザー確認・冷却を選ぶ前 | `../../wi-standards/references/states-and-dependencies-dependencies.md` |
| 基準資料を選択・取得する時 | `../../process-wi/references/all-repos.md` |
| 本文の参照基準を適用する前 | `../../user-confirmation-and-report/SKILL.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:commit/references/push-and-ci.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:/share/session-termination.parent.md` |
| 基準資料を選択・取得する時 | `../../commit/references/history-rewrite-wi-records.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:commit/references/publish.md` |
| 基準資料を選択・取得する時 | `../../../share/add-wi.parent.md` |
| 基準資料を選択・取得する時 | `../../review-session/SKILL.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:report-completion/SKILL.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:delegation/references/waiting-claude-code.md` |
| 基準資料を選択・取得する時 | `../../wi-standards/references/awi-body.md` |
| 基準資料を選択・取得する時 | `run-lanes-completion.md` |
| 統合後の即時修正の担当と時機を決める前 | `agent-toolkit:process-wi/references/immediate-fix.md` |
| 公開後にも独立して成立するWI投入を別実行へ引き継ぐ前 | `../../../share/wi-submission.md` |
| 基準資料を選択・取得する時 | `../../user-confirmation-and-report/references/communication-methods-delivery.md` |
| 基準資料を選択・取得する時 | `../../wi-standards/SKILL.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:wi-standards/references/uwi-format.md` |
| 基準資料を選択・取得する時 | `termination-ci-failure.md` |
| 基準資料を選択・取得する時 | `../../commit/references/push-and-ci.md` |
| 基準資料を選択・取得する時 | `../../report-completion/SKILL.md` |
| 基準資料を選択・取得する時 | `../../wi-standards/references/wi-standards-operations.md` |
| 基準資料を選択・取得する時 | `run-lanes-integration.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 公開対象が無い場合の短絡・公開担当の起動と並行工程に関する判断・作業の前 | `finish-session-publishing.md` |
| 延期adoptと反映後の観測に関する判断・作業の前 | `finish-session-observation.md` |
| 公開工程の続行不能の回復と再開・公開状態の検収と再起動に関する判断・作業の前 | `finish-session-recovery.md` |
| 完了報告と終了に関する判断・作業の前 | `finish-session-completion.md` |
