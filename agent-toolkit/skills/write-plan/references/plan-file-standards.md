# 計画ファイル作成基準

本書は`~/.claude/plans/*.md`の全項目を起草・実行レビューの契約とする。
読込時点は起草者が`agent-toolkit:write-plan`、レビュー担当が`${CLAUDE_PLUGIN_ROOT}/share/exec-review.subagent.md`の読込表に従う（所在参照）。
保存・参照は`plan-file-storage.md`、復元型の変更と保持・不変性・完全復元は`plan-restoration-contracts.md`、構造検証は`plan-structure-check.md`が定める。

計画は要求漏れを防ぐ実装・実行レビューの共通基準と、記録・中断後の再開用の内部資料であり、ユーザー承認は不要である。
作成するのは`../SKILL.md`「計画ファイルの作成要否」の条件に当たる作業だけである。
関連WIの本文と合わせて、要件の追加判断なしに実装へ着手できる内容にする。
本書と条件別資料が定めるのは、計画メタ情報、実施内容、検証、進捗ログ、凍結後の訂正、保存、検査、旧形式の読み方である。
要求の書き方（何をどう変えるか、外部仕様、原因分析、判断根拠、完成条件、逐語引用）は`agent-toolkit:atk-mode/references/awi-body.md`とその条件別資料が定め、本書は再掲しない。
実装手順・関数分割・制御フロー・修正後全文などは実行時のコードで確定し、実装不良はテスト、要求の充足と過剰設計は実行レビューで判定する。

`ユーザー`・`エンドユーザー`は`agent-toolkit/share/rules/common/01-agent.md`「役割分担」に従い、ユーザーは計画の要求・指示・合意・確認回答を示す人である。
「消費主体」は人間、コード、委譲元エージェント、CIなど成果物を利用・検証する主体を指す。

計画を構成するファイル群は次の呼称で区別する。

| 呼称 | 実体 |
| --- | --- |
| 計画ファイル | `<計画名>.md` |
| 実行レビュー指摘管理表 | `<計画名>.exec-review.tsv` |
| 計画バンドル | 計画ファイルと、同じステム名の付属ファイルの一式。付属ファイルには実行レビュー指摘管理表などが含まれる |

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 参照基準の適用前 | `../SKILL.md` |
| 参照基準の適用前 | `plan-file-storage.md` |
| 参照基準の適用前 | `plan-restoration-contracts.md` |
| 参照基準の適用前 | `plan-structure-check.md` |
| 参照基準の適用前 | `agent-basics:writing-standards/SKILL.md` |
| 参照基準の適用前 | `agent-basics:writing-standards/references/writing.md` |
| 参照基準の適用前 | `../../process-wi/SKILL.md` |
| レーンの予測補正記録を書く前 | `../../process-wi/references/selection-format-prediction.md` |
| 参照基準の適用前 | `agent-basics:bugfix/SKILL.md` |
| 本文で基準を使う資料を選択・取得する時 | `../../commit/references/commit-wi-records.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 計画ファイルの構成・要約と計画メタ情報に関する判断・作業の前 | `plan-file-standards-structure.md` |
| 実施内容に関する判断・作業の前 | `plan-file-standards-scope.md` |
| 検証・原因分析・計画した検証の実行に関する判断・作業の前 | `plan-file-standards-verification.md` |
| 進捗ログに関する判断・作業の前 | `plan-file-standards-progress.md` |
| 初回起草の雛形に関する判断・作業の前 | `plan-file-standards-template.md` |
