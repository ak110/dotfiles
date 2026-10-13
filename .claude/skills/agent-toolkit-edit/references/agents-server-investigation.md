# agents_serverの委譲不具合の調査手順

`agents_server`が起動した委譲先が動かないとき、どの記録を読めば事実を確定できるかを本書が保持する。
委譲先は別プロセスのCLIで内部状態を委譲元から直接観測できないため、観測手段は実装と実行環境の複数箇所へ分散する。
共有状態ごとの保存先と読み書きの担当は`references/agents-server-shared-state.md`が扱う。

## 観測できる記録の所在

診断ログのディレクトリは`agent-toolkit/agent_toolkit/_common/state_paths.py`の`state_dir`が解決し、`atk config get state_dir`の出力と一致する。
Linuxは`platformdirs`の`user_state_dir("agent-toolkit", appauthor=False)`を使い、相対の`XDG_STATE_HOME`は`HOME/.local/state`へ退避する。Windowsは`LOCALAPPDATA`配下の`agent-toolkit`を使う。
Claude Codeの作業ディレクトリのスラッグは、絶対パスのパス区切りと記号をハイフンへ置換した文字列である。
`agents_server`の診断ログの書き手は次のとおり。

- MCPサーバーの起動・終了とinitializeの節目: `_agents_server/mcp_tools.py`と`mcp_transport.py`
- 候補ごとの初期化と候補の切替: `manager.py`。Codexの`explore`で読み取り専用sandboxが名前空間を作成できなかった候補は、モデルを呼ぶ前に除外理由`exploreSandboxNamespaceDenied`で警告を残し、`unavailable-candidates.json`へ記録して次候補へ進む
- 開始後の保存失敗（登録簿、待機対象、状態ファイル）: `manager.py`と`state.py`。予約した保存とheartbeatの失敗は`status_file.py`と`manager_base.py`が、書込主体の状態ファイルとsession識別子の一覧とともに記録する。いずれもスタックを含み、置換元と置換先のパスは例外の`add_note`による注記にある。共通の置換（`_common/atomic_file.py`）はWindowsのWinError 5・32を1秒未満の有限回で再試行し、上限後と対象外のエラーでは旧本文を残して失敗を返す
- sessionの状態遷移（`session_transition`）: `manager.py`・`manager_registry.py`・`manager_resume.py`・`state.py`
- 開始・終端の資源記録（`resource_snapshot`）: `resource_snapshot.py`

| 記録 | 所在 | 読み取れる事実 |
| --- | --- | --- |
| `agents_server`の診断ログ | 診断ログのディレクトリ直下の`agents-server.log`（`RotatingFileHandler`が世代管理） | 起動ごとの`engine`、`launch_kind`、`model_type`、初期化の成否と再試行、sessionの状態遷移、開始・終端時の稼働数とホスト資源 |
| 委譲先のCLIの診断ログ | 診断ログのディレクトリ直下の`delegate-debug`配下。名前はUTC時刻、session識別子、`launch_kind`をハイフンで連ねた`.log`（session識別子は初期化完了時に入るため、未到達の起動はその部分を持たない）。保持世代を超えた記録は次の起動時に削除される | SessionStart hookの完了、MCPサーバーの接続、機能フラグの取得、skillsの送信、`[engine] turn 1 start`への到達、セッション間メッセージの保留 |
| Claude Codeが委譲先ごとに残すMCPサーバー接続ログ | Claude CLIのキャッシュディレクトリ配下の`<作業ディレクトリのスラッグ>/mcp-logs-<サーバー名>/<起動時刻>.jsonl`（Windowsのキャッシュは`%LOCALAPPDATA%\claude-cli-nodejs\Cache`） | 各MCPサーバーの接続完了時刻と接続の失敗 |
| 委譲先のセッションのトランスクリプト | `~/.claude/projects/<作業ディレクトリのスラッグ>/<session識別子>.jsonl` | 委譲先が受け取った指示と返した応答。初期化を完了しなかった委譲先はこのファイルを作成しないため、不在が初期化未到達の証拠になる |
| Claude CodeのMCP接続失敗の記録 | 設定ディレクトリ直下の`mcp-needs-auth-cache.json` | プラグインのstdioサーバーの接続失敗と時刻。記録がある間（15分）は同じホストの全Claude Codeプロセスが同じ設定のサーバーへ接続せず、デバッグログにも接続試行が現れない |
| `atk agents wait`の配送済みrun | 状態ディレクトリの`agents-server/<ルートsession識別子>/wait-results/<書込主体>/<run_id>.json`。待機対象0件（終了コード10）と`consumed`（終了コード8）の診断が`run_path`として示す | 先行waitが配送した終端結果本文（`output`のJSON Lines）と対象session。配送済みはエージェントの読了を意味しない。`atk read-file -- <run_path>`で読む |
| 実行ホスト上のセッション登録簿 | `~/.claude/sessions/<プロセス識別子>.json` | `sessionId`、`cwd`、`kind`、`entrypoint`、メッセージ送受信用の識別名（委譲先も登録される） |

## 切り分けの順序

通常の公開応答では活動の経過を`seconds_since_activity`で確認し、絶対時刻と実行条件を比べる調査にはMCPの`show(verbose=True)`を使う。API失敗の公開診断は種別、HTTP状態、経過時間で、初期化診断は受信したメッセージの種別とその位置の失敗情報を示す。

原因調査の前に`agent-toolkit:bugfix`を起動し、`agent-toolkit/skills/bugfix/SKILL.md`「直接的原因と事象時点の確定」に従って、実際の起動条件と成功・失敗を比べ、発生位置を再現する。その入力には本書の診断ログの`engine`・`launch_kind`と委譲先CLIの診断を使い、SDKと環境をそろえる再現には「外部プロセスでの再現」の起動形を使う。

## 資源記録の読み方

- resource_snapshotは時刻、root/session識別子、turn番号で状態遷移へ対応させる
- running_totalとrunning_by_rootは同じ状態ディレクトリで観測したrunningの数で、待機中と孫sessionも含む。開始は自身を含み、終端は自身を除く。稼働中の追送では開始記録を増やさない
- counts_completeがfalseなら集計は不完全で、確定値はnull、読めた範囲はobserved_running_*に入る。欠損理由はstate_read_failuresとunavailable_fieldsにある
- load_average_1_5_15とavailable_memory_bytesはホスト全体の値で、Windowsのloadは未取得として理由を残す。非原子的な観測であり、個々のsessionの寄与や因果は同条件の比較なしに確定しない
- resource_snapshot_failedは計測自体の失敗で、session処理の失敗とは別である。ログは2 MiB・バックアップ3世代で回転する

## 外部プロセスでの再現

`agent-toolkit/agent_toolkit/_agents_server/claude.py`の`_build_options`が組む`ClaudeAgentOptions`で`ClaudeSDKClient`を構成する。続けて`connect()`、`query()`、`receive_messages()`の順に呼ぶ。`query()`を省くと初期化へ到達せず、その失敗を対象の不具合と取り違える。
委譲先のCLIの診断は`ClaudeAgentOptions.extra_args`へ`debug-file`と保存先の絶対パスを与えて採取する。

## 調査で誤りやすい前提

- MCPサーバーのコードの変更は、そのMCPサーバーを再接続した時点で反映される。再接続なしに条件を変えるには、起動のたびに読む外部ファイルから条件を取る形にする
- `start`の応答の`persistence_warnings`は保存の失敗を示す。開始の成否は`status`と`excluded_candidates`も確認し、全候補が除外された場合は返されたsessionを利用不能として扱う。正常に開始したsessionでは、同じ`session_id`で`show`・`send_message`・`atk agents wait`を使い、保存の失敗原因は診断ログで確かめる。保存失敗だけを理由に新しい委譲先を起動しない
- claudeの委譲先が動く根拠は診断ログの`engine`から得る。`start`の`explore`の成功は、工程別モデル設定によってはcodexが選ばれるため根拠にならない
- 委譲先のCLIは`--debug`を与えても標準エラーへ何も書かない。診断は`--debug-file`で採取する
- 起動が`委譲先の作業ディレクトリでプラグインの起動コマンドが失敗した`で拒否された場合、原因は委譲先の作業ディレクトリで`uv`・`uvx`が動かないことにある。例外の`cwd`と標準エラーを読み、未trustのmise設定なら委譲元の手順（`agent-toolkit/skills/process-wi/references/selection-procedure.md`など）でtrustを準備する。それより前に起動した委譲先の接続失敗は、上表の接続失敗の記録を確認する
