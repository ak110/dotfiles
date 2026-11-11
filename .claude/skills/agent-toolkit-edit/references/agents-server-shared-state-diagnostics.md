# agents_serverの共有状態と読み書きの担当：診断とログ

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 共有状態の共通設計・実行主体・保存と回収の条件を判断する前 | `agents-server-shared-state.md` |
| セッション状態と内部フラグの参照範囲を選ぶ前 | `session-state-and-flags.md` |

## 診断とログ

| 共有状態 | 基準となる保持先 | 読む主体 | 更新できる主体 |
| --- | --- | --- | --- |
| Codexコンパクションの計測記録 | `<状態ディレクトリ>/compaction/<thread_id>.jsonl` | `atk run-script session-review-evidence` | `agents_server`のCodexバックエンド |
| MCPサーバーの起動単位の終了診断（開始、Managerの所属ルート、受信シグナル、stdio切断、停止で中断したturn、正常・例外終了、起動ランチャーが観測した終了状態と標準エラー末尾） | `<診断ログのディレクトリ>/agents-server-instances/<server_instance>.jsonl`（追記専用のJSON Lines形式。1ファイルあたり最大64KiB） | 中断を返すMCPツールの`show`、`atk agents show`、`atk agents wait`（表示時点で`diagnostics.status`を評価する）、MCPサーバー起動時およびSessionEnd時のクリーンアップ処理、終了の経緯を調査する処理 | 該当インスタンスのMCPサーバー（`_agents_server/logging_config.py`）、およびそのサーバーを子プロセスとして起動して終了を監視する`scripts/launch.py`（`process_exited`イベントのみ）。ランチャーは環境変数`AGENT_TOOLKIT_AGENTS_SERVER_LAUNCH_RECORD`で指定された一時ファイルを介してサーバーから出力先パスを受け取る |
| 委譲先CLI自身の診断ログ | `<診断ログのディレクトリ>/delegate-debug/<起動時刻>-<session識別子>-<起動区分>.log` | 初期化失敗などのトラブルを事後調査する処理 | Claudeバックエンド（ファイルの作成、初期化完了後のリネーム、保持世代数を超えた古いログの削除を担当） |
| Antigravityの公開イベントログ | `<状態ディレクトリ>/<ルートsession識別子>/logs/<session_id>.jsonl` | `atk agents logs`、`atk run-script session-review-evidence`、セッションのイベント推移を調査する処理 | Antigravityバックエンド（公開stream-jsonイベントを追記） |
| OpenCodeの公開イベントログ | `<状態ディレクトリ>/<ルートsession識別子>/logs/opencode/<session_id>.jsonl` | `atk agents logs`、`atk run-script session-review-evidence`、推定利用料金の集計処理 | OpenCodeバックエンドおよび共通モジュールの`cli_turn.py`。公開JSONイベントとともに、入力内容や要求モデルを示す`agent_toolkit_input`を追記する。要求モデルの情報は、実際の観測モデル名とは区別して記録する |

| 対象 | モジュール |
| --- | --- |
| 開始・終了時の稼働数やホストリソースの診断記録 | `resource_snapshot.py` |

診断ログの出力先ディレクトリは`agents-server.log`が配置される階層とし、`agent-toolkit/agent_toolkit/_agents_server/logging_config.py`の`state_dir`がこれを解決する。
AntigravityやOpenCodeのイベント処理を調査する際は、前述の表に記載された公開イベントログを確認する。
書き込み失敗が発生している場合はバックエンドの警告ログを参照し、イベント処理の最終状態はセッション状態から判断する。

リソース記録の処理は、セッションの開始時および終了時に同一状態ディレクトリ内のすべてのルートを走査し、一覧表示と同様の基準で重複セッションや失効した状態ファイルを除外する。
自プロセスが所有するセッションについては、最新のメモリー状態を優先して上書きする。
記録は既存の`agents-server.log`に保存される。
なお、このリソース集計は非アトミックな観測処理であるため、読み取りに失敗した場合はデータの完全性欠損として記録する。
ロードアベレージや利用可能メモリー量はホスト全体の値であり、セッション個別のリソース消費量とは明確に区別して扱う。

`agents-server-shared-state.md`「判定を確定する前に確認すること」の共有領域の掃引後に、次のログ回収を行う。

続いて、インスタンスごとの終了診断ログを回収する。
参照元となるのは、掃引処理後に残っている登録簿内の`server_instance`・`interrupted_turns`、および各ルートの終了結果ファイル内の`error.diagnostics.server_instance`である。
これらの参照元が存在しない7日以上経過したログファイルを削除する。
なお、ログディレクトリ全体の合計サイズが64MiBを超える場合は、参照元が存在しないログファイルを対象に、更新日時が古いものから順に7日未満であっても前倒しで削除する。
有効な参照元が存在するログファイルや、現在稼働しているサーバー自身のインスタンスログは削除しない。
