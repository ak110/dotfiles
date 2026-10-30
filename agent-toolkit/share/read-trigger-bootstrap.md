# 操作を契機とする共有資料

操作に伴って共通で起動するスキルの宣言を本表にまとめる。各スキル固有の追加資料は、それぞれのスキル内の読込表で宣言する。
操作の対象と副作用から必要なスキルを判断し、まだ起動していなければ操作の前に起動する（努力目標。契約の確認不足と独自手順の重複を避ける）。
配送操作の語彙と記法は、`read-trigger-contract.md`「表と取得方法」の定義に従う。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

## 操作で起動する共有スキル

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 委譲操作の前 | `../skills/delegation/SKILL.md` | 操作 | `delegation` |
| 検索の前 | `agent-basics:search/SKILL.md` | 操作 | `search` |
| 原因分析の記述の前 | `agent-basics:bugfix/SKILL.md` | 操作 | `root-cause-writing` |
| 個別の一時領域の作成の前 | `../skills/managed-temp/SKILL.md` | 操作 | `managed-temp-create` |
| 本文の作成・更新の前 | `agent-basics:writing-standards/SKILL.md` | 操作 | `body-writing` |
| 検証の実行の前 | `agent-basics:check-execution/SKILL.md`、`check-execution-operations.md`、`verification-operations.md` | 操作 | `verification` |
| 要件・技術判断を分類し、確認要否を判定する前 | `../skills/user-confirmation-and-report/references/approval-scope.md` | 判断 | `skill:user-confirmation-and-report` |
| 確認本文と選択肢を起草する前 | `../skills/user-confirmation-and-report/references/approval-scope.md`、`../skills/user-confirmation-and-report/references/choice-construction.md` | 判断 | `confirmation` |
| 原因分析の判断前と記述前 | `agent-basics:bugfix/references/root-cause-analysis.md`、`agent-toolkit:wi-standards/references/analysis-records.md`、`agent-toolkit:user-confirmation-and-report/references/analysis-operations.md`、`agent-toolkit:session-review/references/analysis-operations.md`、`agent-toolkit:delegation/references/investigation-delegation.md` | 判断 | `skill:bugfix`、`root-cause-writing` |
| レビュー指摘管理表を作成・更新する前 | `review-table-operations.md` | 判断 | `skill:review-standards` |
| エージェント向け文書の編集前 | `agent-toolkit:delegation/references/writing.md`、`agent-toolkit:delegation/references/agent-documents-basics.md`、`agent-toolkit:delegation/references/agent-documents-basics.md#「運用条件」「置き場所と配送」「読込表の宣言」` | 操作 | `agent-document-writing` |

## 常時規範が名指しする資料

常時配送される規範の選択基準に基づいて参照する資料の取得方法を、共通の宣言としてここにまとめる。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 配送操作の語彙と記法を選ぶ前 | `read-trigger-contract.md#「表と取得方法」` | 判断 | `section:agent-toolkit:*/share/read-trigger-contract.md#「表と取得方法」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md#「認可を要する操作」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「認可を要する操作」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/communication-methods.md#「手段の選択」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/communication-methods.md#「手段の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/codex-runtime.md#「後続操作の共通先行条件」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/codex-runtime.md#「後続操作の共通先行条件」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/runtime-routing.md#「実行手段」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「実行手段」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/approval-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/approval-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/choice-construction.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/choice-construction.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/waiting-and-monitoring.md#「待機区間の構成」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md#「待機区間の構成」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/codex-format.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/codex-format.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/user-utterance.md#「成果物の制限」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md#「成果物の制限」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/SKILL.md#「継続と新規起動」` | 判断 | `section:agent-toolkit:*/skills/delegation/SKILL.md#「継続と新規起動」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/receiving.md#「受領後の扱い」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/receiving.md#「受領後の扱い」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec.parent.md#「レーン担当が確定した質問への回答」` | 判断 | `section:agent-toolkit:*/share/exec.parent.md#「レーン担当が確定した質問への回答」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/receiving.md#「返却形式の適合確認」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/receiving.md#「返却形式の適合確認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/routing.md#「要否判定」「委譲単位の確定」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/routing.md#「要否判定」「委譲単位の確定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/mandatory-rules.md#「作業ツリーの保護」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/mandatory-rules.md#「作業ツリーの保護」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/handoff-record.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/handoff-record.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/subagent-queue-ops.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/subagent-queue-ops.md` |

読込経路の取得時分類と廃止前の本文配送の観測記録は、`docs/development/audit-records.md`「agent-toolkit/share/read-trigger-bootstrap.md：操作を契機とする共有資料：2026年10月27日」にある。

## 品質判断に伴う工程資料

品質の取得と同じ時点で、次の条件に該当する工程資料を全文読む。品質の定義元はagent-basics、運用本文の定義元は各工程の資料とする。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| エージェント向け文書の責務と配置を決める前 | `agent-toolkit:delegation/references/agent-documents-basics.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/agent-documents-basics.md` |
| 成果物、報告・回答、委譲プロンプトへ書く事実主張を調査する時 | `agent-toolkit:user-confirmation-and-report/references/investigation.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/investigation.md` |
| セッション記録の集計・分析 | `agent-toolkit:session-review/references/session-records.md` | 判断 | `read:agent-toolkit:*/skills/session-review/references/session-records.md` |
| ユーザーの入力素材を整理して取り込む時 | `agent-toolkit:delegation/references/writing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/writing.md` |
| textlintなど文章チェックの診断へ対応する時 | `agent-toolkit:delegation/references/textlint-violations.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/textlint-violations.md` |
| 文章の書き直しを委ねる時 | `agent-toolkit:delegation/references/textlint-violations.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/textlint-violations.md` |
| 人間向け文書の役割と残す内容を選ぶ時 | `agent-toolkit:workflow-overview/references/document-types.md` | 判断 | `read:agent-toolkit:*/skills/workflow-overview/references/document-types.md` |
| 既存の対象を名前で指す時と新しい名前を付ける時 | `agent-toolkit:workflow-overview/references/defined-names.md` | 判断 | `read:agent-toolkit:*/skills/workflow-overview/references/defined-names.md` |
| `ps1`・`ps1.tmpl`・`psm1`・`psd1` | `agent-toolkit:completion-report/references/powershell.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/references/powershell.md` |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時 | `agent-toolkit:commit/references/design-time.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/design-time.md` |
| テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `agent-toolkit:commit/references/testing.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/testing.md` |
| エンドユーザーが操作する画面の新設・変更、その計画またはレビューの時 | `agent-toolkit:managed-temp/references/ui-ux.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/references/ui-ux.md` |
| スキル編集 | `agent-toolkit:completion-report/references/agent-skills.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/references/agent-skills.md` |
| エージェント向け文書の置き場所を選ぶ時 | `agent-toolkit:delegation/references/delivery-scope.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/delivery-scope.md` |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `agent-toolkit:delegation/references/sub-agents.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sub-agents.md` |
| `Stop`・`SubagentStop`のhookと、終了工程の証拠を新設・変更する時 | `agent-toolkit:completion-report/references/claude-hooks-stop.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/references/claude-hooks-stop.md` |
| auto modeのカスタムルール編集（権限拒否に遭遇した場面は`agent-toolkit:user-confirmation-and-report`が扱う） | `agent-toolkit:user-confirmation-and-report/references/auto-mode.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/auto-mode.md` |
| エージェント向け文書へ新しい規定を追記する時と、文書の記述量を管理する時 | `agent-toolkit:wi-standards/references/agent-documents-additions.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/agent-documents-additions.md` |
| AWIの原因分析を起草する前、計画ファイル（バグ）を作成する前 | `agent-toolkit:wi-standards/references/analysis-records.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/analysis-records.md` |
| 事象単位の調査を委譲する前 | `agent-toolkit:delegation/references/investigation-delegation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/investigation-delegation.md` |
| CI失敗を扱う前 | `agent-toolkit:commit/references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/ci-failure-handling.md` |
| ユーザー介入・確認、規範の未適用を分析する前と、類似見直しの委譲・記録・受渡しの前 | `agent-toolkit:session-review/references/analysis-operations.md` | 判断 | `read:agent-toolkit:*/skills/session-review/references/analysis-operations.md` |
| 検証対象の選定と計画・保存結果の実行・取得前（実行レビューで直接影響範囲だけを求める場合を除く） | `agent-toolkit:/share/verification-operations.md` | 判断 | `read:agent-toolkit:*/share/verification-operations.md` |
| 実行レビューで直接影響範囲を求める前 | `agent-toolkit:/share/verification-operations.md` | 判断 | `read:agent-toolkit:*/share/verification-operations.md` |
| 受入シナリオの文書試行・常駐サービスの公開前観測の前 | `agent-toolkit:delegation/references/acceptance-scenarios.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/acceptance-scenarios.md` |
| 専用レビュータスク契約を受領し、判定と返却する前 | `agent-toolkit:/share/exec-review-criteria.md` | 判断 | `read:agent-toolkit:*/share/exec-review-criteria.md` |
| 読者別探索を起動・検収する前 | `agent-toolkit:/share/exec-review-criteria.md` | 判断 | `read:agent-toolkit:*/share/exec-review-criteria.md` |
| 計画・WIの要件と証拠を判定する前 | `agent-toolkit:/share/exec-review-criteria.md` | 判断 | `read:agent-toolkit:*/share/exec-review-criteria.md` |
| レビューイーとして採否と修正を確定する前 | `agent-toolkit:/share/exec-review-responses.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` |
| 実行レビューの指摘登録・完成条件の判定と証拠記入の前 | `agent-toolkit:/share/exec-review-recording.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording.md` |
| 実機確認だけを目的としてエージェントCLIや検証用委譲を起動する前 | `agent-toolkit:delegation/references/routing.md#「実行結果の検証」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/routing.md#「実行結果の検証」` |
