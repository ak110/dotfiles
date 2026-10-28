# agent-toolkitの実装と配布の規約

本書は、`.claude/skills/agent-toolkit-edit/SKILL.md`の読込表から参照する資料である。`agent-toolkit/`の実装モジュールの配置、`atk`コマンドの出力、MCPサーバーやフックの実装・登録、権限設定、配布と反映に関する規約をまとめている。バージョンの更新手順や同期先の文書については、`SKILL.md`を参照する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:*/skills/agent-toolkit-edit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../dotfiles-development/SKILL.md#「テスト配置」` | 判断 | `section:*/skills/dotfiles-development/SKILL.md#「テスト配置」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「バージョン更新」` | 判断 | `section:*/skills/agent-toolkit-edit/SKILL.md#「バージョン更新」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-messages.md#「エージェント宛てメッセージの標識」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/claude-hooks-messages.md#「エージェント宛てメッセージの標識」` |

## agent_toolkitパッケージの配置と層

- `agent-toolkit/agent_toolkit/`の直下には、配布物の外部から絶対パスで解決される公開スクリプトのみを配置し、先頭にアンダースコア（`_`）を付けない命名にする。対象は`hook.py`、`atk.py`、`agents_server_mcp.py`、`wait_ci.py`である。
- リモートホスト上で実行する`atk_serve_plans_remote_helper.py`と`atk_serve_sessions_remote_helper.py`は、`agent-toolkit/scripts/`直下の公開スクリプトとして配置する。これらは同じチェックアウト環境の`agent_toolkit`を`sys.path`経由でインポートする。また、SSH接続先には標準ライブラリおよび`platformdirs`と`watchdog`のみが用意されているため、両ヘルパーがインポートするモジュールもこれらだけに依存させる。
- 実装モジュールは、`_common`、`_git`、`_plan`、`_atk`、`_agents_server`、`_hooks`の各サブパッケージに配置する。この順序が依存関係の階層を表しており、インポートは後続の層から先行する層への方向、または同じ層の内部に限定する。新規モジュールはそれを読み込む主体が属するサブパッケージに配置し、フック以外からも利用するモジュールは`_hooks`以外の適切な層に配置する。
- 直下の公開スクリプトおよび`agent-toolkit/skills/*/scripts/`配下のスクリプトは、起動スクリプトとしてすべての層をインポートできる。ただし、`_hooks`をインポートできるのは`hook.py`のみとする。
- テスト専用の共有ヘルパーは、依存階層の外側にある`_testing`に配置する。このモジュールをインポートできるのは、`*_test.py`、`conftest.py`、および`_testing`配下のコードのみとする。
- サブパッケージ内では絶対インポートを用いる。`scripts/check_script_imports.py`は相対インポートを解析対象外としているため、相対インポートを使うと階層チェックの対象から漏れてしまう。このスクリプトにより、階層違反や`hook.py`以外からの`_hooks`のインポート、許可されていない箇所からの`_testing`のインポートを検出して報告する。
- モジュール名には、所属を表す接頭辞を除いて、組み込みの名前や標準ライブラリのトップレベル名と重複しない名前を付ける。
- テストコードの配置は、`dotfiles-development`の「テスト配置」の定めに従う。

## atkの実行結果出力

`atk`のサブコマンドや出力メッセージの行を追加・変更する際は、接頭辞の文字列を直接記述せず、`agent-toolkit/agent_toolkit/_atk/outcome.py`で定義されている接頭辞と区分を使用する。新規の末端（リーフ）サブコマンドを追加した場合は、同モジュールの区分テーブルにも登録する（この対応関係は`atk_help_test.py`で自動検証される）。
出力する行は、実行結果、ならびに失敗や警告の詳細および次に実行すべき操作に限定する。リーフコマンドの登録や区分を変更した際は、子プロセスの標準出力が成功メッセージより先に出力される場合も含め、結果行が区分に応じた出力先と順序に従っていることをテストで確認する。
この設計の目的や、検討の末に採用を見送った代替案については、`docs/development/design-cli.md`の「atkの結果行の接頭辞と出力先」に記載している。

## MCPサーバー識別子とホスト別ツール名

- MCPサーバーの識別子にはハイフンを用いず、アンダースコアで構成する。
- MCPツール名は、Claude Code環境では`mcp__plugin_<plugin-name>_<server-key>__<tool>`、Codex環境では`mcp__<server-key>__<tool>`という形式で修飾される。フックの正規表現マッチャー、権限設定、およびドキュメント内のツール名一覧は、実行対象となるホストの表記に合わせて個別に記述する。

## プラグイン内リソースの参照書式

エンドユーザー環境で実行するパス（`hooks.json`の`command`や、エージェントやスキルの本文に記載する実行コマンド例など）は、`${CLAUDE_PLUGIN_ROOT}/<相対パス>`の形式で記述する。プラグインのルートディレクトリはインストール先によって動的に決定されるため、リポジトリ相対パスをそのまま記述するとエンドユーザー環境で実行できなくなる。なお、各ファイルの役割を説明するための参照であれば、リポジトリ相対パスの表記を用いてよい。
別プラグイン（agent-toolkitからagent-basicsなど）の資料は、配布時に版ごとの別ディレクトリへ置かれて相対パスでは届かないため、読込表の行でも本文の参照でも`agent-basics:`や`agent-toolkit:`で始まる論理名表記で指す。`agent-toolkit/read_triggers_invariant_test.py`が所有プラグインのrootの外を指す相対パスを拒否する。
Agent PluginsのMCP定義をCodex環境向けに変換（射影）する際は、`args`、`cwd`、`env`に含まれる`${CLAUDE_PLUGIN_ROOT}`を`${PLUGIN_ROOT}`へ置き換える。

## 権限設定の配置

- すべてのエンドユーザーを対象とする共通の権限設定は、配布原本の`share/claude_settings_json_managed*.json`に記述する。これは`pytools/_internal/update_claude_settings.py`によって`~/.claude/settings.json`へ反映される。特定のホストや本リポジトリのみを対象とする設定は、リポジトリ直下の`.claude/settings.local.json`（バージョン管理対象外）に配置する。読み取り専用コマンドに対しては、引数を指定しない`Bash`の許可ルールを適用する。
- 秘匿ファイルの読み取りを禁止する`permissions.deny`設定は、共通の配布原本には記述せず、該当ファイルが存在する各プロジェクトの`.claude/settings.json`に配置する。`Read(*.key)`のようにディレクトリ指定を含まないグロブパターンは、任意の深さのパスに一致するため、全プロジェクトにおけるディレクトリ探索で確認ダイアログが表示されてしまう。
- プラグインの有効化設定については、永続的な設定ファイルへの追記だけで完結する場合は`share/claude_settings_json_managed*.json`の`enabledPlugins`に記述する。設定を反映する前にCLI操作が必要となるプラグインのみ、`pytools/_internal/install_claude_plugins.py`のauto一覧に含める。

## worktreeでの編集時の注意

別の作業用ワークツリー（worktree）で編集した配布物の内容は、配布処理と再起動して初めて実行中のフックや自動チェックに反映される。現在稼働しているバージョンは、`~/.claude/plugins/installed_plugins.json`の`installPath`を参照して確認する（`~/.claude/plugins/data/`は展開先ではない点に注意する）。すでに保持しているplugin rootが無効になった場合は、このファイルから再度パスを解決する。フックによって新たに処理がブロックされたときは、現在の作業ツリーのファイルと稼働中のバージョンのファイルを比較した上で対応方針を決める。

常駐型のMCPサーバー（`agents_server_mcp.py`など）はプロセス起動時に読み込んだモジュールをメモリー上に保持し続けるため、`agent-toolkit/agent_toolkit/`配下の変更はプロセスを再起動した後に反映される。修正後も同一の現象が発生する場合は、修正が無効であると判断する前に、該当プロセスが読み込んでいるバージョンを特定する。判定にあたっては、起動スクリプトのパスと起動時刻の双方を確認する（別の作業ツリーやplugin rootから起動されたプロセスは、ファイル修正後に起動していても修正前の内容を読み込んでいる場合がある）。

- `ps -eo pid,cmd`を実行して起動スクリプトの絶対パスが対象の配布先と一致することを確認した上で、`stat -c %Y /proc/<PID>`で得られるプロセスの起動時刻と、該当パス配下の対象ファイルの最終更新時刻を比較する。ファイルの更新時刻の方が新しければ、修正前の古い版がプロセスに保持されていると判断できる。なお、`ps -o lstart=`はロケール設定によって出力形式が変わるため比較処理には使用しない。
- プロセスがすでに終了しており当時の実行パスや起動時刻が確認できない場合は、バージョン差異を原因と断定することを避け、現行の配布先において事象が再現するかどうかのみを結論とする。

## フック実装の配置先（個人フックと配布物）

PreToolUseをはじめとするフックは、配布先の構成や個人の命名規則などdotfiles固有の運用を前提とする場合は個人フックに配置し、そうした前提を持たない汎用的な機能であればプラグイン側に配置する。類似のチェックがすでに存在する側に統合することを優先する（努力目標）。

- `pytools/claude_hook/pretooluse.py`（個人フック）: chezmoiを通じて自身のアカウントの`~/.claude/settings.json`にのみマージされる。新規のツール名を対象に含める場合は、`share/claude_settings_json_managed.posix.json`および`.win32.json`の`matcher`設定を確認する。
- `agent-toolkit/`（プラグイン）: `.claude-plugin/marketplace.json`を通じて他のユーザーにも広く配布されるため、変更を加える際は`SKILL.md`の「バージョン更新」の規定に従う。
- agent-toolkitのCLI、フック、MCPサーバーは、`uv run --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py <対象>`というコマンドで起動する。`launch.py`は標準ライブラリのみを用いてプラグインルートとデータ領域を解決し、準備済みの共有環境のPythonで対象を起動する。依存の準備には`uv run --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py --prepare agent_toolkit/atk.py`を実行する。

  `--prepare`の後ろのtargetは配布root内に存在する起動対象を確認する必須引数であり、共有環境の選択には影響しない。依存に作用するpyproject設定、`uv.lock`、選択したPythonの実行ファイルパス、OS・アーキテクチャの組み合わせが未準備の場合に実行する。同じ組み合わせなら別の配布rootやworktreeでも準備済み環境を共有する。別worktreeを指定する場合の`<plugin root>`は、そのworktree内の`agent-toolkit`の絶対パスに置き換える。rootは`launch.py`の配置または`--project`から解決され、cwdの変更だけでは切り替わらない。
- 登録済みの`skills/*/scripts/`配下のスクリプトは、`atk run-script`を用いて同じ環境内から実行する。SSH接続先で実行する`atk_serve_*_remote_helper.py`のみは、独立したPEP 723スクリプトとして起動するため、PEP 723形式の宣言を維持する。

- `agent-toolkit/pyproject.toml`の`dependencies`を変更した場合は、同じプロジェクト配下の`uv.lock`も併せて更新する。
- `hooks.json`や`share/claude_settings_json_managed.*.json`から参照されているスクリプトを改名・移動・削除する場合は、後方互換性を保つためのスクリプトを残してバージョン管理に含める。稼働中のセッションが古いパスを参照し続けるためである（詳細な規約は`agent-basics:writing-standards`のフック実装に関する規約を参照する）。古いスクリプトを完全に撤去できるのは、新しいエントリーポイントを含むバージョンを配布した後のバージョン更新以降であり、かつ古い定義を読み込んだセッションがすべて終了したことを確認できた場合に限る。なお、配布先環境でインストールエラーとなるような配置は新しい配布物に残さず、旧バージョンの実体を保持することや更新時の移行処理によって旧方式の呼び出しを維持する。
- 同一のイベントに対してフックを追加する場合は、既存のエントリーポイントへ処理を統合することを推奨する（登録エントリを増やすとツール呼び出しごとのプロセス起動回数が増えるため）。入力契約の差異などにより統合が難しい場合は、理由を明記した上でエントリを分けて定義してよい。マッチャーの条件が互いに重複しない登録であれば、この方針を満たしているとみなす。

agent-toolkit配下のファイルを編集する際は、`pytools/claude_hook/pretooluse.py`がdotfiles固有の名前の混入を監視して警告する。個人プロジェクト名の固定リストは同スクリプト内に定義されており、OSSとして公開しているプロジェクト名については警告（warning）レベルに留める。スキル名、pytoolsのコマンド名、`pytools/_internal/`配下の複合モジュール名、ならびに`scripts/`や`libexec/`配下のスクリプト名は、実行時にディレクトリから動的に取得される。外部CLIの参照については、`_EXTERNAL_CLI_ALLOWED`に登録されている識別子に限り、`command -v`などによる存在確認を行った上で使用を許可する。

## agents_serverのツール呼び出しを判定するhook

`agents_server`は、指定された`engine`に応じて適切なバックエンドを選択する。ユーザーの承認、対話入力、認証の更新、一覧の取得操作などは公開せず、実行中のターンの中断操作のみをセッション単位の`kill`として公開する。

- PreToolUseフックでは、`send_message`および`kill`における保存済みセッションの妥当性と、`<役割名>.subagent.md`で定義された実行命令を伴う起動であるかを確認する。
- `start`の`delegate`、`explore`、`write`モードに対して、引用枠の外側で`<役割名>.subagent.md`の手順に基づく実行命令を直接渡した場合は、タスク起動形式に修正するよう処理を遮断する。また、`Agent`や`Task`の実行命令においては、1行目に記載された正式な命令および事前に宣言された入力以外の行が含まれている場合に処理を遮断する。
- 文書の読解、引用、比較対象としてのパス参照や、引用内に記載された実行命令の例、参照のみでは用途が確定できないケースについてはフックを通過させ、会話の文脈を推測した過剰な遮断や警告は行わない。
- `start`における入力パラメーターの検証（`mode`ごとの必須項目の不足や不正な混在の検出）や、`send_message`および`kill`の入力形式の検証（`prompt`、`instruction`、`extra_params`、`session_id`の指定不備）は、実行基盤側のバリデーションに委ねる。エラーの原因と次に取るべき操作は、ツールのエラー応答を通じて伝達する。フック側にツールと同じ受理条件を持たせると、ツール側の入力形式が拡張された際に正当な入力に対して誤った警告を発してしまうためである。実行権限の値はそのままツールへ渡す。なお、`wait`は新しいターンを開始しないためチェック対象外とする。
- PostToolUseフックでは、成功した`start`呼び出し（旧名称で記録された開始処理を含むすべての`mode`が対象）のカレントワーキングディレクトリ（cwd）と、`wait`、`send_message`、`kill`実行後のセッション状態を記録する。

## 複数hook共存時の識別子

`atk-auto`タグの`source`属性には、agent-toolkit由来のメッセージであれば接頭辞を付けない生成元名を指定し、それ以外の生成元であれば`<所有者>/<生成元>`という形式を指定する。XMLタグの境界や属性の記述規約については、`agent-basics:writing-standards`の`references/claude-hooks-messages.md`「エージェント宛てメッセージの標識」を参照する。

## marketplace管理

- `update-dotfiles`（`chezmoi apply`の事後処理）は、`pytools/_internal/install_claude_plugins.py`を実行してClaude Code向けのagent-toolkitを自動的にインストールおよび更新する。`agent-toolkit/rules/`配下のルールは`post_apply`の`sync_agent_toolkit_rules`によって配布先へ同期され、Codex向けのdotfiles固有スキルや`docs`ディレクトリは`setup_codex_links`によって原本へのシンボリックリンクが作成される。
- 生成ファイルの一括同期は、`uv run python scripts/sync_generated_files.py`を実行して行う（サブコマンドとして`python`を明示する必要がある。詳細は`docs/development/operations.md`を参照する）。
- マーケットプレイスの登録形式について、初期セットアップ（bootstrap）時には`install-claude.sh`や`install-claude.ps1`がGitHub参照型として登録する。その後の`chezmoi apply`の事後処理によってローカルディレクトリ参照型（絶対パス参照）へと維持管理される（GitHub型の設定が残っている場合も自動で移行される）。ローカル環境での編集内容は、`chezmoi apply`（または`update-dotfiles`）を実行した上でClaude Codeを再起動するか、`/reload-plugins`コマンドを実行することで即座に反映される（この際、バージョン番号の更新は不要である）。
- Codex向けの生成ファイルである`agent-toolkit/.codex-plugin/plugin.json`および`.agents/plugins/marketplace.json`は、prek経由で実行されるpyfltrの`sync-generated-files`によって毎回自動で再生成される。また、Codex用のフック定義は、`pytools/_internal/codex_plugin_manifests.py`がイベント名、マッチャー、入力契約を検証した許可リストに基づいて生成する（手動実行は`scripts/sync_codex_plugin_manifests.py`で行う）。`chezmoi apply`の事後処理がCodexのマーケットプレイスを登録し、プラグインの導入と更新を完了させる。
