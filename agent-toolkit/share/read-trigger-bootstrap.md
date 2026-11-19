# 操作を契機とする共有資料

操作に伴って共通で起動するスキルの宣言を本表にまとめる。
各スキル固有の追加資料は、それぞれのスキル内の読込表で宣言する。
操作の対象と副作用から必要なスキルを判断し、まだ起動していなければ操作の前に起動する（努力目標。契約の確認不足と独自手順の重複を避ける）。
資料の範囲は`agent-basics:writing-standards/references/read-method.md`に従って選び、hookの案内の有無によらず判断前に取得する。
配送操作の語彙と記法は、`read-trigger-contract-declarations.md`「表と取得方法」の定義に従う。
フックの案内は、現在の役割・選択資料に関係する条件を確かめる補助とし、資料取得中の依存候補は次の関連操作まで保留する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 操作で起動する共有スキル

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 委譲操作の前 | `../skills/delegation/SKILL.md` | 操作 | `delegation` |
| 検索の前 | `agent-basics:search/SKILL.md` | 操作 | `search` |
| 原因分析の記述の前 | `agent-basics:bugfix/SKILL.md` | 操作 | `root-cause-writing` |
| 個別の一時領域の作成の前 | `../skills/atk-mode/playbooks/managed-temp.md` | 操作 | `managed-temp-create` |
| 本文の作成・更新の前 | `agent-basics:writing-standards/SKILL.md` | 操作 | `body-writing` |
| 検証の実行の前 | `agent-basics:check-execution/SKILL.md`、`../skills/atk-mode/playbooks/verification.md` | 操作 | `verification` |
| 要件・技術判断を分類し、確認要否を判定する前 | `../skills/atk-mode/playbooks/confirmation.md` | | |
| 確認本文と選択肢を起草する前 | `../skills/atk-mode/playbooks/confirmation.md`、`../skills/atk-mode/references/choice-construction.md` | 判断 | `confirmation` |
| 期待からの逸脱を認識し、診断・訂正・対処を判断する前 | `agent-basics:bugfix/SKILL.md` | 判断 | `skill:agent-basics:bugfix` |
| 原因分析の判断前と記述前 | `agent-basics:bugfix/SKILL.md` | 判断 | `skill:agent-basics:bugfix`、`root-cause-writing` |
| レビュー指摘管理表を作成・更新する前 | `review-table-operations.md` | 判断 | `skill:agent-basics:review-standards` |
| エージェント向け文書の編集前 | `agent-basics:writing-standards/SKILL.md`、`agent-toolkit:/share/agent-documents-operations.md` | 操作 | `agent-document-writing` |

## WIの起草と保存で読む資料

WIの原稿を書く操作と保存する操作の前に読む手順書と形式の定義を、共通の宣言としてここにまとめる。
報告用UWIの資料は本書「計画・commit・公開・完了報告で読む資料」が宣言する。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 報告用UWIを除くWI作成の調査に入る前 | `../skills/atk-mode/playbooks/wi-drafting.md` | 判断 | `read:agent-toolkit:*/skills/atk-mode/playbooks/wi-drafting.md`、`bash:atk wi submission begin` |
| 通常AWIの原稿を書く前 | `../skills/atk-mode/playbooks/wi-drafting.md`、`../skills/atk-mode/references/awi-body.md` | 操作 | `awi-draft-writing` |
| UWIの原稿を書く前 | `../skills/atk-mode/references/uwi-format.md` | 操作 | `uwi-draft-writing` |
| UWIの保存・是正で確認の要否を判定する前と、run-skillのUWIを保存・取得する起動単位を判定する前 | `../skills/atk-mode/playbooks/confirmation.md`、`../skills/atk-mode/references/origin-and-approval.md` | 判断 | `uwi-draft-writing`、`wi-standard-commit`、`read:agent-toolkit:*/skills/atk-mode/playbooks/confirmation.md`、`read:agent-toolkit:*/skills/atk-mode/references/origin-and-approval.md` |
| 報告用UWIを除くWIの原稿を検証・保存する前 | `../skills/atk-mode/playbooks/wi-drafting.md`、`../skills/atk-mode/playbooks/wi-saving.md` | 判断 | `wi-standard-dry-run`、`wi-standard-commit`、`read:agent-toolkit:*/skills/atk-mode/playbooks/wi-drafting.md`、`read:agent-toolkit:*/skills/atk-mode/playbooks/wi-saving.md` |
| 報告用UWIを除くWIを保存し、保存先・状態・重要フラグを決める前 | `../skills/atk-mode/references/states-and-dependencies.md` | 判断 | `wi-standard-commit`、`read:agent-toolkit:*/skills/atk-mode/references/states-and-dependencies.md` |
| 報告用UWIを除くWIの意味を確認する前 | `../skills/atk-mode/references/draft-semantic-review.md` | 判断 | `wi-standard-self-review`、`read:agent-toolkit:*/skills/atk-mode/references/draft-semantic-review.md` |
| 通常AWIの投入と投入済みAWIの内容の更新を、WI投入担当へ委譲するか自分で起草するかを決める前と、WI投入担当を起動する前 | `../skills/atk-mode/roles/add-wi.parent.md` | 判断 | `delegation`、`read:agent-toolkit:*/skills/atk-mode/roles/add-wi.parent.md` |
| 独立した方針検収または本文検収を依頼する前 | `../skills/atk-mode/roles/wi-draft-review.parent.md` | 判断 | `delegation`、`delegation-result`、`read:agent-toolkit:*/skills/atk-mode/roles/wi-draft-review.parent.md` |
| AWI・計画の原因分析を記録する前 | `../skills/atk-mode/references/analysis-records.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md`、`read:agent-toolkit:*/skills/atk-mode/references/analysis-records.md` |

対象リポジトリのプロジェクトスキルをツールで起動できない場合の取得は、次の行で宣言する。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| プロジェクトスキルを起動できず、確定したSKILL.mdの実在する絶対パスを取得操作へ渡す時 | 呼出側が確定したSKILL.mdと、その宣言が指す起動時・条件付きの必読資料 | 操作 | `read:*/.claude/skills/*/SKILL.md`、`read:*/.agents/skills/*/SKILL.md` | `project-skill` |

## 計画・commit・公開・完了報告で読む資料

計画の初版を書く操作と保存する操作、push、作業完了報告と報告用UWIの保存の前に読む手順書と形式の定義を、共通の宣言としてここにまとめる。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 計画ファイルの初版を書く前 | `../skills/atk-mode/references/plan-format.md`、`../skills/atk-mode/references/awi-body.md` | 操作 | `plan-draft-writing` | |
| 計画バンドルを`atk plans commit`で保存する前 | `../skills/atk-mode/references/plan-storage.md` | 操作 | `bash:atk plans commit` | |
| 実際にpushする前 | `../skills/atk-mode/playbooks/commit.md`、`../skills/atk-mode/playbooks/publish.md` | 操作 | `bash:git push` | |
| 作業完了報告の手順書を読む時と、作業完了報告の到達水準を判定する前 | 起動のwhole_contract_pathまたはその正式な解決結果、メインでは自身のmanaged-temp直下にある現行の作業契約 | 操作 | `read:agent-toolkit:*/skills/atk-mode/playbooks/report-completion.md` | `input:作業契約` |
| 報告用UWIを書く前 | `../skills/atk-mode/references/report-formats.md`、`../skills/atk-mode/references/uwi-format.md`、`../skills/atk-mode/playbooks/wi-saving.md` | 判断 | `read:agent-toolkit:*/skills/atk-mode/references/report-formats.md`、`read:agent-toolkit:*/skills/atk-mode/references/uwi-format.md`、`read:agent-toolkit:*/skills/atk-mode/playbooks/wi-saving.md`、`wi-report-dry-run`、`wi-report-commit` | |

## 振り返りで読む資料

振り返りの準備（`atk run-script session-review-prepare`）の後に、対象リポジトリ固有の参照文書を届ける宣言である。
準備の記録が無い会話では対象が空になる。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 工程2で`atk run-script session-review-prepare`の出力に含まれる`reference_document`に値が設定されている場合 | `reference_document`が示す対象リポジトリ固有の振り返り参照文書 | 判断 | `tool:Read`、`tool:Bash` | `review-reference` |

## 常時規範が名指しする資料

常時配送される規範の選択基準に基づいて参照する資料の取得方法を、共通の宣言としてここにまとめる。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 配送操作の語彙と記法を選ぶ前 | `read-trigger-contract-declarations.md` |
| 成果作業を終える作業完了報告を送る前 | `../skills/atk-mode/playbooks/report-completion.md` |
| 採否・手段・進め方を判断する前 | `agent-basics:writing-standards/references/quality-principles-qcd.md`、`agent-basics:writing-standards/references/design-heuristics.md` |
| 完了・成功・不在・件数・互換性を述べる前と、要約・中継・報告を書く前 | `agent-basics:writing-standards/references/quality-principles-facts.md` |
| 常時規範の節が名指す時点（手段・規範・確定方針から外れる案の評価、資格情報の扱い、方針の衝突の判定、発話への回答の確定、進捗点検の通知を受けた時など） | `agent-conduct-details.md` |

読込経路の取得時分類と廃止前の本文配送の観測記録は、`docs/development/audit-records-read-triggers-initial.md`にある。
該当節は「agent-toolkit/share/read-trigger-bootstrap.md：操作を契機とする共有資料：2026年10月27日」である。

## 品質判断に伴う工程資料

品質判断に入るとき、現在の役割・工程に次の条件が成立する行の全文を読む。
hookの確認候補を受けたら条件を自分で確かめ、該当する資料を判断前に取得する。
品質の定義元はagent-basics、運用本文の定義元は各工程の資料とする。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| エージェント向け文書の責務と配置を決める前 | `agent-toolkit:/share/agent-documents-operations.md` | | |
| セッション記録の集計・分析 | `agent-toolkit:atk-mode/references/session-records.md` | | |
| process-loop・run-skillの統括の挙動（起動、turn終了、再開、外からの制御）を前提に、案・回答・確認の選択肢を出す前 | `agent-toolkit:atk-mode/references/orchestrator-runtime.md` | | |
| ユーザーの入力素材を整理して取り込む時 | `agent-basics:writing-standards/references/writing-genres.md` | | |
| 文章の書き直しを委ねる時 | `agent-toolkit:delegation/references/delegation-sending.md` | | |
| 人間向け文書の役割と残す内容を選ぶ時 | `agent-basics:writing-standards/references/document-types.md`、`agent-toolkit:atk-mode/references/workflow-overview.md` | | |
| 既存の対象を名前で指す時と新しい名前を付ける時 | `agent-toolkit:atk-mode/references/defined-names.md` | | |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時 | `agent-basics:writing-standards/references/design-data-interfaces.md` | | |
| テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `agent-toolkit:atk-mode/playbooks/verification.md` | | |
| エンドユーザーが操作する画面の新設・変更、その計画またはレビューの時 | `agent-toolkit:atk-mode/playbooks/verification.md` | | |
| スキル（`SKILL.md`・`references/`）を新設・編集する時 | `agent-toolkit:/share/agent-skills-operations.md` | | |
| process-wiのレーンworktreeを作成・保持・回収する時 | `agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md` | | |
| エージェント向け文書の置き場所を選ぶ時 | `agent-toolkit:delegation/references/delivery-scope.md` | | |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `agent-toolkit:delegation/references/sub-agents-context.md` | | |
| `Stop`・`SubagentStop`のhookと、終了工程の証拠を新設・変更する時 | `agent-basics:writing-standards/references/claude-hooks.md` | | |
| auto modeのカスタムルール編集（権限拒否に遭遇した場面は`agent-toolkit:atk-mode/playbooks/permission-denial.md`が扱う） | `agent-toolkit:atk-mode/references/auto-mode-rule-addition.md` | | |
| エージェント向け文書を新しく作る時、新しい規定を追記する時、指示文の改訂が必要と確定した時と、文書の記述量を管理する時 | `agent-basics:writing-standards/references/agent-documents-additions.md`、`agent-toolkit:atk-mode/playbooks/refine-prompt.md` | | |
| 事象単位の調査を委譲する前 | `agent-toolkit:delegation/references/investigation-delegation.md` | 判断 | `delegation`、`read:agent-toolkit:*/skills/delegation/references/investigation-delegation.md` |
| CI失敗を扱う前 | `agent-toolkit:atk-mode/references/ci.md` | | |
| 修正commitが必要なCI失敗の修正担当・CI記録・修正系列を扱う前 | `agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md` | | |
| ユーザー介入・確認、規範の未適用を分析する前と、類似見直しの委譲・記録・受渡しの前（操作対象外: 過去の確認の分析） | `agent-toolkit:atk-mode/playbooks/review-session-analysis.md`、`agent-toolkit:atk-mode/playbooks/user-utterance.md` | | |
| 検証対象の選定と計画、保存した結果の実行・取得、実行レビューで直接影響範囲を求める前 | `agent-toolkit:atk-mode/playbooks/verification.md` | | |
| 受入シナリオの文書試行・常駐サービスの公開前観測の前 | `agent-toolkit:atk-mode/references/workflow-phases.md` | | |
| 専用レビュータスク契約を受領し、判定と返却する前（操作対象外: 役割の開始入力） | `agent-toolkit:/share/exec-review-criteria.md` | | |
| 読者別探索を起動・検収する前 | `agent-toolkit:/share/exec-review-criteria.md` | 判断 | `delegation`、`delegation-result`、`read:agent-toolkit:*/share/exec-review-criteria.md` |
| 計画・WIの要件と証拠を判定する前 | `agent-toolkit:/share/exec-review-criteria.md` | | |
| レビューイーとして採否と修正を確定する前 | `agent-toolkit:/share/exec-review-responses.md` | | |
| 実行レビューの指摘登録・完成条件の判定と証拠記入の前 | `agent-toolkit:/share/exec-review-recording.md` | | |
| 実機確認だけを目的としてエージェントCLIや検証用委譲を起動する前 | `../skills/delegation/references/routing-experiments.md` | 判断 | `delegation`、`read:agent-toolkit:*/skills/delegation/references/routing-experiments.md` |
