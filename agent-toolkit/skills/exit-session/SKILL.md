---
name: exit-session
description: >
  Claude Code本体プロセスへシグナルを送出して現セッションを自律終了する。
  `agent-toolkit:process-feedbacks-finish`工程6、自律終了再促フック（未起動判定時の再促誘導。
  dotfiles個人環境固有の`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`環境変数で有効化され、
  配布物の利用者環境では既定で無効）、またはユーザーがSkill名を明示指定した場合にのみ起動する。
  作業完了・振り返り完了・会話の区切りのみを契機に呼び出さない。
  「終了」「やめる」等のユーザーの一般的な意思表示のみでは起動せず、`/exit`の入力を案内する。
allowed-tools: Bash
---

# セッション自律終了

現在のClaude Codeセッションを自律終了する。
`Bash`ツールから親プロセス（Claude Code本体プロセス）へシグナルを送出することで、
`/exit`スラッシュコマンドに近い停止挙動を得る。

## 起動条件

次のいずれかを満たすときに限り呼び出す。

- `agent-toolkit:process-feedbacks-finish`工程6から呼ばれた場合
- 自律終了再促フック（`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`環境変数のStopフック。dotfiles個人環境専用）から
  未起動判定時の再促として誘導された場合
- ユーザーがSkill名を明示的に指定して本スキルを起動した場合

作業完了・振り返り完了・会話の区切りのみを契機に呼び出さない。
ユーザーの一般的な終了意思表示（「終了」「やめる」等）のみでは起動せず、`/exit`の入力を案内する。
上記いずれにも該当しないスキルから本スキルへの遷移指示を新設しない
（不可逆操作の呼び出し集約規範は`agent-toolkit:agent-standards`「共通の記述原則」節
「不可逆操作の呼び出し集約」小見出しに従う）。
途中失敗・エラー・部分完遂・ユーザー確認事項の残置がある状態では呼び出さず、
呼び出し元スキルの通常の完了報告経路に従う。

## 実行手順

1. 終了理由を1文で発話する（呼び出し元スキルの完遂サマリーと重複する場合は要点のみ記述する）
2. `Bash`ツールで`kill -TERM $PPID`を実行する
   - 採用シグナルは本スキル本文で固定する。現状は`TERM`
   - 発火後はClaude Code本体プロセスが停止するため、後続のツール呼び出し・発話は行わない

## auto mode下で拒否される場合の対処

`kill -TERM $PPID`がauto mode classifierに拒否される場合がある。
対処は`agent-toolkit/skills/agent-standards/references/auto-mode.md`
「既知の誤拒否パターンと対応」節のexit-session該当項を参照する。

## シグナル種別の見直し

実運用で`kill -TERM $PPID`実行後にClaude Code本体プロセスが停止しない現象を観測した場合、
`kill -INT $PPID`（SIGINT）へ本スキル本文を書き換えて対応する。
本スキル実行時に動的に切り替える構造は取らない
（SIGTERM送出後は本体プロセスが停止するため後続ツール呼び出しが実行不能となるため）。
