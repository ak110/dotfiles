# agents_serverの委譲不具合の調査手順

本書は、`agents_server`が起動した委譲先が動作しない場合に、どの記録を確認して事実関係を特定すべきかをまとめた文書である。
委譲先は別プロセスのCLIとして起動するため、委譲元が内部状態を知る手段は記録と診断に限られる。
そのため、観測手段は実装コードや実行環境の複数箇所に分散している。
共有状態ごとの保存場所や読み書きの担当モジュールについては、`references/agents-server-shared-state.md`を参照する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 共有状態の保存場所や読み書きの担当を調べる前 | `agents-server-shared-state.md` |
| 原因調査で発生位置と直接的原因を確定する前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md` |

## 観測できる記録の所在

診断ログが保存されるディレクトリは、`agent-toolkit/agent_toolkit/_common/state_paths.py`の`state_dir`によって解決され、`atk config get state_dir`の出力と一致する。
Linux環境では`platformdirs`の`user_state_dir("agent-toolkit", appauthor=False)`を使用し、相対パスの`XDG_STATE_HOME`は`HOME/.local/state`へ退避する。
Windows環境では`LOCALAPPDATA`配下の`agent-toolkit`を使用する。
Claude Codeにおける作業ディレクトリのスラッグは、絶対パスの区切り文字や記号をハイフンへ置換した文字列である。

`agents_server`の診断ログを出力する各モジュールは以下のとおり。

- MCPサーバーの起動・終了や初期化（initialize）の節目: `_agents_server/mcp_tools.py`および`mcp_transport.py`
- 起動単位ごとの終了診断: サーバー自身のイベントは`_agents_server/logging_config.py`（呼び出し元は`mcp_tools.py`・`mcp_transport.py`・`manager_registry.py`）。
  子プロセスの終了状態や標準エラー末尾のログは`scripts/launch.py`
- 候補ごとの初期化と候補の切り替え: `manager.py`。
  全バックエンドの探索（explore）には通常のコマンド実行を用いる。
  起動応答の`excluded_candidates`や警告ログから、CLIのインストール状況、認証、モデルの可用性に関する失敗原因を調べる。
  Codexの開始・再開時には、スキルを配信しないためのplugins設定と、配布定義から直接配信するpyfltr MCP設定を確認する。
  専用サンドボックスのprobeは使用しない
- 起動後の保存失敗（agents_serverのセッション登録簿`<状態ディレクトリ>/agents-server/sessions/<session_id>.json`、待機対象、状態ファイルなど）: `manager.py`と`state.py`。
  予約保存やheartbeatの失敗は、書き込み主体の状態ファイルやセッション識別子の一覧とともに`status_file.py`および`manager_base.py`が記録する。
  いずれもスタックトレースを含み、置換元・置換先のパスは例外オブジェクトの`add_note`による注記に記載される。
  共通のアトミック置換処理（`_common/atomic_file.py`）では、Windows環境特有のWinError 5や32に対して1秒未満の有限回数でリトライし、上限到達後や対象外のエラーでは既存の内容を保ったままエラーを返す
- セッションの状態遷移（`session_transition`）: `manager.py`・`manager_registry.py`・`manager_resume.py`・`state.py`
- 開始時および終了時のリソース記録（`resource_snapshot`）: `resource_snapshot.py`
- AntigravityおよびOpenCodeの子プロセス管理・イベント保存・終了処理: `cli_turn.py`。
  通信フォーマット（wire形式）の解釈は`antigravity.py`と`opencode.py`が担当する。
  OpenCodeの起動には`opencode run --format json --auto --model <provider/model> --variant <effort>`を用い、セッション継続時には`--session`を渡す。
  起動候補として指定したモデルと、イベントログから実際に観測されたモデル識別子は区別して扱う

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

1. 原因分析の基準を揃えるため、`agent-basics:bugfix`を起動し、`agent-basics:bugfix/references/root-cause-analysis-evidence.md`「直接的原因と事象時点の確定」を適用する。
2. 発生箇所を絞るため、「観測できる記録の所在」から診断を取得する。
   通常の応答では`seconds_since_activity`で最後の活動からの経過時間を確認し、絶対時刻や実行パラメーターの比較には`show(verbose=True)`を使う。
   API失敗の公開診断にはエラー種別、HTTPステータスコード、経過時間、初期化診断には受信メッセージの種別と失敗箇所の詳細がある。
   Claude接続候補の試行・選択・失敗は`claude_connection_attempt`・`claude_connection_selected`・`claude_connection_failed`を確認する。
   API切替は`claude_api_switch`、MCP再接続成否は`claude_mcp_recovered`・`claude_mcp_recovery_failed`を確認する。
   MCP再接続の監査記録は`docs/development/audit-records-agents-server.md`「docs/development/design-agents-runtime.md：MCP接続失敗の再接続：2026年10月20日」にある。

3. 停止契機を特定するため、中断（`interrupted`）では`diagnostics.next_action`の行範囲（`sed -n '<開始行>,<終了行>p' -- <絶対パス>`）で終了診断を読む。
   受信シグナル、標準入力の切断、終了ステータスの有無を確認する。
   親側が終了を開始した直接の理由は親側の記録にのみ残るため、`parent_connection_log.path`の親CLI接続ログと終了イベント（`exited`・`signal_received`・`process_exited`）の時刻（`at`）を突き合わせる。

   `parent_connection_log`が`path`でなく`reason`を返す場合は、親接続ログの所在を特定できないため別の方法で親側の記録を確認する。
   `unsupported_parent`は`CLAUDECODE`が`1`以外（親がClaude Code以外）、`not_found`はClaude CLIのキャッシュを解決できないか、該当スラッグ配下に起動時刻以前の`mcp-logs-*agents*server*`ログが無いことを示す。

   保存場所の報告前にクラッシュした場合（依存解決失敗や起動直後のエラーなど）は、診断ファイルも登録簿の`server_instance`も作られない。
   ランチャーが標準エラーへ出す1行（診断ファイル未作成と推奨操作を含む）が親CLI接続ログに残る唯一の手掛かりとなる。
   終了コード0の正常終了（`--check-dependencies`など）ではこの行は出ない。

4. 因果を裏付けるため、診断ログの`engine`・`launch_kind`と委譲先CLIの診断を使い、実際の起動パラメーターと成否を比較して発生箇所を特定・再現する。
   SDKや実行環境を統一する再現は「外部プロセスでの再現」へ進む。

## 資源記録の読み方

- リソーススナップショット（`resource_snapshot`）は、記録時刻、ルートセッションID・セッションID、ターン番号をもとに、各セッションの状態遷移ログと突き合わせて確認する。
- `running_total`および`running_by_root`は、同一の状態ディレクトリ内で観測された実行中（running）のセッション数を示し、待機中プロセスや孫セッションもカウントに含まれる。
  自身が含まれるのはセッション開始時のスナップショットだけである。
  稼働中のセッションへの追加入力（追送）は、既存の開始記録の続きとして扱われる。
- `counts_complete`が`false`の場合は集計が不完全であることを示し、確定値は`null`となって読み取れた範囲の数値が`observed_running_*`に格納される。
  情報の欠落理由は`state_read_failures`や`unavailable_fields`に記録される。
- `load_average_1_5_15`および`available_memory_bytes`はホストマシン全体のリソース値である（Windows環境ではロードアベレージは未取得となり、その旨の理由が残される）。
  これらは非アトミックに取得された値であるため、個々のセッションが与えた影響や因果関係の特定には同一条件下での比較を要する。
- `resource_snapshot_failed`はリソース計測処理そのものが失敗したことを示すイベントであり、セッション本体の処理失敗とは区別して扱う。
  ログファイルはサイズ2 MiB、バックアップ3世代で自動ローテーションされる。
- WindowsのCodex境界試験では、試験開始・終了のpytest PIDとUTC時刻、App Serverの終了要求前・終了確認後の所有PID集合を対応付ける。
  各PIDの`created_at`と`ppid`で起動時刻と親子関係を確認し、終了後の`unavailable: NoSuchProcess`を保持PID不在の照会結果と区別する。
- pytestの回収失敗時は、標準エラーの`resource-diagnostics:`にある失敗パス・Windowsエラー番号・`before`・`after`を読む。
  `ownership.state`の`success`だけが保持PID照会の成功を示し、`unavailable`は採取不能、`unsupported`は対象外OSを表す。
  診断handleの自己PIDは外部保持者と分けて記録され、同じPIDの別handleの有無は判別できない。
  CIの開始・終了行と試験開始記録から、Python完全版・実行ファイル、runner image、Codex・pytest・xdistの版、並列度と最終終了コードも突き合わせる。
- 診断を採取できなかった場合も`next_action`に従い、元の回収例外のtracebackから失敗パス・winerrorを保存し、同じ実行の終了前後PID記録・CI環境・最終コードを原因分析担当へ渡す。

## 外部プロセスでの再現

1. 起動条件を揃えるため、`agent-toolkit/agent_toolkit/_agents_server/claude_options.py`の`_build_options`が生成する`ClaudeAgentOptions`で`ClaudeSDKClient`を構成する。
   診断ログの採取には`ClaudeAgentOptions.extra_args`へ`--debug-file`と出力先の絶対パスを指定する。
2. 初期化へ到達させるため、`connect()`、`query()`、`receive_messages()`の順で必ず呼び出す。
   `query()`を省くと初期化に到達しない。

## 調査で誤りやすい前提

- MCPサーバーのコードを変更した場合、その変更はMCPサーバーを再接続したタイミングで初めて反映される。
  再接続せずに動作パラメーターを変更したい場合は、起動ごとに外部ファイルから設定値を読み込む設計とする。
- `start`ツールの応答に含まれる`persistence_warnings`は、セッション情報の永続化に失敗したことを示している。
  セッション開始の成否は`status`および`excluded_candidates`も併せて確認し、すべての候補が除外されていた場合は返されたセッションを利用不能と判断する。
  正常に開始できたセッションであれば、同じ`session_id`を用いて`show`、`send_message`、`atk agents wait`を継続して実行し、保存失敗の原因は診断ログ側で確認する。
  保存失敗のみを理由として新しい委譲先を起動することは避け、同一セッションで処理を継続する。
- Claudeの委譲先が実際に動作しているかの根拠は、診断ログ内の`engine`で確認する。
  工程別のモデル設定によってCodexが選ばれる場合があるため、`start`実行時の`explore`（事前探索）の成功ログは別のバックエンドの動作可能性を含む。
- 委譲先CLIは、`--debug`オプションを指定しても標準エラー出力にはログを出力しない。
  詳細な診断情報を採取するには、必ず`--debug-file`オプションを使用してファイルに出力させる。
- プロセス起動が「委譲先の作業ディレクトリでプラグインの起動コマンドが失敗した」というエラーで拒否された場合、主な原因は委譲先の作業ディレクトリ上で`uv`や`uvx`が実行できないことにある。
  例外情報に記録された`cwd`や標準エラー出力を確認する。
  miseの設定が未信頼（untrusted）になっている場合は、委譲元の手順（`agent-toolkit/skills/process-wi/references/selection-procedure.md`など）に従って`mise trust`を実行するなどの準備をする。
  それより前に起動された委譲先で発生した接続エラーについては、前述の接続失敗記録の一覧から該当ログを確認する。
