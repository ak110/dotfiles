# 計画ファイル作成基準

本書は`~/.claude/plans/*.md`の全項目を起草・実行レビューの契約とする。
読込時点は起草者が`agent-toolkit:write-plan`、レビュー担当が`${CLAUDE_PLUGIN_ROOT}/share/exec-review.subagent.md`の読込表に従う（所在参照）。
保存・参照は`plan-file-storage.md`、復元型の変更と保持・不変性・完全復元は`plan-restoration-contracts.md`、構造検証は`plan-structure-check.md`が定める。

計画は要求漏れを防ぐ実装・実行レビューの共通基準と、記録・中断後の再開用の内部資料であり、ユーザー承認は不要である。
要件の追加判断なしに実装へ着手できる要件・外部仕様とし、`agent-basics:writing-standards/references/writing-readers.md`「読み手別の追加注意点」のエージェント向け文書の基準に従う。
一般設計・コーディングは方針とキーワードで示し、変更対象、確定要件・外部仕様、入力、期待結果、異常系、受入条件、固有の検証コマンドを具体化する。
実装手順・関数分割・制御フロー・修正後全文などは実行時のコードで確定し、実装不良はテスト、要件・外部仕様の充足と過剰設計は実行レビューで判定する。

`ユーザー`・`エンドユーザー`は`agent-toolkit/share/rules/common/01-agent.md`「役割分担」に従い、ユーザーは計画の要求・指示・合意・確認回答を示す人である。
「消費主体」は人間、コード、委譲元エージェント、CIなど成果物を利用・検証する主体を指す。

計画を構成するファイル群は次の呼称で区別する。

| 呼称 | 実体 |
| --- | --- |
| 計画ファイル | `<計画名>.md` |
| 計画ファイル（バグ） | `<計画名>.bugs.md` |
| 実行レビュー指摘管理表 | `<計画名>.exec-review.tsv` |
| 計画バンドル | 計画ファイルと、同じステム名の付属ファイルの一式。付属ファイルには計画ファイル（バグ）、実行レビュー指摘管理表などが含まれる |

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 共通参照AWI読解前 | `../../wi-standards/references/cross-repository-submission.md` |
| 参照基準の適用前 | `../SKILL.md` |
| 参照基準の適用前 | `plan-file-storage.md` |
| 参照基準の適用前 | `plan-restoration-contracts.md` |
| 参照基準の適用前 | `plan-structure-check.md` |
| 参照基準の適用前 | `agent-basics:writing-standards/SKILL.md` |
| 参照基準の適用前 | `agent-basics:writing-standards/references/writing-readers.md` |
| 参照基準の適用前 | `../../wi-standards/references/wi-standards-origin.md` |
| 参照基準の適用前 | `agent-basics:bugfix/references/root-cause-remediation.md` |
| 参照基準の適用前 | `agent-basics:bugfix/references/root-cause-severity.md` |
| 参照基準の適用前 | `agent-basics:bugfix/references/root-cause-actions.md` |
| 参照基準の適用前 | `agent-basics:bugfix/references/root-cause-prevention.md` |
| 参照基準の適用前 | `../../../share/exec-review-responses.md` |
| 参照基準の適用前 | `../../process-wi/SKILL.md` |
| レーンの予測補正記録を書く前 | `../../process-wi/references/selection-format-prediction.md` |
| 参照基準の適用前 | `agent-basics:writing-standards/references/notation-rules.md` |
| 参照基準の適用前 | `agent-toolkit:write-plan/references/testing-branch-verification.md` |
| 参照基準の適用前 | `../../user-confirmation-and-report/references/user-confirmation-and-report-authorization.md` |
| 参照基準の適用前 | `agent-basics:writing-standards/references/design-time.md` |
| 参照基準の適用前 | `agent-toolkit:/share/exec-review-recording.md` |
| 参照基準の適用前 | `agent-toolkit:/share/acceptance-scenarios.md` |
| 参照基準の適用前 | `../../wi-standards/references/awi-body.md` |
| 参照基準の適用前 | `agent-basics:writing-standards/references/design-heuristics-quality.md` |
| 参照基準の適用前 | `agent-basics:writing-standards/references/agent-documents-additions-volume.md` |
| 参照基準の適用前 | `agent-basics:bugfix/SKILL.md` |
| 参照基準の適用前 | `../../user-confirmation-and-report/references/user-utterance-incorporation.md` |
| 参照基準の適用前 | `../../wi-standards/references/awi-body-completion.md` |
| 本文で基準を使う資料を選択・取得する時 | `agent-toolkit:/share/verification-operations.md` |
| 本文で基準を使う資料を選択・取得する時 | `agent-toolkit:wi-standards/references/analysis-records.md` |
| 本文で基準を使う資料を選択・取得する時 | `../../commit/references/commit-wi-records.md` |
| 実作業の経験の恒久化候補を選ぶ前 | `agent-basics:writing-standards/references/investigation-experience.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 計画ファイルの構成・概要と計画メタ情報に関する判断・作業の前 | `plan-file-standards-structure.md` |
| 実施内容に関する判断・作業の前 | `plan-file-standards-scope.md` |
| 要件・外部仕様に関する判断・作業の前 | `plan-file-standards-requirements.md` |
| 設計・実装方針・恒久化・リファクタリング・変更履歴に関する判断・作業の前 | `plan-file-standards-design.md` |
| 検証と公開工程・バグ調査・要件の成立性・計画した検証の実行に関する判断・作業の前 | `plan-file-standards-verification.md` |
| 進捗ログに関する判断・作業の前 | `plan-file-standards-progress.md` |
| 初回起草の雛形に関する判断・作業の前 | `plan-file-standards-template.md` |
