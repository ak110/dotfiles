# agents_serverの委譲不具合の調査手順

`agents_server`が起動した委譲先が動かないとき、どの記録を読めば事実を確定できるかを本書が保持する。
委譲先は呼び出し元とは別プロセスのCLIであり、その内部状態を呼び出し元から直接観測できないため、観測手段は実装と実行環境の複数箇所へ分散する。
共有状態ごとの基準となる保存先と読み書きの担当は`references/agents-server-shared-state.md`が扱い、本書はその状態を外部から観測する手順を扱う。

## 観測できる記録の所在

診断ログのディレクトリの絶対パスは`agent-toolkit/agent_toolkit/_agents_server/logging_config.py`の`state_dir`が解決する。
同関数は`platformdirs`の`user_state_dir("agent-toolkit", appauthor=False)`が返す値を用いる。
Claude Codeの作業ディレクトリのスラッグは、そのディレクトリの絶対パスのうちパス区切りと記号をハイフンへ置換した文字列である。

| 記録 | 所在の組み立て方 | 読み取れる事実 |
| --- | --- | --- |
| `agents_server`の診断ログ | 診断ログのディレクトリ直下の`agents-server.log`。`RotatingFileHandler`が世代管理する | 起動ごとの`engine`、`launch_kind`、`model_type`、初期化の成否と再試行、sessionの状態遷移 |
| 委譲先のCLIの診断ログ | 診断ログのディレクトリ直下の`delegate-debug`配下。ファイル名はUTC時刻、session識別子、`launch_kind`をハイフンで連ねた`.log`。session識別子は初期化の完了時に名前へ入るため、初期化へ到達しなかった起動の記録はその部分を持たない。保持世代を超えた記録は次の起動時に削除される | SessionStart hookの完了、MCPサーバーの接続、機能フラグの取得、skillsの送信、`[engine] turn 1 start`への到達、セッション間メッセージの保留 |
| Claude Codeが委譲先ごとに残すMCPサーバー接続ログ | Claude CLIのキャッシュディレクトリ配下の`<作業ディレクトリのスラッグ>/mcp-logs-<サーバー名>/<起動時刻>.jsonl`。Windowsでは`%LOCALAPPDATA%\claude-cli-nodejs\Cache`がそのキャッシュディレクトリとなる | 委譲先が起動した各MCPサーバーの接続完了時刻と接続の失敗 |
| 委譲先のセッションのトランスクリプト | `~/.claude/projects/<作業ディレクトリのスラッグ>/<session識別子>.jsonl` | 委譲先が受け取った指示と返した応答。初期化を完了しなかった委譲先はこのファイルを作成しないため、不在そのものが初期化未到達の証拠になる |
| Claude CodeのMCP接続失敗の記録 | 設定ディレクトリ直下の`mcp-needs-auth-cache.json` | プラグインのstdioサーバーの接続失敗とその時刻。記録がある間（15分）は、同じホストの全Claude Codeプロセスが同じ設定のサーバーへ接続せず、デバッグログにも接続試行が現れない |
| 実行ホスト上のセッション登録簿 | `~/.claude/sessions/<プロセス識別子>.json` | `sessionId`、`cwd`、`kind`、`entrypoint`、メッセージ送受信用の識別名。委譲先もこの登録簿へ登録される |

## 切り分けの順序

1. `agents_server`の診断ログで、対象の起動が実際に使った`engine`と`launch_kind`を確認する。前提に置くのはこの診断ログの値とし、症状からの推定はその代わりから外す。
2. 委譲先のCLIの診断ログを、成功する起動と失敗する起動の双方で採取して比べる。失敗した起動が止まった位置は、この2つの差分から確定する。
3. 差が現れた位置を、SDKのオプションと環境を外部プロセスで再現して特定する。

## 外部プロセスでの再現

`agent-toolkit/agent_toolkit/_agents_server/claude.py`の`_build_options`が組む`ClaudeAgentOptions`をそのまま用いる。
そのオプションで`ClaudeSDKClient`を構成し、`connect()`、`query()`、`receive_messages()`の順に呼ぶ。
`query()`を省いた再現は初期化へ到達しないため、その省略による失敗を対象の不具合と取り違える。
委譲先のCLIの診断を採取する場合は、`ClaudeAgentOptions.extra_args`へ`debug-file`と保存先の絶対パスを与える。

## 調査で誤りやすい前提

- MCPサーバーのコードの変更は、そのMCPサーバーを再接続した時点で稼働中のプロセスへ反映される。再接続を要さずに条件を変えるには、起動のたびに読む外部ファイルから条件を取る形にする。
- claudeの委譲先が動作する根拠は、`agents_server`の診断ログの`engine`から得る。`start_explore`の成功は工程別モデル設定によってはcodexが選ばれるため、その根拠から外す。
- 委譲先のCLIは`--debug`を与えても標準エラーへ何も書かない。診断は`--debug-file`で採取する。
- 起動が`委譲先の作業ディレクトリでプラグインの起動コマンドが失敗した`で拒否された場合、原因は委譲先の作業ディレクトリで`uv`・`uvx`が動かないことにある。例外の`cwd`と標準エラーを読み、未trustのmise設定なら呼び出し元の手順（`agent-toolkit/share/pick-wi.parent.md`など）でtrustを準備する。この事前確認より前に起動した委譲先で接続失敗が起きた場合は、上表の接続失敗の記録を確認する。
