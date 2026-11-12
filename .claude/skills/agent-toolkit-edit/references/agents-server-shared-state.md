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
| run-skillの再開入力を変更・調査する前 | `agents-server-shared-state-run-skill.md` |

## 共通事項

`agents_server`の状態は、以下の4つの形式で保持・表現される。

1. MCPサーバーのメモリー
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
