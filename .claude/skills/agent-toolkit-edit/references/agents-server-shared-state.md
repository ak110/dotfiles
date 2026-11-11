# agents_serverの共有状態と読み書きの担当

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 状態資料の選択

状態資料の名指し参照と読込表では、読み書きする状態、更新する主体、消費側との利用関係から必要な資料を選ぶ。
複数の状態を扱う変更では、本書の共通事項と該当する主題別資料の全文を読む。
ファイル名だけで対象を限定せず、判断に必要な前提・例外が別の資料にある場合も、その資料を加える。
共有状態は次の表、セッション状態と内部フラグは`session-state-and-flags.md`「セッション状態資料の選択」を使う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| セッション状態と内部フラグの参照範囲を選ぶ前 | `session-state-and-flags.md` |

## 共有状態資料の選択

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 待機run、終了結果の保持・回収、通知、保留結果や自動再開を変更・調査する前 | `agents-server-shared-state-waiting.md`、`agents-server-shared-state-sessions.md`、`agents-server-shared-state-projection.md` |
| セッションの登録、追送、引き継ぎ、再開、所有の解決や解放を変更・調査する前 | `agents-server-shared-state-sessions.md` |
| ルート・書込主体の索引、statusline・CLIの状態表示、生存確認やコンテキスト使用量を変更・調査する前 | `agents-server-shared-state-projection.md` |
| バックエンドの接続・環境継承、API失敗・利用上限、モデル候補や可用性を変更・調査する前 | `agents-server-shared-state-backends.md` |
| process-loopの所有・再開・無進捗記録、活動記録やレーンの統合・回収を変更・調査する前 | `agents-server-shared-state-sessions.md`、`agents-server-shared-state-process-loop.md` |
| 配布物の保持・リース、managed-tempや共有状態の掃引を変更・調査する前 | `agents-server-shared-state-retention.md`、`agents-server-shared-state-diagnostics.md` |
| 診断・公開イベントログ、コンパクション計測やリソース集計を変更・調査する前 | `agents-server-shared-state-diagnostics.md` |

## 共通事項

`agents_server`の状態は、以下の4つの形式で保持・表現される。

1. MCPサーバーのメモリー
2. 状態ディレクトリ内のファイル
3. フックが記録するセッション状態
4. statuslineが参照する射影データ

コンポーネント（実行主体）ごとに更新できる範囲が異なる。
挙動を確認する際は、関係するすべての処理フローを網羅して確認する（単一の処理だけを追跡すると、別の処理がその状態を更新していることを見落としやすくなる）。

状態ディレクトリは、`atk config get state_dir`が返すディレクトリ配下の`agents-server`を使用する。

process-loop、レーン操作、配布物保持が共有する状態も、同じ`state_dir`の下に保持する。
具体的な保持先は「共有状態資料の選択」から該当する資料を読む。

## 実行主体

| 実行主体 | 実体 | 寿命 |
| --- | --- | --- |
| MCPサーバー | 起動スクリプトは`agent-toolkit/agent_toolkit/agents_server_mcp.py`。MCPツールの実装は`_agents_server/mcp_tools.py`、セッション管理は`_agents_server/manager.py`および`manager_*.py`が担当する | ホストのMCP起動単位ごとに1プロセスが存在する。環境の会話コンテキスト、または要求の`_meta.threadId`から管理対象を確定する。同じ接続の異なる会話は、Manager・root・待機と終了処理を分離する |
| `atk`のCLI | `atk agents wait`、`atk agents notify`、`atk agents list`、`atk agents show`、`atk agents logs`、`atk lane create/integrate/delete`など | コマンド呼び出しごとに起動する短命なプロセス。現在のセッション識別子を取得して動作する。レーン操作では既存のセッション登録情報の参照のみを行う |
| フック | `agent-toolkit/agent_toolkit/_hooks/posttooluse.py`。観測記録の読み書きは`_hooks/agents_server_observations.py`が担当する | ツール実行イベントごとに起動する短命なプロセス。入力JSONから現在のセッション識別子を取得する |
| statusline | `rust/claude-statusline` | 描画更新ごとに起動する短命なプロセス。入力JSONから現在のセッション識別子を取得する |

環境変数`CLAUDE_CODE_SESSION_ID`には、子プロセスの起動時に現在のセッション識別子が設定される。
Claude Codeが同一プロセス内でセッション識別子を切り替えた場合、切り替え後に起動される短命プロセスは新しい識別子を取得できるが、常駐している長命なMCPサーバーは起動時の識別子を保持し続ける。
そのため、`start`ツールの`task`が委譲プロンプトに付与する委譲元の記録情報（`atk-auto`の`kind="launcher-record"`。`ManagerBase.launcher_record_context`が生成）も、会話の切り替え後には以前の会話を指している可能性がある。
データを利用する側は、明示された出所情報や取得済みの`record`・`line`を優先し、対象の発話と一致しているか確認する必要がある。
`session-review-evidence`の`--delegation-record parent`および`--delegation-record root`は、委譲先自身を起点として直接の委譲元や最上位の会話を解決する。
親子関係の判定ロジックは`_common/session_parents.py`に共通化されており、ログ等の記録に残っている起動結果を、登録簿作成時の委譲元情報よりも優先して扱う。
なお、MCPサーバープロセスには`CLAUDE_PID`が渡されないため、Claude Codeのプロセス識別子を環境変数から直接解決できない。

`_meta.threadId`を持つ要求は、`mcp_tools.py`の要求単位のContextVarが該当会話のManagerを選ぶ。
環境から解決した親と一致する場合は既存Managerを使い、それ以外は同じ会話IDのrootと`root.json`を所有するManagerをMCPの寿命内で保持する。
最初の要求を返す前に子の復旧起動と状態の公開を終え、既存aliasesの形式でCLIへの対応を保存する。
同じ会話の初期化だけを排他し、異なる会話の接続は並行して初期化できる。
会話IDの無い要求は従来の起動環境とプロセスごとのrootの解決を維持する。

## 共有状態ごとの保持先と読み書きの担当

状態ディレクトリ配下の読み書きは、`agent-toolkit/agent_toolkit/_agents_server/`配下の次のモジュールが担当する。

| 対象 | モジュール |
| --- | --- |
| 配置とセッション識別子の検証 | `shared_layout.py` |

## 判定を確定する前に確認すること

- 各状態資料の表で「更新できる主体」が2つ以上存在する状態については、関係するすべてのコンポーネントの処理フローを網羅して確認した上で挙動を確定する。
- MCPサーバーのメモリー上にのみ存在する状態はMCPサーバー自身しか更新できず、`atk` CLIやフックから更新できない。
  そのため、その状態に依存する判定処理がCLI経由の操作でも破綻せずに成立するかを確認する。
- 新しい状態を設計する際は、状態の表現形式を1つに集約する。

`notices`および`hosts`配下の共有ファイルは、保持期限が経過した後に各書込主体が削除できる。

MCP起動時とSessionEndの掃引は、最終更新から7日を超える通常のルート、登録簿、計測記録を対象にする。
`session_registry.pending_session_ids`が解決する未完了・所有者停止による中断・再開予約と、未回収結果・未配送のCLI待機runは保持する。
これらの会話と親の登録簿・会話別managed-tempも保護し、依頼と復旧記録を期限だけで失わせない。
終了・回収後は通常の掃引へ戻す。
現在のルートは保持し、compactionのJSONLとロックは対で回収する。
個別の削除失敗は診断へ記録し、他の対象とサーバーの処理を続ける。

バックエンドの起動処理が成功した後のデータ保存成否は、起動処理自体の成否とは独立して評価する。
`manager.py`の`_save_started_session`は、以下の保存処理における失敗を個別に検知して`persistence_warnings`に集約し、起動レスポンスには`session_id`、作業ディレクトリ（cwd）、委譲元情報を確実に残す。

- セッション登録簿の更新（`SessionState.publish_registration_async`）
- 状態ファイルの出力・反映（`StatusFileWriter.flush_async`）
- 待機対象リストへの登録（`wait_targets.retain_wait_targets`。これは`flush`に失敗した場合のフェイルセーフとしてのみ実行される。`flush`が成功した場合は、既存の状態ファイルとPostToolUseフックの登録情報がそのまま利用される）

登録簿の更新処理は、正常に永続化できた値のみを「公開済み」として記録するため、保存に失敗した場合は次回以降の状態更新や`flush`のタイミングで自動的に再試行される。
表示用の状態ファイルの書き込み失敗は`status_file.ProjectionWriteError`として扱われ、終了結果ファイルの保存失敗とは区別される。
タイマー予約された`flush`やハートビート更新の失敗はログに出力した上で、次回の更新サイクルへと進む。
非同期での保存処理では、対象パス、書き込み本文、登録用データをイベントループ側で確定させ、実際のファイルI/O操作のみを`file_operations.run_file_operation`経由でワーカースレッドへ委譲する。
最新スナップショットの反映や保持タイマーの操作もイベントループ側で管理し、共有のセッション辞書やwriterオブジェクト自体をワーカースレッドへ渡すことはしない。
同期CLIが提供する`atomic_write`によるアトミックな書き込みと、有限回数の再試行ルールはそのまま維持される。
イベントループ上の`SessionState.touch`は、登録情報の公開タスクをセッションごとの単一タスクに集約し、保存処理の実行中に値が変化した場合でも最終的に最新の状態まで更新する。
起動、プロンプト追送、結果配送などのレスポンスは、必要な永続化処理の完了を待ってから返却される。
writer側は保存処理と削除処理を直列化（キューイング）して実行するため、すでに公開済みまたは回収済みの結果を重複して再保存することはなく、過去ターンの保存成功フラグが新しいターンへ誤って持ち越されることもない。
MCPサーバーの停止処理（`AgentsServerManager.close`）では、終了情報の公開時に発生した`ProjectionWriteError`のみをログに残してクリーンアップ処理を続行する。
一方で、終了結果ファイルの保存時に発生した`OSError`は呼び出し元へ例外として伝播させ、イベントリスナーの解除や`StatusFileWriter.deactivate`は`finally`節で確実に実行する。

停止要求がキャンセルされた場合でも、実行中の保存タスクやワーカースレッドの完了を待って回収し、終了情報の公開と予約タスクの解除を完了した上で自身の状態ファイルを削除する。
保存の失敗を成功とみなすことはせず、停止処理が完了した後にワーカースレッドが状態ファイルを再作成してしまう事態を防ぐ。
ドレイン処理（drain）は、並行して実行する保存処理の一部が失敗した場合でも、すべての対象ワーカーの終了を確実に回収し、登録情報の公開まで完了した上で、保持していたエラーを呼び出し元へ報告する。
共通ユーティリティの`atomic_write`は、ファイル置換に失敗した例外に対して置換元と置換先のパス情報を注記（`add_note`）するため、呼び出し側が記録するスタックトレースからも双方のパスを特定できる。

同期I/O操作を担うワーカーの実装は`_common/async_operations.py`が提供しており、agents_serverの`file_operations.py`は既存の関数名を再エクスポートして提供している。
監視処理の開始や終了時にもこの共通実装を利用し、タスクのキャンセル後も所有スレッドを確実に回収する。

非同期での共有状態操作は、`file_operations.run_file_operation`を用いて完了を待機し、結果や通知を1回限りで回収する処理も排他ロック区間全体をワーカースレッド内で実行する。
Managerによる問い合わせ、復元、結果回収には非同期APIを使用する。
状態の射影、`SessionState`、タイマー、および公開済み結果台帳の更新はイベントループ側で行う。
診断情報の収集処理へ渡すセッション情報は、イベントループ側で確定したスナップショットデータとする。
永続ログの書き込みは専用のワーカースレッドが順次出力し、サーバー終了処理の完了前に対象ログのフラッシュとワーカースレッドの終了を確実に回収する。

## 本書の更新が必要になる変更

以下を変更する場合は、該当する状態の資料を更新する必要がある。

- 各状態資料の表に記載された状態について、基準となる保存場所、読み取りを担当する主体、または更新可能な主体を変更する場合。
- 状態ディレクトリ配下に新しい種類のファイルを配置・追加する場合。
- MCPサーバー、`atk` CLI、フック、statuslineのいずれかに対して、既存の共有状態を読み書きする処理を新たに追加する場合。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 待機と終了結果・通知に関する判断・作業の前 | `agents-server-shared-state-waiting.md` |
| セッション登録と再開・解放に関する判断・作業の前 | `agents-server-shared-state-sessions.md` |
| 索引と状態表示に関する判断・作業の前 | `agents-server-shared-state-projection.md` |
| バックエンド固有の接続とAPI状態に関する判断・作業の前 | `agents-server-shared-state-backends.md` |
| process-loopとレーンに関する判断・作業の前 | `agents-server-shared-state-process-loop.md` |
| run-skillの再開入力に関する判断・作業の前 | `agents-server-shared-state-run-skill.md` |
| 配布物保持と掃引に関する判断・作業の前 | `agents-server-shared-state-retention.md` |
| 診断とログに関する判断・作業の前 | `agents-server-shared-state-diagnostics.md` |
