# agents_serverの委譲不具合の調査手順

`agents_server`が起動した委譲先が動かないとき、どの記録を読めば事実を確定できるかを本書が保持する。
委譲先は委譲元とは別プロセスのCLIであり、その内部状態を委譲元から直接観測できないため、観測手段は実装と実行環境の複数箇所へ分散する。
共有状態ごとの基準となる保存先と読み書きの担当は`references/agents-server-shared-state.md`が扱い、本書はその状態を外部から観測する手順を扱う。

## 観測できる記録の所在

診断ログのディレクトリの絶対パスは`agent-toolkit/agent_toolkit/_common/state_paths.py`の`state_dir`が解決し、`atk config get state_dir`の出力と同じ値になる。
同関数はLinuxでは`platformdirs`の`user_state_dir("agent-toolkit", appauthor=False)`を使い、相対の`XDG_STATE_HOME`は`HOME/.local/state`へ退避する。
Windowsでは`LOCALAPPDATA`配下の`agent-toolkit`を使う。
Claude Codeの作業ディレクトリのスラッグは、そのディレクトリの絶対パスのうちパス区切りと記号をハイフンへ置換した文字列である。
`agents_server`の診断ログのうち、MCPサーバーの起動・終了とinitializeの節目は`_agents_server/mcp_tools.py`と`mcp_transport.py`が書く。
候補ごとの初期化の開始・完了・再試行と候補の切替は`_agents_server/manager.py`が書く。
sessionの状態遷移（`session_transition`）は`manager.py`、`manager_registry.py`、`manager_resume.py`と`state.py`が書く。
開始・終端の資源記録（`resource_snapshot`）は共通の状態遷移から`resource_snapshot.py`が書く。

| 記録 | 所在の組み立て方 | 読み取れる事実 |
| --- | --- | --- |
| `agents_server`の診断ログ | 診断ログのディレクトリ直下の`agents-server.log`。`RotatingFileHandler`が世代管理する | 起動ごとの`engine`、`launch_kind`、`model_type`、初期化の成否と再試行、sessionの状態遷移、開始・終端時の稼働数とホスト資源 |
| 委譲先のCLIの診断ログ | 診断ログのディレクトリ直下の`delegate-debug`配下。ファイル名はUTC時刻、session識別子、`launch_kind`をハイフンで連ねた`.log`。session識別子は初期化の完了時に名前へ入るため、初期化へ到達しなかった起動の記録はその部分を持たない。保持世代を超えた記録は次の起動時に削除される | SessionStart hookの完了、MCPサーバーの接続、機能フラグの取得、skillsの送信、`[engine] turn 1 start`への到達、セッション間メッセージの保留 |
| Claude Codeが委譲先ごとに残すMCPサーバー接続ログ | Claude CLIのキャッシュディレクトリ配下の`<作業ディレクトリのスラッグ>/mcp-logs-<サーバー名>/<起動時刻>.jsonl`。Windowsでは`%LOCALAPPDATA%\claude-cli-nodejs\Cache`がそのキャッシュディレクトリとなる | 委譲先が起動した各MCPサーバーの接続完了時刻と接続の失敗 |
| 委譲先のセッションのトランスクリプト | `~/.claude/projects/<作業ディレクトリのスラッグ>/<session識別子>.jsonl` | 委譲先が受け取った指示と返した応答。初期化を完了しなかった委譲先はこのファイルを作成しないため、不在そのものが初期化未到達の証拠になる |
| Claude CodeのMCP接続失敗の記録 | 設定ディレクトリ直下の`mcp-needs-auth-cache.json` | プラグインのstdioサーバーの接続失敗とその時刻。記録がある間（15分）は、同じホストの全Claude Codeプロセスが同じ設定のサーバーへ接続せず、デバッグログにも接続試行が現れない |
| 実行ホスト上のセッション登録簿 | `~/.claude/sessions/<プロセス識別子>.json` | `sessionId`、`cwd`、`kind`、`entrypoint`、メッセージ送受信用の識別名。委譲先もこの登録簿へ登録される |

## 切り分けの順序

通常の公開応答では活動の経過を`seconds_since_activity`で確認する。絶対時刻と実行条件を比較する調査にはMCPの`show(verbose=True)`を使う。API失敗の公開診断は種別、HTTP状態、経過時間であり、集計回数と初回時刻は内部に保持する。初期化診断には、受信したメッセージの種別と、その位置に対応する失敗情報を示す。

原因調査の前に`agent-toolkit:bugfix`を起動する。`agent-toolkit/skills/bugfix/SKILL.md`「直接的原因と事象時点の確定」に従い、実際の起動条件と成功・失敗を比べ、発生位置を再現する。
本書の診断ログの`engine`・`launch_kind`と、委譲先CLIの診断をその入力に使う。SDKと環境をそろえる再現は「外部プロセスでの再現」の起動形を使う。

## 資源記録の読み方

資源との関係を調べるときは、resource_snapshotの時刻、root/session識別子とturn番号を状態遷移へ対応させる。
running_totalとrunning_by_rootは同じ状態ディレクトリで観測したrunningの数であり、待機中と孫sessionも含む。
発生元の最新状態を反映するため、開始は自身を含み、終端は自身を除く。稼働中の追送では開始記録を増やさない。
counts_completeがfalseなら集計は不完全で、確定値はnull、読めた範囲はobserved_running_*に入る。state_read_failuresとunavailable_fieldsで欠損の理由を確認する。
load_average_1_5_15はホスト全体の1・5・15分load、available_memory_bytesはホストの利用可能メモリーである。Windowsのloadは未取得として理由を残す。
これらは非原子的な観測であり、agents_serverや個々のsessionのCPU・メモリー寄与は確定しない。因果関係の判断には同条件の比較が必要である。
resource_snapshot_failedは計測自体の失敗を表し、session処理の失敗とは区別する。ログは既存の2 MiB・バックアップ3世代で回転する。

## 外部プロセスでの再現

`agent-toolkit/agent_toolkit/_agents_server/claude.py`の`_build_options`が組む`ClaudeAgentOptions`をそのまま用いる。
そのオプションで`ClaudeSDKClient`を構成し、`connect()`、`query()`、`receive_messages()`の順に呼ぶ。
`query()`を省いた再現は初期化へ到達しないため、その省略による失敗を対象の不具合と取り違える。
委譲先のCLIの診断を採取する場合は、`ClaudeAgentOptions.extra_args`へ`debug-file`と保存先の絶対パスを与える。

## 調査で誤りやすい前提

- MCPサーバーのコードの変更は、そのMCPサーバーを再接続した時点で稼働中のプロセスへ反映される。再接続を要さずに条件を変えるには、起動のたびに読む外部ファイルから条件を取る形にする。
- claudeの委譲先が動作する根拠は、`agents_server`の診断ログの`engine`から得る。`start`の`explore`の成功は工程別モデル設定によってはcodexが選ばれるため、その根拠から外す。
- 委譲先のCLIは`--debug`を与えても標準エラーへ何も書かない。診断は`--debug-file`で採取する。
- 起動が`委譲先の作業ディレクトリでプラグインの起動コマンドが失敗した`で拒否された場合、原因は委譲先の作業ディレクトリで`uv`・`uvx`が動かないことにある。例外の`cwd`と標準エラーを読み、未trustのmise設定なら委譲元の手順（`agent-toolkit/skills/process-wi/references/selection-procedure.md`など）でtrustを準備する。この事前確認より前に起動した委譲先で接続失敗が起きた場合は、上表の接続失敗の記録を確認する。
