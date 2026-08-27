---
name: exit-session
description: 自律終了スキル。他スキル呼び出しまたはユーザー手動起動。
# 編集時の注意点:
# 起動条件に列挙の無いスキルから本スキルへの遷移指示を新設しない。
allowed-tools: Bash
---

# セッション自律終了

本スキルは、現在の対話セッションを自律終了する手順を提供する。
本体プロセスを一意に識別できる実行環境では、当該単一プロセスへ停止を要求して`/exit`に近い停止挙動を得る。
一意に識別できない実行環境ではプロセスを停止せず、終了理由と対話CLIの終了案内を最終応答としてターンを完了する。
ホスト別・環境別の判定と停止手順は`references/host-and-os-termination.md`を正本とする。
process-loopから起動されたセッションでは、agent-toolkit pluginのPostToolUseが`exit-session`の呼び出しを状態へ記録し、pluginのStop hookが`autonomous_exit_invoked`を参照して呼び出し漏れを検出する。

## 起動条件

次のいずれかを満たすときに限り呼び出す。

- `agent-toolkit:process-feedbacks`「6. 振り返りと終了」節から呼ばれた場合
- 自律終了再促フック（`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`環境変数のStopフック）から未起動判定時の再促として誘導された場合
- ユーザーがSkill名を明示的に指定して本スキルを起動した場合

作業完了・振り返り完了・会話の区切りのみを契機に呼び出さない。
ユーザーの一般的な終了意思表示（「終了」「やめる」等）のみでは起動せず、`/exit`の入力を案内する。
途中失敗・エラー・部分完遂・ユーザー確認事項の残置など、完遂を妨げる状態では呼び出さず、
呼び出し元スキルの通常の完了報告経路に従う。

## 実行手順

1. 終了理由を1文で発話する（呼び出し元スキルの完遂サマリーと重複する場合は要点のみ記述する）
2. `references/host-and-os-termination.md`を全文読み、実行ホストと実行環境を判定する
3. 判定した経路に従って本体プロセスの停止を要求するか、ユーザーへの案内でターンを完了させる
4. 停止要求の発火後は本体プロセスが停止するため、後続のツール呼び出し・発話は行わない

## auto mode下で拒否される場合の対処

`kill -TERM $PPID`がauto mode classifierに拒否される事象を観測している。
対処は`agent-toolkit:agent-standards`の
`references/auto-mode.md`「既知の誤拒否パターンと対応」節のexit-session該当項を参照する。
