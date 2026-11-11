# backend構成と起動の設計記録

[設計記録の索引](design.md)から参照する、backend構成と起動の記録である。

## backend構成と起動

MCP Python SDK 2系の`MCPServer`が6ツールの登録、入力スキーマと構造化応答を所有する。
stdioの送受信は公開lowlevel `Server`と`stdio_server`へ接続し、登録側の`list_tools`・`call_tool`を使う。
通常の`MCPServer.run`では既存のstream監視を挿入できないため、この接続でinitializeの受信、応答送信の成功・失敗と未完了終了を診断する。
Managerのactivate・closeも同じlowlevelサーバーのlifespanへ接続する。
SDKのprivateサーバー属性へ依存する案と、ツール定義やスキーマ生成を二重に持つ案は採らない。
ツール処理の`ToolError`は理由と次の操作を持つエラー応答へ変換し、SDKが返す構造化成功応答と区別する。

`agents_server`はClaude CodeとCodexから同じ公開APIで委譲できる共有MCPサーバーである。
`start`の`model_type`と起動の用途に対応する工程別モデル設定でCodex backendまたはClaude backendを選択し、各backendの実行主体を
共有のsession状態機械、待機通知および結果配送境界へ接続する。
Codex backendは公式stdio App Serverを
セッション単位で所有し、Claude backendはClaude Agent SDKのclientをセッション単位で所有する。
共有daemonや永続job registryは使わず、MCP終了時に自身が起動した子プロセスだけをPID指定で終了するため、
LinuxとWindowsで寿命契約を揃えられる。

Codex backendは子App Serverの作業ディレクトリへ、実行主体のホームディレクトリを明示指定する。
MCPサーバー自身の作業ディレクトリはホストの起動条件で決まり、Codexホストではpluginの版数付きcacheディレクトリになる。
plugin更新で作業ディレクトリが消えると、子App Serverは生存したまま`thread/start`へ`failed to load configuration: No such file or directory`のJSON-RPC errorを返し続ける。
子プロセスを再生成する案は、新しい子も同じ親の作業ディレクトリを継承するため採用しない。

サーバーは共通の`_agents_server/plugin_root.py`の読込時に、自身を起点に解決したagent-toolkit plugin rootを確定する。
root自身か祖先がmanifestの版番号を持つ場合、同じ配布元・版に対応するmanaged-tempの保持実体を複数プロセスで共有する。
checkoutは元の実体を使う。
保持実体から再起動したサーバーも同じ実体を使い、異なる配布元・版と不完全な作成物を区別する。
起動側と回収側の使用中判定は`_common/plugin_root_leases.py`が所有し、プロセスの識別とファイル排他で使用中の実体を保護する。
全使用プロセスの終了後は既存のmanaged-temp回収へ渡せる状態にする。
同じ保持版を役割名による文書解決、本文のplugin root変数の展開、内側のagents_server MCP起動設定へ渡す。
`thread/start`・`thread/resume`の全launch kindの`developerInstructions`にも同じ版を渡す。
元の配布物の絶対パスを受け取った場合も、起動時に保持した対応から同じ文書へ解決する。
委譲先はagent-toolkit skillとplugin内部資源をそのrootから読み、`<役割名>.subagent.md`や別hostのcache版数から別rootを組み立てない。
直接起動したCodexメインはこの受け渡しの対象外であり、そのplugin root解決は`rules-common.codex.md`「Codexのplugin root解決」が所有する。

知識境界として、`<役割名>.subagent.md`内の`${CLAUDE_PLUGIN_ROOT}`を文書自身のrootへ展開する処理は`_agents_server/launch_requests.py::task_document_request`が所有する。
委譲先が実行時にskillや内部資源を探す起点は、Codex backendのdeveloper instructionが所有する。
内側MCPだけにstable rootを渡すと、委譲先自身のファイル参照は安定しないため、この案は採用しない。
開始時だけにstable rootを渡す案も、保存済みthreadを別processで再開した後の起点を失うため採用しない。

ローカルbackendのclassはMCP module初期化時に読込み、共有の`SessionState`と状態更新関数は独立した共通moduleが所有する。
この境界により、プラグイン配置が初回engine選択まで利用できない場合も、既に読込済みのclassからbackendを生成できる。
Claude Agent SDKのimportはClaude backend内でoptions/clientを使う時点まで遅延し、Codex専用の処理へSDK依存を持ち込まない。
`--check-dependencies`はPEP 723環境でClaudeAgentOptionsを構築するだけの確認であり、外部sessionもMCP公開statusも生成しない。
Claude CodeとCodexのplugin導入後は、対象の実MCP定義と共通ランチャーを使い、環境準備と依存検査に続いてstdio MCPのinitialize・tools/list・closeを確認してからupdate-dotfilesを終える。
有効な対象だけを選び、無効なプラグインを有効化しない。
agents_serverのウォームアップは初回MCP起動の成立条件であるため、実プロトコルの確認に30秒の上限を持たせ、起動不能、空のツール一覧、timeoutおよび非0終了を段階・対象・終了コード・所要時間・標準エラーとともに更新処理へ伝播する。
依存取得失敗は既存の取得工程の分類を保つ。
依存検査だけではMCP初期化の成立を観測できず、クライアントの起動上限を延ばす案も失敗箇所を特定できないため、この確認を選んだ。
hookスクリプトのウォームアップは更新後の実行を高速化するだけであり、個別失敗を段階失敗へ変えない既存契約を維持する。
pyfltr MCPのウォームアップも初回MCP起動の成立条件として扱い、失敗を、ウォームアップを呼び出した更新処理へ伝播する。
Claude CodeとCodexは`uvx`でpyfltr MCPを起動し、ツール環境が未構築なら取得と構築の時間がMCPクライアントの起動上限（Codexの`startup_timeout_sec`は未設定時10秒）を超え得るためである。
起動形は両ホストが実際に読むMCP定義（Claude Codeの`.mcp.json`、Codexの`.mcp.codex.json`）の`pyfltr`の`command`と`args`から導出し、末尾の`mcp`を`--version`へ置き換えて1回起動する。
要求指定をウォームアップ側へ書き写すと、配布元の要求指定の更新に追随しない二重管理になるためである。

ウォームアップは依存の準備が済んでいない初回起動の失敗を防ぐが、準備が済んだ後にPCの状態で起動が遅れる場合は防げない。
Claude CodeはMCPサーバーの接続を`MCP_TIMEOUT`（既定30秒）まで待ち、最初の接続に失敗したstdioのサーバーを自動では再接続しない。
agent-toolkitのMCPサーバーの接続所要は通常4〜6秒だが、2026年11月10日のPC再起動直後には約32秒かかり、`agents_server`と`pyfltr`の両方が時間切れになった（同じPCの接続記録と起動記録の突き合わせによる）。
このときは起動の各段階が一様に6〜8倍遅く、サーバー固有の処理ではなくPC全体の遅さが原因だった。
通常の所要と既定の上限の差は5〜7倍しかなく、この遅延を吸収できないため、dotfilesの管理設定（`share/claude_settings_json_managed.json`の`env`）で`MCP_TIMEOUT`を120秒にしている。
120秒は観測した最長の所要の約4倍である。
Claude Codeは既定でMCPサーバーの接続を待たずに最初の入力を受け付けるため、対話のセッションの開始は遅れず、`claude -p`が待つのもサーバーが起動に失敗した場合の最長120秒である。
起動処理を重くする変更や、この上限を戻す変更では、通常の所要・遅い条件の所要・上限の3つを比べる。
ウォームアップは更新の直後に動くため再起動直後の所要を観測できず、所要の割合で警告する案は採らない。
起動所要そのものを縮める案も、一様な遅延の倍率を変えないため採らない。
Codexの`startup_timeout_sec`（既定10秒）は、プラグインに同梱するMCP定義から上限を指定する方法が公式資料に無いため変えておらず、同じ時間切れが起き得る。
