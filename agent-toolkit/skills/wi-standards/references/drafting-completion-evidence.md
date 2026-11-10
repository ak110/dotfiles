# 起草完了の条件：既存証拠の再利用

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 報告用UWIの確認・保存手順へ切り替える前 | `../../report-completion/references/report-formats-uwi.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-uwi.md` |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 観測事象を契機とする要求の採用を確定する前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md`、`agent-basics:bugfix/references/root-cause-actions-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-actions-selection.md` |
| 一連の処理全体の成立を判定する前 | `agent-basics:bugfix/references/root-cause-actions-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions-selection.md` |
| 類似見直しの限定探索と是正範囲を判定する前 | `agent-basics:bugfix/references/root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 原因分析の記録が推定だけに依拠していないことを判定する前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md` |

## 既存証拠の再利用

計画開始時は、既存証拠の対象・版・前提を現在の現物と比べる。
変化した部分、残る問い、実装に必要な詳細だけを再調査し、一致する範囲は再利用する。
