# Claude Codeでの委譲の実装

本書はClaude Codeから委譲する際の実装上の取り決めを定める。
共通の送信・受領契約は`agent-toolkit:delegation`のSKILL.mdに従う。
委譲先の選定は同スキルの`references/runtime-routing.md`「実行手段」、worktreeの扱いは`references/implementation-worktree.md`「実装担当とworktree」に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-worktree.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/implementation-worktree.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `routing-fork.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing-fork.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `delegation-sending.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/delegation-sending.md` |
| 委譲入力の言語指定を確定する前 | `base-contract-operations.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract-operations.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `waiting-and-monitoring-intervals.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-intervals.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `receiving-format.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/receiving-format.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `waiting-and-monitoring-responses.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-responses.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `wait-anomalies-stopped.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-stopped.md` |
| `Agent`ツールで起動した担当を継続する前 | `continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/continuation.md` |
| `Agent`ツールで起動した担当を継続する前 | `agent-tool-continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/agent-tool-continuation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `waiting-and-monitoring-auto-resume.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-auto-resume.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 起動パラメーター・委譲プロンプトへ含める前提・計画ファイル作成のplan mode下委譲に関する判断・作業の前 | `claude-code-runtime-launch.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime-launch.md` |
| 実行時能力と通信scope・委譲先での発火に関する判断・作業の前 | `claude-code-runtime-communication.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime-communication.md` |
| 一時領域と成果物の観測・結果の受け取り方・稼働状態の観測と継続依頼に関する判断・作業の前 | `claude-code-runtime-receiving.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime-receiving.md` |
| 検証コマンドの実行形態・待機中の定期再確認と背景転換に関する判断・作業の前 | `claude-code-runtime-waiting.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime-waiting.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 委譲の基準を適用する前 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/delegation/SKILL.md` |
