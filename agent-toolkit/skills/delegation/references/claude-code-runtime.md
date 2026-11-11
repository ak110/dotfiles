# Claude Codeでの委譲の実装

本書はClaude Codeから委譲する際の実装上の取り決めを定める。
共通の送信・受領契約は`agent-toolkit:delegation`のSKILL.mdに従う。
委譲先の選定は同スキルの`references/runtime-routing.md`「実行手段」、worktreeの扱いは`references/implementation-worktree.md`「実装担当とworktree」に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 委譲入力の言語指定を確定する前 | `base-contract-operations.md` |
| `Agent`ツールで起動した担当を継続する前 | `continuation.md` |
| `Agent`ツールで起動した担当を継続する前 | `agent-tool-continuation.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 起動パラメーター・委譲プロンプトへ含める前提・計画ファイル作成のplan mode下委譲に関する判断・作業の前 | `claude-code-runtime-launch.md` |
| 実行時能力と通信scope・委譲先での発火に関する判断・作業の前 | `claude-code-runtime-communication.md` |
| 一時領域と成果物の観測・結果の受け取り方・稼働状態の観測と継続依頼に関する判断・作業の前 | `claude-code-runtime-receiving.md` |
| 検証コマンドの実行形態・待機中の定期再確認と背景転換に関する判断・作業の前 | `claude-code-runtime-waiting.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 委譲の基準を適用する前 | `../SKILL.md` |
