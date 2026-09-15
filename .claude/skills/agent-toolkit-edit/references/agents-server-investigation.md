# agents_serverの委譲不具合の調査手順

`agents_server`が起動した委譲先が動かないとき、どの記録を読めば事実を確定できるかを本書が保持する。
委譲先は呼び出し元とは別プロセスのCLIであり、その内部状態を呼び出し元から直接観測できないため、観測手段は実装と実行環境の複数箇所へ分散する。
共有状態ごとの正本と読み書き経路は`references/agents-server-shared-state.md`が扱い、本書は当該状態を外部から観測する手順を扱う。

## 観測できる記録の所在

状態ディレクトリの絶対パスは`agent-toolkit/agent_toolkit/_agents_server/logging_config.py`の`state_dir`が解決する。
同関数は`platformdirs`の`user_state_dir("agent-toolkit", appauthor=False)`が返す値を用いる。
Claude Codeの作業ディレクトリのスラッグは、当該ディレクトリの絶対パスのうちパス区切りと記号をハイフンへ置換した文字列である。

| 記録 | 所在の組み立て方 | 読み取れる事実 |
| --- | --- | --- |
| `agents_server`の診断ログ | 状態ディレクトリ直下の`agents-server.log`。`RotatingFileHandler`が世代管理する | 起動ごとの`engine`、`launch_kind`、`model_type`、初期化の成否と再試行、sessionの状態遷移 |
| 委譲先のCLIの診断ログ | 状態ディレクトリ直下の`delegate-debug`配下。ファイル名はUTC時刻、プロセス識別子、`launch_kind`をハイフンで連ねた`.log`。保持世代を超えた記録は次の起動時に削除される | SessionStart hookの完了、MCPサーバーの接続、機能フラグの取得、skillsの送信、`[engine] turn 1 start`への到達、セッション間メッセージの保留 |
| Claude Codeが委譲先ごとに残すMCPサーバー接続ログ | Claude CLIのキャッシュディレクトリ配下の`<作業ディレクトリのスラッグ>/mcp-logs-<サーバー名>/<起動時刻>.jsonl`。Windowsでは`%LOCALAPPDATA%\claude-cli-nodejs\Cache`が当該キャッシュディレクトリとなる | 委譲先が起動した各MCPサーバーの接続完了時刻と接続の失敗 |
| 委譲先のセッションのトランスクリプト | `~/.claude/projects/<作業ディレクトリのスラッグ>/<session識別子>.jsonl` | 委譲先が受け取った指示と返した応答。初期化を完了しなかった委譲先は当該ファイルを作成しないため、不在そのものが初期化未到達の証拠になる |
| 実行ホスト上のセッション登録簿 | `~/.claude/sessions/<プロセス識別子>.json` | `sessionId`、`cwd`、`kind`、`entrypoint`、メッセージング用の経路名。委譲先も当該登録簿へ登録される |

## 切り分けの順序

1. `agents_server`の診断ログで、対象の起動が実際に使った`engine`と`launch_kind`を確認する。症状から推定したengineを前提にしない。
2. 委譲先のCLIの診断ログを、成功する起動と失敗する起動の双方で採取して突き合わせる。どちらか一方だけでは、失敗した起動が止まった位置を確定できない。
3. 差が現れた位置を、SDKのオプションと環境を外部プロセスで再現して切り分ける。

## 外部プロセスでの再現

`agent-toolkit/agent_toolkit/_agents_server/claude.py`の`_build_options`が組む`ClaudeAgentOptions`をそのまま用いる。
当該オプションで`ClaudeSDKClient`を構成し、`connect()`、`query()`、`receive_messages()`の順に呼ぶ。
`query()`を省いた再現は初期化へ到達しないため、当該省略による失敗を対象の不具合と取り違える。
委譲先のCLIの診断を採取する場合は、`ClaudeAgentOptions.extra_args`へ`debug-file`と保存先の絶対パスを与える。

## 調査で誤りやすい前提

- MCPサーバーのコードを変更しても、当該MCPサーバーを再接続するまで稼働中のプロセスへ反映されない。再接続を要さずに条件を変えるには、起動のたびに読む外部ファイルから条件を取る形にする。
- `start_explore`が成功したことは、claudeの委譲先が動作する根拠にならない。工程別モデル設定によってはcodexが選ばれるため、`agents_server`の診断ログの`engine`で確認する。
- 委譲先のCLIは`--debug`を与えても標準エラーへ何も書かない。診断は`--debug-file`で採取する。
