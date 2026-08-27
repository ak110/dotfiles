# ホスト別・環境別の終了経路

実行手順で停止要求を発行する直前に本文書を全文読む。
起動条件と手順の骨子は`../SKILL.md`を正本とする。

## 終了能力の判定

停止対象の本体プロセスを一意に識別できるかを、実行ホストと環境の観測結果で判定する。

| 入力環境 | 判定 | 終了経路 |
| --- | --- | --- |
| Claude Codeで既存のPOSIX直親又はWindows祖先を一意識別できる | 停止可能 | 既存の単一PID停止を維持する |
| Linuxでremote-controlなしのCodex対話CLIが前記argv・TTY・実行ファイル条件へ一致し、停止直前のPID開始時刻と実行ファイルのデバイス・inodeも一致する | 停止可能 | 表示済み単一PIDへ`TERM`を送る |
| Codexの直親が`app-server`等の共有プロセスである | 停止不能 | シグナルを送らず終了案内を返す |
| Windows Codex、親子関係を取得できない環境又は判定条件を満たさない環境 | 停止不能 | シグナルを送らず終了案内を返す |

## Claude Codeでの停止要求

実行環境を判定し、対応する経路で本体プロセスへ停止を要求する。

- POSIX互換のプロセス識別が成立する環境: `Bash`ツールで`kill -TERM $PPID`を実行する。
  採用シグナルは`TERM`へ固定し、実行時に切り替えない
- Windows環境（`$PPID`が実プロセスを指さない環境を含む）: `Bash`ツールから
  `powershell.exe -NoProfile -Command "<スクリプト>"`の形でPowerShellを起動する。
  スクリプトは`Get-CimInstance Win32_Process`で自身のPIDから`ParentProcessId`をたどり、
  祖先の実行ファイルとコマンドラインを照合してClaude Code本体を一意に特定し、
  当該単一PIDだけを`Stop-Process -Id <PID>`で終了する。
  POSIXシグナルは用いず、実行ファイル名の一致だけを根拠にしない
  （`agent-toolkit/rules/02-agent-operations.md`「プロセス終了の安全規定」節の所有PID原則に従う。
  親子関係・実行ファイル・コマンドラインを照合して一意に特定した単一PIDは所有権確認済みとして扱う）
- 対象を一意に特定できない場合は停止を要求せず、ユーザーへ`/exit`の入力を案内して本スキルを終える

## LinuxのCodex直接CLIでの停止要求

Linuxでremote-controlを使わないCodex直接CLIを実行している場合は、停止要求の直前に、最初の`Bash`ツール呼び出しで次をそのまま実行する。

```sh
candidate_pid=$PPID
candidate_exe=$(readlink -f "/proc/$candidate_pid/exe") || exit 1
candidate_exe_id=$(stat -Lc '%d:%i' "/proc/$candidate_pid/exe") || exit 1
candidate_start=$(awk '{print $22}' "/proc/$candidate_pid/stat") || exit 1
candidate_tty=$(ps -p "$candidate_pid" -o tty=) || exit 1
mapfile -d '' -t candidate_argv < "/proc/$candidate_pid/cmdline" || exit 1
printf 'pid=%s\nexe_basename=%s\nexe_id=%s\nstart=%s\ntty=%s\nargc=%s\n' "$candidate_pid" "${candidate_exe##*/}" "$candidate_exe_id" "$candidate_start" "$candidate_tty" "${#candidate_argv[@]}"
for index in "${!candidate_argv[@]}"; do printf 'argv[%s]=%q\n' "$index" "${candidate_argv[$index]}"; done
```

全ての読取りが成功し、`pid`と`start`が正の10進整数、`exe_id`が`<10進整数>:<10進整数>`、`exe_basename`と`argv[0]`のbasenameがともに`codex`、`tty`が空でも`?`でもないことを確認する。argvが次のいずれかに一致する場合だけ停止可能と判定する。

- `argc=1`の`codex`
- `codex resume`で始まるargv
- `AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`で、次のいずれかに一致するargv
  - `codex --model <値> -c model_reasoning_effort=<値> <目的文>`
  - `codex resume --model <値> -c model_reasoning_effort=<値> [session-id]`

argvはNUL区切りの要素単位で照合し、目的文に現れる語をsubcommandとして扱わない。前記以外、`app-server`若しくは`remote-control`がsubcommandであるargv、`--remote`を持つargv、読取失敗又は値の不一致は停止不能とする。

この単一PIDへの停止要求は、`agent-toolkit/rules/02-agent-operations.md`「プロセス終了の安全規定」に定める、セッション本体を一意に識別した場合の契約済み経路として扱う。

停止可能と判定した場合だけ、2回目の`Bash`ツール呼び出しで、1回目に表示された数値形式の`pid`、`exe_id`及び`start`を次のプレースホルダーへそのまま代入して単独実行する。実行ファイルの生パスはコマンドへ再埋込みしない。

```sh
test "$(stat -Lc '%d:%i' /proc/<表示されたpid>/exe)" = '<表示されたexe_id>' && test "$(awk '{print $22}' /proc/<表示されたpid>/stat)" = '<表示されたstart>' && kill -TERM <表示されたpid>
```

再照合が失敗した場合は`kill`を実行しない。プロセス名の検索、推測又は再構成したPID、`pkill`、`killall`及び`codex remote-control stop`は使用しない。

## 停止不能な環境

本体プロセスを一意に特定できない場合は停止を要求せず、終了理由を最終応答としてターンを完了させる。
対話CLIを閉じる操作はユーザーに委ね、`/exit`または`/quit`の入力が必要である旨を案内する。

Codex CLI 0.150.1をLinuxでremote-controlなしに実行した実測では、`ps`、`readlink /proc/$PPID/exe`及び`kill -0 $PPID`で、
ツールシェルの直親が別TTYのCodexとは異なるセッション固有の単一`codex`プロセスであることを確認した。
実セッションを失わないため、実測時には`TERM`を送っていない。

## シグナル種別の見直し

POSIX互換経路から`kill -TERM $PPID`を実行してもClaude Code本体プロセスが停止しない現象を
実運用で観測した場合は、対象PIDがClaude Code本体を指していたことを確認する。
確認後に`kill -INT $PPID`（SIGINT）へ本文書を書き換えて対応する。
SIGTERM送出後は本体プロセスが停止して後続のツール呼び出しが実行不能となるため、実行時に動的へ切り替える構造は取らない。
Windows環境で`$PPID`がinit相当の値を返しClaude Code本体プロセスへ到達しない事象は別に観測しており、
ホストの判定と環境の判定はこの2つの観測に基づく。
