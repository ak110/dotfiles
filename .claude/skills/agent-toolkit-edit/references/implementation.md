# 実装の配置と規約

`agent-toolkit/`の実装モジュール、スクリプト、起動、権限設定、`atk serve`の静的資産を新設・変更する時の、このリポジトリ固有の配置と規約である。
一般の基準は`agent-basics:writing-standards/references/python.md`、`agent-basics:writing-standards/references/check-script-design.md`などにあり、本書はこのリポジトリでの差分だけを持つ。

## agent_toolkitパッケージの配置と層

- `agent-toolkit/agent_toolkit/`の直下には、配布物の外から絶対パスで解決する公開スクリプト（`hook.py`、`atk.py`、`agents_server_mcp.py`、`wait_ci.py`）だけを置き、名前の先頭に`_`を付けない。
- リモートで動く`atk_serve_plans_remote_helper.py`と`atk_serve_sessions_remote_helper.py`は、`agent-toolkit/scripts/`直下の公開スクリプトとして置く。
  両ヘルパーは同じチェックアウトの`agent_toolkit`を`sys.path`経由で読み、SSH接続先には標準ライブラリと`platformdirs`、`watchdog`だけがあるため、両ヘルパーが読むモジュールもこれらだけに依存させる。
- 実装モジュールは`_common`、`_git`、`_plan`、`_atk`、`_agents_server`、`_hooks`のサブパッケージに置く。
  この順が依存の階層であり、importは後の層から前の層へか、同じ層の中に限る。
  新しいモジュールはそれを読む主体のサブパッケージに置き、hook以外からも使うモジュールは`_hooks`以外の層に置く。
- 直下の公開スクリプトと`agent-toolkit/skills/*/scripts/`のスクリプトは起動スクリプトとして全ての層をimportできるが、`_hooks`をimportできるのは`hook.py`だけである。
- テスト専用の共有ヘルパーは階層の外の`_testing`に置き、importできるのは`*_test.py`、`conftest.py`、`_testing`の中のコードだけである。
- サブパッケージの中では絶対importを使う。
  階層を検査する`scripts/check_script_imports.py`は相対importを解析しないため、相対importは検査から漏れる。
- モジュール名は、所属を表す接頭辞を除いて、組み込みの名前と標準ライブラリのトップレベル名に重ねない。
- テストの配置は`.claude/skills/develop-dotfiles/references/verification.md`「テスト配置」に従う。
- agent-toolkitの中でGitを起動する時は`agent_toolkit._git.command.run`を使う（`text=True`で渡した文字列の`input`も、UTF-8のバイト列として子プロセスへ渡る）。
- 構造化文書の共通判定は`agent-toolkit/agent_toolkit/_plan/structure/`にあり、成果物契約は`agent-toolkit/skills/atk-mode/references/plan-format.md`が定める。
  判定の追加では、見出しの親子と重複、コードブロック、段落と表の構文境界を共通のテスト入力へ加える。

## 起動と依存の準備

- agent-toolkitのCLI、hook、MCPサーバーは`uv run --no-cache --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py <対象>`で起動する。
  `launch.py`は標準ライブラリだけでプラグインのrootとデータ領域を解決し、準備済みの共有環境のPythonで対象を起動する。
- 依存の準備は`uv run --no-cache --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py --prepare agent_toolkit/atk.py`で行う。
  `--prepare`の後ろのtargetは、配布rootの中に起動対象が在ることを確かめるためだけの必須の引数である。
  依存の宣言と固定内容（`launch.py`のインラインメタデータと`launch.py.lock`）、選んだPythonの実行ファイルのパス、OSとアーキテクチャの組み合わせが未準備の時に実行し、同じ組み合わせなら別の配布rootやworktreeでも準備済みの環境を共有する。
  別のworktreeを指す時の`<plugin root>`は、そのworktreeの`agent-toolkit`の絶対パスにする（rootは`launch.py`の配置か`--project`の指定から解決し、cwdを変えただけでは切り替わらない）。
- 登録済みの`skills/*/scripts/`のスクリプトは、`atk run-script`で同じ環境から実行する。
  SSH接続先で動く`atk_serve_*_remote_helper.py`だけは独立したPEP 723スクリプトとして起動するため、PEP 723の宣言を保つ。
- `agent-toolkit/`の実行依存は`scripts/launch.py`冒頭のPEP 723インラインメタデータへ書き、`agent-toolkit/`直下には`pyproject.toml`と`uv.lock`を作らない。
  変えたら`uv lock --script agent-toolkit/scripts/launch.py`で`launch.py.lock`を更新し、リポジトリ直下の`pyproject.toml`の`[project] dependencies`へ同じ表記で含める。
  整合は`agent_toolkit_dependencies_invariant_test.py`が確かめる。
  開発用の依存は直下の`[dependency-groups] dev`だけが持ち、agent_toolkitの本番コードからimportしない（`scripts/check_script_imports.py`が検出する）。

## 常駐CLIと自動起動の判定入力

常駐のCLI（`atk wi process-loop`など）の待機と期限、hookからの自動起動、外部の返却の受理を実装・変更する時は、局所の処理の成立と、呼び出し元の目的の成立を分けて判定する。
関数名を禁じる一覧、別の監視プロセス、起動履歴の台帳は設けず、設計の時に次を比べる。

- 待機と期限: 処理を起こすイベント、期限を延ばして起動を許す業務上の事実（対象への投入など）、待機全体の上限を分ける。
  対象外の入力と途切れない入力列を与えた時に、期限が対象の事実だけで動き、呼び出し元へ戻って開始条件、中断、周期の処理を判定し直せるかを比べる。
- 自動起動の集約: イベントの受付と高コストの処理の開始、処理すべき記録と今回起動する条件を分ける。
  単独、連続、同時のイベント列で、目的の集約単位と実際の起動回数を比べ、起動を抑える条件には保持する記録と後続の処理の機会を対応付ける。
- 外部の返却の受理: エージェントや別プロセスの返却は、今回の対象に関わる欠落と不正だけを失敗にし、対象外の記載は記録して除く。
  局所の検証の失敗で常駐全体を止めるかは、その入力が影響してよい範囲から決め、同じ会話や状態を使い回す継続の回も入力列に含めて比べる。

## MCPサーバー識別子とホスト別ツール名

- MCPサーバーの識別子はハイフンを使わず、アンダースコアで作る。
- MCPのツール名は、Claude Codeでは`mcp__plugin_<plugin-name>_<server-key>__<tool>`、Codexでは`mcp__<server-key>__<tool>`と修飾される。
  hookの正規表現のmatcher、権限設定、文書のツール名の一覧は、実行するホストの表記で別々に書く。

## プラグイン内リソースの参照書式

- エンドユーザーの環境で実行するパス（`hooks.json`の`command`、エージェントやスキルの本文の実行コマンドの例など）は`${CLAUDE_PLUGIN_ROOT}/<相対パス>`で書く。
  rootは導入先で決まるため、リポジトリ相対のパスはエンドユーザーの環境で解決に失敗する。
  各ファイルの役割を説明するだけの参照は、リポジトリ相対のパスでよい。
- 別のプラグイン（agent-toolkitからagent-basicsなど）の資料は、配布時に版ごとの別のディレクトリへ置かれて相対パスでは届かないため、読込表でも本文でも`agent-basics:`か`agent-toolkit:`で始まる論理名で指す。
  `agent_toolkit_reference_delivery_invariant_test.py`が、所有プラグインのrootの外を指す相対パスを拒否する。
- Agent PluginsのMCP定義をCodex向けに変換する時は、`args`、`cwd`、`env`の`${CLAUDE_PLUGIN_ROOT}`を`${PLUGIN_ROOT}`へ置き換える。

## 権限設定の配置

- 全てのエンドユーザーに共通の権限設定は、配布原本の`share/claude_settings_json_managed*.json`に書き、`pytools/_internal/update_claude_settings.py`が`~/.claude/settings.json`へ反映する。
  特定のホストか本リポジトリだけの設定は、リポジトリ直下の`.claude/settings.local.json`（Gitの管理外）に置く。
  読み取り専用のコマンドには、引数を指定しない`Bash`の許可ルールを使う。
- 秘匿ファイルの読み取りを禁じる`permissions.deny`は共通の配布原本に書かず、そのファイルがある各プロジェクトの`.claude/settings.json`に置く。
  ディレクトリを含まないglob（`Read(*.key)`など）は任意の深さに一致し、全てのプロジェクトのディレクトリの探索で確認のダイアログを出すためである。
  globの一致の仕方は`agent-basics:writing-standards/references/claude-hooks.md`「PermissionRequest」にある。
- プラグインの有効化は、永続の設定ファイルへの追記だけで済む時は`share/claude_settings_json_managed*.json`の`enabledPlugins`に書く。
  反映の前にCLIの操作が必要なプラグインだけを、`pytools/_internal/install_claude_plugins.py`のautoの一覧に入れる。

## 構造定数の追随

計画ファイルの見出し、固定のH3、表の行名は、`agent-toolkit/agent_toolkit/_plan/structure/constants.py`の構造定数で定める。

1. 改める時は構造定数を変え、同じ名前を使う文書を同じ変更でそろえる。
   対象は`agent-toolkit/skills/atk-mode/references/plan-format.md`と、`agent-toolkit/skills/atk-mode/roles/`の`<役割名>.subagent.md`である。
   `docs/development/`の該当する設計資料と`concepts.md`も対象に含む。
   ユーザーの操作か画面の観測結果が変わる時だけ、対応するガイドも直す。
2. 改める前の名前は読み取りの互換用の構造定数として残し、新規作成と改訂の検証だけでエラーにする。
3. 構造定数を持たず`plan-file-standards-structure.md`だけが必須とする見出しは、同ファイルに従う。
   稼働中の計画が自動チェックで不合格になるのを防ぐためにこの形を選んだ時は、その理由を計画ファイルへ書く。

## atk serveの静的資産

全画面を`agent-toolkit/agent_toolkit/_atk/serve/static/app.css`のトークンと共通のスタイルで組み、画面独自の配色やブラウザー既定のままの操作要素を残さない。
シェルと表示名は[共通スタイルと表示名](../../../../docs/development/design-serve-shell.md#共通スタイルと表示名)に従う。
ユーザビリティレビューを起動する時は、この節の参照先を`主要シナリオ`の「保つ設計意図」へ書く。

- 画面固有のCSSは配置を扱い、共通シェルの高さ・文字サイズを上書きしない。
  本文の装飾は`.markdown-body`へ限定する。
- 共通処理（SSEの購読、一覧の差分描画、警告、ドロワーなど）は`common.js`に置き、画面ごとの差分を引数で渡す。
  `index.html`からESモジュールとして読み、リクエスト固有の値は`serve-bootstrap`などのJSONブロックから取る。
