---
name: exit-session
description: 自律終了スキル。他スキル呼び出しまたはユーザー手動起動。
allowed-tools: Bash
---

# セッション自律終了

現在のClaude Codeセッションを自律終了する。
`Bash`ツールから親プロセス（Claude Code本体プロセス）へシグナルを送出することで、
`/exit`スラッシュコマンドに近い停止挙動を得る。

## 起動条件

次のいずれかを満たすときに限り呼び出す。

- `agent-toolkit:process-feedbacks`ステップ8から呼ばれた場合
- 自律終了再促フック（`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`環境変数のStopフック。dotfiles個人環境専用）から
  未起動判定時の再促として誘導された場合
- ユーザーがSkill名を明示的に指定して本スキルを起動した場合

作業完了・振り返り完了・会話の区切りのみを契機に呼び出さない。
ユーザーの一般的な終了意思表示（「終了」「やめる」等）のみでは起動せず、`/exit`の入力を案内する。
上記いずれにも該当しないスキルから本スキルへの遷移指示を新設しない。
途中失敗・エラー・部分完遂・ユーザー確認事項の残置がある状態では呼び出さず、
呼び出し元スキルの通常の完了報告経路に従う。

## 実行手順

1. 終了理由を1文で発話する（呼び出し元スキルの完遂サマリーと重複する場合は要点のみ記述する）
2. 実行環境を判定し、対応する経路でClaude Code本体プロセスへ停止を要求する
   - POSIX互換のプロセス識別が成立する環境: `Bash`ツールで`kill -TERM $PPID`を実行する
   - Windows環境（`$PPID`が実プロセスを指さない環境を含む）: PowerShellから
     `Get-CimInstance Win32_Process`を使い、PowerShell自身のPIDから`ParentProcessId`をたどる。
     祖先の実行ファイルとコマンドラインを照合してClaude Code本体を一意に特定し、
     当該単一PIDだけを`Stop-Process -Id`で終了する。実行ファイル名の一致だけを根拠にしない
     （`agent-toolkit/rules/02-claude-code.md`「サブエージェント運用」節のプロセス終了規定に従う）
   - 対象を一意に特定できない場合は停止を要求せず、利用者へ`/exit`の入力を案内して本スキルを終える
3. POSIX経路の採用シグナルは本スキル本文で`TERM`へ固定する。
   Windows経路はPOSIXシグナルを用いず、前項の`Stop-Process -Id`へ固定する
4. 停止要求の発火後はClaude Code本体プロセスが停止するため、後続のツール呼び出し・発話は行わない

## auto mode下で拒否される場合の対処

`kill -TERM $PPID`がauto mode classifierに拒否される場合がある。
対処は`agent-toolkit/skills/agent-standards/references/auto-mode.md`
「既知の誤拒否パターンと対応」節のexit-session該当項を参照する。

## シグナル種別の見直し

実運用でPOSIX互換経路から`kill -TERM $PPID`実行後にClaude Code本体プロセスが停止しない現象を観測した場合、
`kill -INT $PPID`（SIGINT）へ本スキル本文を書き換えて対応する。
これとは別に、Windows環境では`$PPID`がinit相当の値を返し、
Claude Code本体プロセスへ到達しない事象を観測している。
実行手順の環境判定はこの観測に基づく。
本スキル実行時に動的に切り替える構造は取らない
（SIGTERM送出後は本体プロセスが停止するため後続ツール呼び出しが実行不能となるため）。
