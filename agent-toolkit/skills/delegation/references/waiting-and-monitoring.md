# 待機と観測

委譲先の処理や、委譲を伴わないバックグラウンドタスクの完了を待つ際の観測手順、停滞の判定基準、および巻き取りの手順を定める。
プロセスを終了させる操作の所有権については、`agent-toolkit/share/rules/common/02-agent-operations.md`の「プロセス終了の安全規定」に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| セッション記録を復元・進捗・操作の観測へ使う前 | `../../review-session/references/session-records-conversation.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/session-records-conversation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-code-runtime-receiving.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime-receiving.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `wait-anomalies.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `wait-anomalies-failures.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-failures.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/references/push-and-ci.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/push-and-ci.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `wait-anomalies-stopped.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-stopped.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `codex-runtime-continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/codex-runtime-continuation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-code-runtime-waiting.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime-waiting.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `codex-runtime-waiting.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/codex-runtime-waiting.md` |
| Claude Codeのバックグラウンドタスクの起動形を判断する時 | `waiting-claude-code.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-claude-code.md` |
| 停滞の検知と巻き取りへ進む時 | `waiting-stalls.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-stalls.md` |
| 拒否またはclassifierの判定不能で待機を開始できない時 | `wait-anomalies-refusal.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-refusal.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 待機手段の選択・待機の起動条件・バックグラウンドタスクの所有記録に関する判断・作業の前 | `waiting-and-monitoring-setup.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-setup.md` |
| 待機区間の構成に関する判断・作業の前 | `waiting-and-monitoring-intervals.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-intervals.md` |
| `atk agents wait`の応答の扱いに関する判断・作業の前 | `waiting-and-monitoring-responses.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-responses.md` |
| 外部サービスの状態変化を待つ場合に関する判断・作業の前 | `waiting-and-monitoring-external.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-external.md` |
| 完了通知を待ってターンを終える場合に関する判断・作業の前 | `waiting-and-monitoring-auto-resume.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-auto-resume.md` |
| 待機中の先行実施に関する判断・作業の前 | `waiting-and-monitoring-independent-work.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-independent-work.md` |
| 成果物側の観測に関する判断・作業の前 | `waiting-and-monitoring-progress.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-progress.md` |
