# agents_serverの共有状態と読み書きの担当

## 共通事項

`agents_server`の状態は、以下の4つの形式で保持・表現される。

1. 共有管理サービスの会話別メモリー
2. 状態ディレクトリ内のファイル
3. フックが記録するセッション状態
4. statuslineが参照する射影データ

コンポーネント（実行主体）ごとに更新できる範囲が異なる。
挙動を確認する際は、関係するすべての処理フローを網羅して確認する（単一の処理だけを追跡すると、別の処理がその状態を更新していることを見落としやすくなる）。

状態ディレクトリは、`atk config get state_dir`が返すディレクトリ配下の`agents-server`を使用する。
配置とセッション識別子の検証は`agent-toolkit/agent_toolkit/_agents_server/shared_layout.py`が担当する。

process-loop、レーン操作、配布物保持が共有する状態も、同じ`state_dir`の下に保持する。
具体的な保持先は「共有状態資料の選択」から該当する資料を読む。

## 実行主体

| 実行主体 | 実体 | 寿命 |
| --- | --- | --- |
| MCP転送 | `agents_server_mcp.py`から`_agents_server/service_forward.py`を起動する。stdioと認証済みローカルIPCを中継する | 接続単位。Manager・backend・MCP SDKを読み込まず、接続EOFで会話を閉じない |
| 共有管理サービス | `_agents_server/service.py`。公開6操作は既存の`mcp_tools.py`、会話別Managerは`service_context.py`、世代移譲は`service_coordinator.py`が担当する | 同じOSアカウント・状態領域で共有する。会話ごとに環境・cwd・親settings、Manager・root・待機を分ける。effort・release・heartbeatを中央で巡回し、実ホスト死亡はPID起動世代で確認する |
| `atk`のCLI | `atk agents wait`、`atk agents notify`、`atk agents list`、`atk agents show`、`atk agents logs`、`atk lane create/integrate/delete`など | コマンド呼び出しごとに起動する短命なプロセス。現在のセッション識別子を取得して動作する。レーン操作では既存のセッション登録情報の参照のみを行う |
| フック | `agent-toolkit/agent_toolkit/_hooks/posttooluse.py`。観測記録の読み書きは`_hooks/agents_server_observations.py`が担当する | ツール実行イベントごとに起動する短命なプロセス。入力JSONから現在のセッション識別子を取得する |
| statusline | `rust/claude-statusline` | 描画更新ごとに起動する短命なプロセス。入力JSONから現在のセッション識別子を取得する |

環境変数`CLAUDE_CODE_SESSION_ID`には子プロセスの起動時に現在のセッション識別子が設定される。
共有サービスは接続から受け取った`CallerContext`と要求の会話IDを使い、サービス自身の起動環境を会話の推定元から除外する。
同一プロセス内で会話が切り替わり、要求に新しい会話IDが無い場合は、起動時の環境に基づく会話の識別に限られる。
`start`が付与する委譲元の記録情報（`kind="launcher-record"`。`ManagerBase.launcher_record_context`が生成）も、元発話との一致を確認する。
データを利用する側は、明示された出所情報や取得済みの`record`・`line`を優先し、対象の発話と一致しているか確認する必要がある。
`session-review-evidence`の`--delegation-record parent`および`--delegation-record root`は、委譲先自身を起点として直接の委譲元や最上位の会話を解決する。
親子関係の判定ロジックは`_common/session_parents.py`に共通化されており、ログ等の記録に残っている起動結果を、登録簿作成時の委譲元情報よりも優先して扱う。
実ホストのPID世代と親settingsは、`service.py`がOS認証した転送側peerの祖先から確認し、確認不能なら会話を保持する。

`_meta.threadId`を持つ要求は、`mcp_tools.py`の要求単位のContextVarが該当会話のManagerを選ぶ。
同じ会話IDへの接続はサービス内のManagerへ合流し、会話ごとのrootとwriterを保持する。
最初の要求を返す前に子の復旧起動と状態の公開を終え、既存aliasesの形式でCLIへの対応を保存する。
同じ会話の初期化だけを排他し、異なる会話の接続は並行して初期化できる。
会話IDの無い要求は接続固有の識別子と起動環境からrootを解決し、別の匿名接続と混同しない。

## 管理サービスの世代登録

管理領域は`state_dir/services/agents-server`とし、既存セッション・結果領域の`state_dir/agents-server`と区別する。
Windowsの管理領域は、通常権限では`services/agents-server-standard`、昇格時は`services/agents-server-elevated`に分ける。
`service_generations.py`が`generations.json`へ受付先、コード実体・版、準備順、OSプロセス世代を保存する。
これはジョブ台帳ではなく、実行の復旧には既存セッション登録簿とbackendの物理記録を使う。
`startup.lock`・`publish.lock`・`generations.lock`は固定名で保持し、世代ごとの`<generation>.json`にreadyの接続先、`<generation>.stderr.log`に起動診断を置く。
転送側は世代の識別（`service_forward.code_identity`）の結果を`code-identity/index.json`へ保存し、rootの全モジュールの識別（inode・更新時刻・大きさ）が同じ間はその結果を使う。
直下の`*.json`は世代の記録だけに使うため、この索引は下位のディレクトリへ置き、壊れた索引は再計算した結果で置き換える。
認証値はIPCの非永続context内だけで扱い、世代登録・ログ・再開情報へ保存しない。
Linuxは相互UID、Windowsは現在SIDのDACL・remote拒否とpeer PID世代で相手を認証する。
所有移譲と初回旧stdioの限界は`docs/development/design-agents-server-service.md`に記録する。

## 判定を確定する前に確認すること

- 各状態資料の表で「更新できる主体」が2つ以上存在する状態については、関係するすべてのコンポーネントの処理フローを網羅して確認した上で挙動を確定する。
- MCPサーバーのメモリー上にのみ存在する状態はMCPサーバー自身しか更新できず、`atk` CLIやフックから更新できない。
  そのため、その状態に依存する判定処理がCLI経由の操作でも破綻せずに成立するかを確認する。
- 新しい状態を設計する際は、状態の表現形式を1つに集約する。

## 本書の更新が必要になる変更

以下を変更する場合は、該当する状態の資料を更新する必要がある。

- 各状態資料の表に記載された状態について、基準となる保存場所、読み取りを担当する主体、または更新可能な主体を変更する場合。
- 状態ディレクトリ配下に新しい種類のファイルを配置・追加する場合。
- MCPサーバー、`atk` CLI、フック、statuslineのいずれかに対して、既存の共有状態を読み書きする処理を新たに追加する場合。
