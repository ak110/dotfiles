# ホスト別・環境別の終了経路

実行手順で停止要求を発行する直前に本文書を全文読む。
起動条件と手順の骨子は`../SKILL.md`を正本とする。

## 終了能力の判定

停止対象の本体プロセスを一意に識別できるかを、実行ホストと環境の観測結果で判定する。

| 入力環境 | 判定 | 終了経路 |
| --- | --- | --- |
| Claude Codeで既存のPOSIX直親又はWindows祖先を一意識別できる | 停止可能 | 識別した単一PIDを停止する |
| Linuxでremote-controlなしのCodex対話CLIが前記argv・TTY・実行ファイル条件へ一致し、停止直前のPID開始時刻と実行ファイルのデバイス・inodeも一致する | 停止可能 | 表示済み単一PIDへ`TERM`を送る |
| Codexの直親が`app-server`等の共有プロセスである | 停止不能 | シグナルを送らず終了案内を返す |
| Windows Codex、親子関係を取得できない環境又は判定条件を満たさない環境 | 停止不能 | シグナルを送らず終了案内を返す |

## Claude Codeでの停止要求

実行環境を判定し、対応する経路でClaude Code本体として一意識別した単一PIDへ停止を要求する。

- POSIX互換のプロセス識別が成立する環境: `Bash`ツールの直親が現在のClaude Code本体であることを確認し、`kill -TERM $PPID`を実行する。
  採用シグナルは`TERM`へ固定し、実行時に切り替えない
- Windows環境（`$PPID`が実プロセスを指さない環境を含む）: `Bash`ツールから
  `powershell.exe -NoProfile -Command "<スクリプト>"`の形でPowerShellを起動する。
  スクリプトは`Get-CimInstance Win32_Process`で自身のPIDから`ParentProcessId`をたどり、
  祖先の実行ファイルとコマンドラインを照合してClaude Code本体を一意に特定し、
  当該単一PIDだけを`Stop-Process -Id <PID>`で終了する。
  POSIXシグナルは用いず、実行ファイル名の一致だけを根拠にしない
- 対象を一意に特定できない場合: 停止を要求せず、ユーザーへ`/exit`の入力を案内して本スキルを終える

前記の停止要求は、`agent-toolkit/rules/02-agent-operations.md`「プロセス終了の安全規定」に定める、
現在のClaude Code本体を安全に一意識別した自身の終了経路として扱う。

## LinuxのCodex直接CLIでの停止要求

Linuxでremote-controlを使わないCodex直接CLIを実行している場合は、終了能力probeを実行する都度、最初の`Bash`ツール呼び出しで次をそのまま実行する。

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

このコマンドは読み取りと標準出力だけを行う副作用のない終了能力probeである。
`kill`、ファイルへの書込み及びCodex CLIその他のコマンドの起動をせず、環境変数も変更しない。
いずれかの読取りが失敗した場合はprobe未完了として停止不能にする。

### Codex CLI通常起動argvの解析

Codex CLI 0.150.1の`codex --help`で確認した通常起動は`codex [OPTIONS] [PROMPT]`である。
`candidate_argv`は`mapfile -d ''`で取得したNUL区切りの配列として扱い、文字列全体の正規表現や空白分割で解析しない。
`argv[0]`のbasenameを`codex`と照合した後、`argv[1:]`を左から1要素ずつ解析する。
`--`はオプション終端を示す1要素として扱い、終端後は目的文を1要素だけ許可する。
終端前のtop-level optionは次の表の引数数で次のNUL要素を消費し、必要な引数が無い場合は停止不能にする。

| 引数の数 | 対象 |
| --- | --- |
| 引数なし | `--strict-config`、`--oss`、`--approve-for-me`、`--dangerously-bypass-approvals-and-sandbox`、`--dangerously-bypass-hook-trust`、`--search`、`--no-alt-screen` |
| 次の1要素 | `-c`・`--config`、`--enable`、`--disable`、`--remote`、`--remote-auth-token-env`、`-m`・`--model`、`--local-provider`、`-p`・`--profile`、`-s`・`--sandbox`、`-C`・`--cd`、`--add-dir`、`-a`・`--ask-for-approval` |
| 次の1要素以上 | `-i`・`--image` |

長い形式の`--name=value`は値を同じNUL要素で指定する形式として扱い、`--remote=値`と`--remote-auth-token-env=値`も拒否する。
`-i`・`--image`は次の1要素以上を画像パスとして消費し、画像指定の後に目的文を渡す場合は`--`を間に配置する。
値を取る短縮alias`-c`、`-m`、`-i`、`-p`、`-s`、`-C`、`-a`は、次のNUL要素へ値を置く形式と、同じNUL要素へ非空値を連結する形式を許可する。
aliasと連結値は既知aliasごとに分離して解析し、値を再分割しない。
通常起動と`resume`の共有optionへ同じ解析を適用する。
`--help`・`-h`と`--version`・`-V`は引数なしであるが対話CLIを開始しないため拒否する。
`--remote`と`--remote-auth-token-env`は引数数を確認した後もremote接続を表すため拒否する。
許可表にないoption、短縮alias単独の引数不足、必要な引数の欠落と余分な要素は停止不能とする。
既知のsubcommand又はaliasを先頭位置の非option要素として検出した場合は拒否する。
拒否対象は次のとおりである。

- `agents`、`exec`、`e`、`review`、`login`、`logout`、`mcp`、`plugin`及び`mcp-server`。
- `app-server`、`remote-control`、`completion`、`update`、`doctor`、`sandbox`、`debug`、`apply`及び`a`。
- `queue`、`archive`、`delete`、`migrate-rollouts`、`unarchive`、`fork`、`cloud`、`exec-server`、`features`及び`help`。
目的文の1要素内に含まれる語はsubcommandとして扱わず、optionの引数として消費した1要素もsubcommandとして再解釈しない。

`argv`の最初の非option要素が`resume`の場合は、`codex resume [OPTIONS] [SESSION] [PROMPT]`へ切り替える。
`resume`専用の引数なしoptionは`--last`、`--all`及び`--include-non-interactive`であり、通常起動と共通するoptionは同じ引数数で消費する。
`resume`のremote optionも通常起動と同じく拒否し、`resume`の後の非option要素は先頭をsession、次を目的文として最大2要素まで受け付ける。
session又は目的文の要素内に含まれるsubcommand名はデータとして扱い、subcommandへ再解釈しない。

Codex直接CLIの契約例では、表の各配列要素を1つのNUL要素として扱う。

| 判定 | argv |
| --- | --- |
| 停止可能 | `["codex"]` |
| 停止可能 | `["codex","--model","o3","--search","inspect"]` |
| 停止可能 | `["codex","--model","app-server"]` |
| 停止可能 | `["codex","--search","app-server is prompt"]` |
| 停止可能 | `["codex","-cmodel=o3","-mo3","-iimage.png","-pprofile","-sread-only","-C/tmp","-aon-request","--","inspect"]` |
| 停止可能 | `["codex","-i","image.png","--","inspect"]` |
| 停止可能 | `["codex","resume","--last"]` |
| 停止可能 | `["codex","resume","--model","o3","session-id","inspect"]` |
| 停止可能 | `["codex","resume","session-id","app-server is prompt"]` |
| 停止可能 | `["codex","resume","-cmodel=o3","-mo3","-iimage.png","-pprofile","-sread-only","-C/tmp","-aon-request","session-id","inspect"]` |
| 停止不能。非対話subcommand | `["codex","exec","inspect"]` |
| 停止不能。共有subcommand | `["codex","app-server"]` |
| 停止不能。remote subcommand | `["codex","remote-control"]` |
| 停止不能。remote option | `["codex","--remote","server"]` |
| 停止不能。remote token option | `["codex","--remote-auth-token-env","TOKEN"]` |
| 停止不能。非対話option | `["codex","--help"]` |
| 停止不能。引数不足 | `["codex","--model"]` |
| 停止不能。短縮aliasの引数不足 | `["codex","-m"]` |
| 停止不能。未知option | `["codex","-zvalue","inspect"]` |
| 停止不能。resume短縮aliasの引数不足 | `["codex","resume","-m"]` |
| 停止不能。resume未知option | `["codex","resume","-zvalue","session-id"]` |

通常形の`argc=1`の`codex`を含め、前記解析に一致するargvだけを停止可能と判定する。
`codex resume [OPTIONS] [SESSION] [PROMPT]`も前記のとおりNUL区切りの要素単位で解析し、optionの引数、session及び目的文の境界を先読みや空白分割で推測しない。
`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`では、通常形に加えて`codex resume --model <値> -c model_reasoning_effort=<値> [session-id]`を再開専用形として扱う。
`resume`以外の既知subcommandが先頭位置にあるargv、`app-server`若しくは`remote-control`がsubcommandであるargv、`--remote`を持つargvは停止不能とする。

全ての読取りが成功し、`pid`と`start`が正の10進整数、`exe_id`が`<10進整数>:<10進整数>`、`exe_basename`と`argv[0]`のbasenameがともに`codex`、`tty`が空でも`?`でもないことを確認する。
前記通常形に一致するargvは停止可能と判定する。
`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`では、`codex --model <値> -c model_reasoning_effort=<値> <目的文>`若しくは前記の再開専用形に一致するargvも停止可能と判定する。
argvはNUL区切りの要素単位で照合し、目的文に現れる語をsubcommandとして扱わない。
読取失敗又は値の不一致も停止不能とする。

`process-feedbacks`の起動時probeが停止可能でも、このスキルは停止要求直前に終了能力probeを新規実行する。
起動時の判定結果を再利用せず、probe未実行、読取失敗又は値の不一致は停止不能とする。

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
