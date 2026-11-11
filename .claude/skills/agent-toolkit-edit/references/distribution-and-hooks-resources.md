# agent-toolkitの実装と配布の規約：MCPサーバー識別子とホスト別ツール名

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 機構や外部インターフェースの変更に伴う文書の追随先を確定する前 | `agent-documents.md` |
| Claudeの子入力の寿命とserver保持版を判断する前 | `agents-server-shared-state-retention.md` |
| hookの実装や登録を新設・変更する前 | `claude-hooks.md` |
| hookの遮断・警告の判定を新設・変更する前 | `claude-hooks-block-warn.md` |
| hookの出力フィールドを選択・変更する前 | `claude-hooks-output.md` |
| hook通知の本文（`fix`・次の操作を含む）またはformatterを新設・変更する前 | `claude-hooks-messages.md` |

## MCPサーバー識別子とホスト別ツール名

- MCPサーバーの識別子にはハイフンを用いず、アンダースコアで構成する。
- MCPツール名は、Claude Code環境では`mcp__plugin_<plugin-name>_<server-key>__<tool>`、Codex環境では`mcp__<server-key>__<tool>`という形式で修飾される。
  フックの正規表現マッチャー、権限設定、およびドキュメント内のツール名一覧は、実行対象となるホストの表記に合わせて個別に記述する。

## プラグイン内リソースの参照書式

エンドユーザー環境で実行するパス（`hooks.json`の`command`や、エージェントやスキルの本文に記載する実行コマンド例など）は、`${CLAUDE_PLUGIN_ROOT}/<相対パス>`の形式で記述する。
プラグインのルートディレクトリはインストール先によって動的に決定されるため、リポジトリ相対パスをそのまま記述するとエンドユーザー環境で実行できなくなる。
なお、各ファイルの役割を説明するための参照であれば、リポジトリ相対パスの表記を用いてよい。
別プラグイン（agent-toolkitからagent-basicsなど）の資料は、配布時に版ごとの別ディレクトリへ置かれて相対パスでは届かないため、読込表の行でも本文の参照でも`agent-basics:`や`agent-toolkit:`で始まる論理名表記で指す。
`agent-toolkit/read_triggers_invariant_test.py`が所有プラグインのrootの外を指す相対パスを拒否する。
agents_serverは候補のengineとcwdごとに両pluginの実在rootを解決し、新規起動と追送へ論理名との対応を渡す。
Claudeの子入力を更新する固定mirrorとserverの保持版は、`agents-server-shared-state-retention.md`「配布物保持と掃引」に従って寿命を分離する。
Agent PluginsのMCP定義をCodex環境向けに変換（射影）する際は、`args`、`cwd`、`env`に含まれる`${CLAUDE_PLUGIN_ROOT}`を`${PLUGIN_ROOT}`へ置き換える。

## 権限設定の配置

- すべてのエンドユーザーを対象とする共通の権限設定は、配布原本の`share/claude_settings_json_managed*.json`に記述する。
  これは`pytools/_internal/update_claude_settings.py`によって`~/.claude/settings.json`へ反映される。
  特定のホストや本リポジトリのみを対象とする設定は、リポジトリ直下の`.claude/settings.local.json`（バージョン管理対象外）に配置する。
  読み取り専用コマンドに対しては、引数を指定しない`Bash`の許可ルールを適用する。
- 秘匿ファイルの読み取りを禁止する`permissions.deny`設定は、共通の配布原本には記述せず、該当ファイルが存在する各プロジェクトの`.claude/settings.json`に配置する。
  `Read(*.key)`のようにディレクトリ指定を含まないグロブパターンは、任意の深さのパスに一致するため、全プロジェクトにおけるディレクトリ探索で確認ダイアログが表示されてしまう。
- プラグインの有効化設定については、永続的な設定ファイルへの追記だけで完結する場合は`share/claude_settings_json_managed*.json`の`enabledPlugins`に記述する。
  設定を反映する前にCLI操作が必要となるプラグインのみ、`pytools/_internal/install_claude_plugins.py`のauto一覧に含める。
