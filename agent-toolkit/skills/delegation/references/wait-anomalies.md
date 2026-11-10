# 待機の異常時の扱い

本書は待機を開始できない場合、`atk agents wait`や待機中のツール呼び出しが異常終了した場合、ホストから背景待機の停止通知を受けた場合、および`agents_server`のMCPサーバーが再起動した場合の対処手順を定める。
平常時の待機手順については`references/waiting-and-monitoring.md`を参照する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `waiting-and-monitoring.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 待機の呼び出しが拒否された時、またはclassifierが判定不能を返した時 | `../../user-confirmation-and-report/references/permission-denial.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/permission-denial.md` |
| 必須操作を報告する前 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 待機を開始できない場合に関する判断・作業の前 | `wait-anomalies-refusal.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-refusal.md` |
| 終了コードと呼び出しの失敗に関する判断・作業の前 | `wait-anomalies-failures.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-failures.md` |
| 役割宣言・起動時資料の起動前検査で稼働実装と役割資源の版相違が示された時 | `wait-anomalies-failures.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-failures.md` |
| ホストによる背景待機の停止後に関する判断・作業の前 | `wait-anomalies-stopped.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-stopped.md` |
| MCPサーバーの再起動後に関する判断・作業の前 | `wait-anomalies-server-restart.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-server-restart.md` |
