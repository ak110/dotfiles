# agents_serverの委譲不具合の調査手順

本書は、`agents_server`が起動した委譲先が動作しない場合に、どの記録を確認して事実関係を特定すべきかをまとめた文書である。
委譲先は別プロセスのCLIとして起動するため、委譲元が内部状態を知る手段は記録と診断に限られる。そのため、観測手段は実装コードや実行環境の複数箇所に分散している。
共有状態ごとの保存場所や読み書きの担当モジュールについては、`references/agents-server-shared-state.md`を参照する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agents-server-shared-state.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/agents-server-shared-state.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 原因調査で発生位置と直接的原因を確定する前 | `agent-basics:bugfix/references/root-cause-analysis.md#「直接的原因と事象時点の確定」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「直接的原因と事象時点の確定」` |

## 観測できる記録の所在

診断ログが保存されるディレクトリは、`agent-toolkit/agent_toolkit/_common/state_paths.py`の`state_dir`によって解決され、`atk config get state_dir`の出力と一致する。
Linux環境では`platformdirs`の`user_state_dir("agent-toolkit", appauthor=False)`を使用し、相対パスの`XDG_STATE_HOME`は`HOME/.local/state`へ退避する。Windows環境では`LOCALAPPDATA`配下の`agent-toolkit`を使用する。
Claude Codeにおける作業ディレクトリのスラッグは、絶対パスの区切り文字や記号をハイフンへ置換した文字列である。

`agents_server`の診断ログを出力する各モジュールは以下のとおり。

- MCPサーバーの起動・終了や初期化（initialize）の節目: `_agents_server/mcp_tools.py`および`mcp_transport.py`
- 起動単位ごとの終了診断: サーバー自身のイベントは`_agents_server/logging_config.py`（呼び出し元は`mcp_tools.py`・`mcp_transport.py`・`manager_registry.py`）。子プロセスの終了状態や標準エラー末尾のログは`scripts/launch.py`
- 候補ごとの初期化と候補の切り替え: `manager.py`。全バックエンドの探索（explore）には通常のコマンド実行を用いる。起動応答の`excluded_candidates`や警告ログから、CLIのインストール状況、認証、モデルの可用性に関する失敗原因を調べる。Codexの開始・再開時には、スキルを配信しないためのplugins設定と、配布定義から直接配信するpyfltr MCP設定を確認する。専用サンドボックスのprobeは使用しない
- 起動後の保存失敗（agents_serverのセッション登録簿`<状態ディレクトリ>/agents-server/sessions/<session_id>.json`、待機対象、状態ファイルなど）: `manager.py`と`state.py`。予約保存やheartbeatの失敗は、書き込み主体の状態ファイルやセッション識別子の一覧とともに`status_file.py`および`manager_base.py`が記録する。いずれもスタックトレースを含み、置換元・置換先のパスは例外オブジェクトの`add_note`による注記に記載される。共通のアトミック置換処理（`_common/atomic_file.py`）では、Windows環境特有のWinError 5や32に対して1秒未満の有限回数でリトライし、上限到達後や対象外のエラーでは既存の内容を保ったままエラーを返す
- セッションの状態遷移（`session_transition`）: `manager.py`・`manager_registry.py`・`manager_resume.py`・`state.py`
- 開始時および終了時のリソース記録（`resource_snapshot`）: `resource_snapshot.py`
- AntigravityおよびOpenCodeの子プロセス管理・イベント保存・終了処理: `cli_turn.py`。通信フォーマット（wire形式）の解釈は`antigravity.py`と`opencode.py`が担当する。OpenCodeの起動には`opencode run --format json --auto --model <provider/model> --variant <effort>`を用い、セッション継続時には`--session`を渡す。起動候補として指定したモデルと、イベントログから実際に観測されたモデル識別子は区別して扱う

| 記録 | 所在 | 読み取れる事実 |
| --- | --- | --- |
| `agents_server`の診断ログ | 診断ログのディレクトリ直下の`agents-server.log`（`RotatingFileHandler`が世代管理） | 起動ごとの`engine`、`launch_kind`、`model_type`、初期化の成否とリトライ、セッションの状態遷移、開始・終了時の稼働数とホストリソース |
| 起動単位の終了診断 | 診断ログのディレクトリ直下の`agents-server-instances/<server_instance>.jsonl`。中断エラーを返す`show`・`atk agents show`・`atk agents wait`の`error.diagnostics.path`や`interrupted_turns[].diagnostics.path`が同じファイルを指す。参照元のセッション登録簿と終了結果が残っている間は保持される | 起動時の`pid`・`cwd`・親CLIの接続ログ候補（`parent_connection_log`）、所属ルートと書き込み主体、受信シグナル、標準入力の終端、プロセス停止により中断したセッションとターン、正常・例外終了の別、ランチャーが観測した終了コード・シグナル・標準エラー末尾。`diagnostics.status`の`incomplete`はサーバーとランチャーの双方が記録を完了できずに終了したことを示し、`not_collected`は記録を開始する前に中断したことを示す。所在を報告する前にクラッシュした起動ではこのファイルが作成されず、ランチャーが親CLIの接続ログに残す標準エラー1行のみが手がかりとなる。`parent_connection_log`が理由コード（`unsupported_parent`または`not_found`）を返す起動では親の接続ログを特定できない |
| 委譲先CLIの診断ログ | 診断ログのディレクトリ直下の`delegate-debug`配下。名前はUTC時刻、セッション識別子、`launch_kind`をハイフンで連ねた`.log`（セッション識別子は初期化完了時に付与されるため、初期化に失敗した起動では含まれない）。世代保持の上限を超えたログは次回の起動時に自動削除される | SessionStartフックの完了、MCPサーバーへの接続、機能フラグの取得、skillsの送信、`[engine] turn 1 start`への到達、セッション間メッセージの保留状態 |
| Claude Codeが委譲先ごとに残すMCPサーバー接続ログ | Claude CLIのキャッシュディレクトリ配下の`<作業ディレクトリのスラッグ>/mcp-logs-<サーバー名>/<起動時刻>.jsonl`（Windows環境のキャッシュパスは`%LOCALAPPDATA%\claude-cli-nodejs\Cache`） | 各MCPサーバーへの接続完了時刻と接続失敗の記録 |
| 委譲先セッションのトランスクリプト | `~/.claude/projects/<作業ディレクトリのスラッグ>/<session識別子>.jsonl` | 委譲先が受信した指示と返した応答内容。初期化を完了しなかった委譲先はこのファイルを作成しないため、ファイルが存在しないこと自体が初期化未到達の証拠となる |
| Antigravity・OpenCodeの公開イベントログ | 状態ディレクトリ配下の`agents-server/<ルートsession識別子>/logs/<session_id>.jsonl`（Antigravity用）および`logs/opencode/<session_id>.jsonl`（OpenCode用） | 開始、メッセージ本文、ツール呼び出し、トークン使用量、エラー発生などのイベント。OpenCodeの`agent_toolkit_input`は渡した入力と要求モデルの補助記録であり、実モデルの観測を保証するものではない。閲覧には`atk agents logs`を使用する |
| Claude CodeのMCP接続失敗の記録 | 設定ディレクトリ直下の`mcp-needs-auth-cache.json` | プラグインのstdioサーバーへの接続失敗履歴と発生時刻。プロセスの新規起動や再開ごとに公開再接続APIを1回試行し、回復しなかった失敗のみがstart/showの警告として記録される |
| `atk agents wait`の配信済みrun記録 | 状態ディレクトリ配下の`agents-server/<ルートsession識別子>/wait-results/<書込主体>/<run_id>.json`。待機対象が0件の場合（終了コード10）や処理済みの場合（`consumed`、終了コード8）の診断情報が`run_path`として指し示す | 先行するwaitが配信した終了行（`output`のJSON Lines。本文自体は除かれ、`agent_message_path`が本文ファイルを指す）および対象セッション。配信済みであってもエージェントが読み終えたとは限らない。`jq -r .output -- <run_path>`で終了行を読み、メッセージ本文は`agent_message_path`が指すファイルから読む |
| 実行ホスト上のセッション登録簿 | `~/.claude/sessions/<プロセス識別子>.json` | `sessionId`、`cwd`、`kind`、`entrypoint`、メッセージ送受信用の識別名（委譲先プロセスも登録される） |

## 切り分けの順序

Claude接続に関する候補の試行・選択・失敗を追うには、ログイベントの`claude_connection_attempt`・`claude_connection_selected`・`claude_connection_failed`を確認し、APIの切り替えは`claude_api_switch`を確認する。MCPサーバーの再接続成否は`claude_mcp_recovered`と`claude_mcp_recovery_failed`で確認できる。
MCP再接続に関する監査記録は、`docs/development/audit-records.md`内の「docs/development/design-agents-runtime.md：MCP接続失敗の再接続：2026年10月20日」に記載されている。

中断（`interrupted`）の原因を調査する際は、API応答の`diagnostics.next_action`に示された行範囲（`sed -n '<開始行>,<終了行>p' -- <絶対パス>`）で終了診断ログを読み出す。受信シグナル、標準入力の切断、終了ステータスの有無からサーバーがどの契機で停止したかを特定する。親プロセス側が終了を開始した直接的な理由は親プロセス側の記録にのみ残る。そのため、`parent_connection_log.path`が示す親CLIの接続ログから、終了イベント（`exited`・`signal_received`・`process_exited`）の発生時刻（`at`）と同時刻の記録を突き合わせて調査する。

`parent_connection_log`が`path`を返さず`reason`を返している場合、親プロセスの接続ログの所在は特定できない。理由コードが`unsupported_parent`の場合は、環境変数`CLAUDECODE`が`1`以外の環境（親プロセスがClaude Code以外）で起動されたことを示す。`not_found`の場合は、Claude CLIのキャッシュディレクトリが解決できないか、該当する作業ディレクトリのスラッグ配下に起動時刻以前の`mcp-logs-*agents*server*`ログが存在しないことを示す。どちらの場合も、親プロセス側の記録を別の方法で確認する必要がある。

サーバーが診断ファイルの保存場所を報告する前にクラッシュした場合（依存パッケージの解決失敗や起動直後のエラーなど）は、診断ファイルもセッション登録簿の`server_instance`も作成されない。この場合の手がかりは、子プロセスの異常終了時にランチャーが標準エラーへ出力する1行のメッセージ（診断ファイルが未作成である旨と推奨操作を含む）のみであり、親CLIの接続ログに記録される。なお、終了コード0で正常終了した場合（`--check-dependencies`などの単発実行）にはこのメッセージは出力されない。

通常運用の応答では、`seconds_since_activity`によって最後の活動からの経過時間を確認できる。詳細な絶対時刻や実行パラメーターを突き合わせる調査には、MCPツールの`show(verbose=True)`を使用する。API呼び出しの失敗に関する公開診断情報にはエラー種別、HTTPステータスコード、経過時間が含まれ、初期化診断には受信したメッセージの種別や失敗箇所の詳細が示される。

原因調査に着手する前には`agent-basics:bugfix`を起動し、`agent-basics:bugfix/references/root-cause-analysis.md`「直接的原因と事象時点の確定」に従って、実際の起動パラメーターと成否を比較しながら問題の発生箇所を特定・再現する。その際のインプットとして、本書で解説した診断ログの`engine`や`launch_kind`、委譲先CLIの診断ログを利用する。また、SDKや実行環境の条件を統一して不具合を再現する際は、後述の「外部プロセスでの再現」に示す起動手順を用いる。

## 資源記録の読み方

- リソーススナップショット（`resource_snapshot`）は、記録時刻、ルートセッションID・セッションID、ターン番号をもとに、各セッションの状態遷移ログと突き合わせて確認する。
- `running_total`および`running_by_root`は、同一の状態ディレクトリ内で観測された実行中（running）のセッション数を示し、待機中プロセスや孫セッションもカウントに含まれる。自身が含まれるのはセッション開始時のスナップショットだけである。稼働中のセッションへの追加入力（追送）は、既存の開始記録の続きとして扱われる。
- `counts_complete`が`false`の場合は集計が不完全であることを示し、確定値は`null`となって読み取れた範囲の数値が`observed_running_*`に格納される。情報の欠落理由は`state_read_failures`や`unavailable_fields`に記録される。
- `load_average_1_5_15`および`available_memory_bytes`はホストマシン全体のリソース値である（Windows環境ではロードアベレージは未取得となり、その旨の理由が残される）。これらは非アトミックに取得された値であるため、個々のセッションが与えた影響や因果関係の特定には同一条件下での比較を要する。
- `resource_snapshot_failed`はリソース計測処理そのものが失敗したことを示すイベントであり、セッション本体の処理失敗とは区別して扱う。ログファイルはサイズ2 MiB、バックアップ3世代で自動ローテーションされる。

## 外部プロセスでの再現

外部プロセスから再現テストを行う場合は、`agent-toolkit/agent_toolkit/_agents_server/claude.py`の`_build_options`が生成する`ClaudeAgentOptions`を用いて`ClaudeSDKClient`を構成する。その後、`connect()`、`query()`、`receive_messages()`の順に呼び出す。`query()`の呼び出しを省略すると初期化処理まで到達しないため、初期化処理を進めるために必ずこの順序で呼び出す。
委譲先CLIの診断ログを採取するには、`ClaudeAgentOptions.extra_args`に`--debug-file`と出力先の絶対パスを指定する。

## 調査で誤りやすい前提

- MCPサーバーのコードを変更した場合、その変更はMCPサーバーを再接続したタイミングで初めて反映される。再接続せずに動作パラメーターを変更したい場合は、起動ごとに外部ファイルから設定値を読み込む設計とする。
- `start`ツールの応答に含まれる`persistence_warnings`は、セッション情報の永続化に失敗したことを示している。セッション開始の成否は`status`および`excluded_candidates`も併せて確認し、すべての候補が除外されていた場合は返されたセッションを利用不能と判断する。正常に開始できたセッションであれば、同じ`session_id`を用いて`show`、`send_message`、`atk agents wait`を継続して実行し、保存失敗の原因は診断ログ側で確認する。保存失敗のみを理由として新しい委譲先を起動することは避け、同一セッションで処理を継続する。
- Claudeの委譲先が実際に動作しているかの根拠は、診断ログ内の`engine`で確認する。工程別のモデル設定によってCodexが選ばれる場合があるため、`start`実行時の`explore`（事前探索）の成功ログは別のバックエンドの動作可能性を含む。
- 委譲先CLIは、`--debug`オプションを指定しても標準エラー出力にはログを出力しない。詳細な診断情報を採取するには、必ず`--debug-file`オプションを使用してファイルに出力させる。
- プロセス起動が「委譲先の作業ディレクトリでプラグインの起動コマンドが失敗した」というエラーで拒否された場合、主な原因は委譲先の作業ディレクトリ上で`uv`や`uvx`が実行できないことにある。例外情報に記録された`cwd`や標準エラー出力を確認する。miseの設定が未信頼（untrusted）になっている場合は、委譲元の手順（`agent-toolkit/skills/process-wi/references/selection-procedure.md`など）に従って`mise trust`を実行するなどの準備をする。それより前に起動された委譲先で発生した接続エラーについては、前述の接続失敗記録の一覧から該当ログを確認する。
