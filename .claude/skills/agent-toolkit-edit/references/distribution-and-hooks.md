# agent-toolkitの実装と配布の規約

本書は`.claude/skills/agent-toolkit-edit/SKILL.md`の読込表から読む参照資料であり、`agent-toolkit/`の実装モジュールの配置、`atk`の出力、MCPサーバーとhookの実装と登録、権限設定、配布と反映の手順の規約を持つ。版数更新と同期先の文書は`SKILL.md`が定める。

## agent_toolkitパッケージの配置と層

`agent-toolkit/agent_toolkit/`直下には配布物の外部から絶対パスで解決される公開スクリプトだけを置く。
対象とする公開スクリプトは`hook.py`・`atk.py`・`agents_server_mcp.py`・`wait_ci.py`・`_managed_temp.py`とする。
リモートホスト上で読み込んで実行する`atk_serve_plans_remote_helper.py`と`atk_serve_sessions_remote_helper.py`は
`agent-toolkit/scripts/`直下に置く。これらも公開スクリプトとする。
両ヘルパーは同じcheckoutの`agent_toolkit`を`sys.path`経由でimportする。
SSH先の実行環境は標準ライブラリのほかに`platformdirs`と`watchdog`だけを与えるため、ヘルパーがimportする`agent_toolkit`のモジュールはそれらだけに依存させる。
実装モジュールは責務ごとのサブパッケージ`_common`・`_git`・`_plan`・`_atk`・`_agents_server`・`_hooks`へ置く。
この6つを依存の層とし、この並び順を層の順序とする。
後ろの層は前の層をimportしてよく、前の層は後ろの層をimportしない。
同じ層の中のimportは制限しない。
直下の公開スクリプトと`agent-toolkit/skills/*/scripts/`配下のスクリプトは層の外から起動されるスクリプト（以下、起動スクリプト）として全ての層をimportできるが、`_hooks`をimportできるのは`hook.py`だけとする。
hook以外からも使うモジュールは`_hooks`へ置かず、`_common`などの前の層へ置く。
テスト専用の共有ヘルパーは`_testing`へ置く。
`_testing`は層の順序に含めない例外とし、`*_test.py`・`conftest.py`と`_testing`配下のモジュールだけがimportできる。
新しいモジュールの追加先は、そのモジュールを読み込む主体が属するサブパッケージで判定する。
直下の公開スクリプトは接頭辞`_`を付けずに命名する。
`_managed_temp.py`だけは外部の許可判定がそのパスを解決するため名前を維持し、`agent-toolkit/agent_toolkit/script_prefix_invariant_test.py`がこの1件を除外する。

サブパッケージ内のimportには絶対importを使う。
`scripts/check_script_imports.py`が相対importを解析の対象にせず、相対importへ変えるとimport到達性の自動チェックの被覆が失われるためである。
同スクリプトはサブパッケージ、直下の公開スクリプトと`skills/*/scripts/`配下を走査し、層の順序に反するimport、`hook.py`以外の起動スクリプトからの`_hooks`のimport、前段の3種以外のモジュールからの`_testing`のimportを失敗として報告する。
モジュール名からは所属を表す接頭辞を除き、Pythonの組込み名と標準ライブラリのトップレベル名とは異なる名前を選ぶ。
テストは`dotfiles-development`「テスト配置」に従い、対象モジュールの動作テストを同居させ、実物の文書や設定を読むテストをその近くへ置く。

## atkの実行結果出力

`atk`のサブコマンドを追加する場合と、出力の行を追加または変更する場合は、
`agent-toolkit/agent_toolkit/_atk/outcome.py`が定める接頭辞と区分を使う。
接頭辞の文字列を各出力箇所へ直接書かず、新しいリーフサブコマンドは同モジュールの区分表へ加える。
出力する行は実行したコマンドの結果と、失敗・警告およびその次の操作に限る。結果と無関係な状況の通知、対処を要しない付随処理の報告、毎回同じ固定の案内、同じ内容の反復は加えず、人向けの補足として環境判定で振り分けない。
リーフを登録する場合と区分を変える場合は、実行した結果行がその区分の出力先と順序を満たすことをテストで確かめ、子プロセスの標準出力が成功行より前に出る場合も同じ変更単位で検証する。
区分表と実在するリーフの対応は`agent-toolkit/agent_toolkit/atk_help_test.py`が検証する。
規約の目的、区分の意味、却下した代替案は`docs/development/design-cli.md`「atkの結果行の接頭辞と出力先」が持つ。

## MCPサーバー識別子とホスト別ツール名

- MCPサーバー識別子にはハイフンを使わず、アンダースコアで構成する。MCPツール名はホストごとの修飾規則が異なるため、片方の綴りを別ホストへ流用しない
- Claude CodeのMCPツール名は`mcp__plugin_<plugin-name>_<server-key>__<tool>`、CodexのMCPツール名は`mcp__<server-key>__<tool>`で修飾する。hook matcher・権限設定・文書の列挙は対象ホストの綴りへそろえる

## プラグイン内リソースの参照書式

エンドユーザー環境で実行される実行時パス（`hooks.json`の`command`・エージェント/スキル本文の実行コマンド例）は`${CLAUDE_PLUGIN_ROOT}/<相対パス>`形式に統一する。
プラグイン配布物のルートはインストール先で動的に解決されるため、dotfilesリポジトリ相対パスはエンドユーザー環境で実行不能となる。
エージェント向け文書内で役割を説明する言及（「〜は`agent-toolkit/agent_toolkit/<name>.py`が担う」等）はリポジトリ相対表記のままでよい。
判定基準はそのパスをエンドユーザー環境で実行するか否かとする。
Agent PluginsのMCP定義をCodexへ射影する場合は、`args`・`cwd`・`env`の各値に含まれる`${CLAUDE_PLUGIN_ROOT}`を`${PLUGIN_ROOT}`へ変換する。Claude Code側の実行時パスは前項の形式を維持する。

## 権限設定の配置

権限設定を変更する場合は、対象が全エンドユーザー向けかを先に判定する。
全エンドユーザー向けの内容は配布原本`share/claude_settings_json_managed*.json`へ置く
（`pytools/_internal/update_claude_settings.py`が`~/.claude/settings.json`へ反映する）。
特定ホスト・本リポジトリ限定の内容はリポジトリ直下の`.claude/settings.local.json`（バージョン管理対象外）へ置く。
読み取り専用コマンドには、引数なしの`Bash`許可を適用する。

秘匿ファイルの読み取りを禁止する`permissions.deny`は配布原本へ置かず、そのファイルを持つプロジェクトのリポジトリ直下の`.claude/settings.json`へ置く。
`Read(*.key)`のようにディレクトリを含まないグロブを配布原本へ置くと、gitignore構文で任意の深さに一致するため、全プロジェクトのディレクトリ走査が確認ダイアログの対象となる。

プラグインの有効・無効は、永続的な設定値だけで再現できる場合に
`share/claude_settings_json_managed*.json`の`enabledPlugins`へ置く。設定反映前に
インストール済みプラグインへCLI遷移が必要な場合だけ
`pytools/_internal/install_claude_plugins.py`のauto一覧を使う。新規登録は前段の判定で置き場所を決め、既存の歴史的重複はその判定の入力から外す。

## worktreeでの編集時の注意

作業用の複製（git worktree等）で配布物（`agent-toolkit/`配下等）を改訂しても、
実行中のhookや自動チェックにはそのセッションでは反映されない。稼働中の版は
`~/.claude/plugins/installed_plugins.json`の`installPath`で確認する。
保持済みのplugin rootが失効した場合は同ファイルから現行の導入版と`installPath`を再解決し、利用する資源の実在を確認する。
plugin本体の展開先は`installPath`が示す位置とし、`~/.claude/plugins/data/`配下はその対象から外す。
hookに新規にブロックされた場合は、まず作業ツリーと稼働中の版に差異がないか確認し、
そのhookが参照する配布先のファイルを`diff`等で比較してから対応する。

常駐するMCPサーバープロセス（`agent-toolkit/agent_toolkit/agents_server_mcp.py`等）は起動時に読み込んだ
Pythonモジュールを保持し続ける。このため、`agent-toolkit/agent_toolkit/`配下の修正はそのプロセスの再起動後に反映される。
修正の確定後も同じ事象を観測した場合は、修正が無効であると結論する前にそのプロセスが読み込んだ版を確定する。
確定には次の順の観測を用い、起動時刻だけの比較はその根拠から外す。別の作業ツリーや別のplugin rootから起動したプロセスは、
修正commitより後に起動していてもその修正を含まないファイルを読み込み得るためである。

1. `ps -eo pid,cmd`で稼働プロセスのPIDと起動スクリプトの絶対パスを取得し、そのパスが対象の配布先であることを確認する
2. `stat -c %Y /proc/<PID>`でプロセス起動時刻を、`stat -c %Y <そのパス配下の対象ファイル>`でファイル更新時刻を取得する
   （`ps -o lstart=`の出力は実行環境のロケールにより`date -d`が解釈できないため、時刻の比較には用いない）
3. ファイル更新時刻がプロセス起動時刻より後であれば、そのプロセスは修正前の版を保持している

過去に終了したプロセスについては同じ証拠を回収できない。
当時の起動スクリプトのパスと起動時刻を保持していない場合は、版差を原因と決めつけず、現行の配布先での再現可否を現物で確かめた範囲だけを結論とする。

## フック実装の配置先（個人フックと配布物）

自動化手段の選定は`agent-toolkit:writing-standards`の振り分け規定と本節に従う。

PreToolUseは、dotfiles固有の運用前提（配布先構成・個人の命名など）への依存で配置を決める。依存する機能は個人フック、依存しない汎用機能はプラグインへ置く。
類似チェックがある側への統合を優先する（努力目標。定義と改訂箇所を1か所にする）。

- `pytools/claude_hook/pretooluse.py`（個人フック）: chezmoi経由で自分の`~/.claude/settings.json`にのみマージされる。
  配置した場合は`share/claude_settings_json_managed.posix.json`および同`win32.json`の
  `matcher`に新しいツール名を追加する必要があるか確認する
- `agent-toolkit/`（プラグイン）: `.claude-plugin/marketplace.json`経由で他者にも配布される。
  配置した場合は`SKILL.md`「バージョン更新」の手順に従う
- agent-toolkitの公開スクリプトは`uv run --project <plugin root> --locked --no-default-groups <対象>`形式で呼び出す。
  対象は`agent-toolkit/hooks/hooks.json`、MCP manifest、`agent-toolkit/bin/atk`および`agent-toolkit/skills/*/scripts/`配下のスクリプトである。
  SSH先で動く`agent-toolkit/scripts/atk_serve_*_remote_helper.py`だけは独立したPEP 723スクリプトとして起動する
- `agent-toolkit/hooks/hooks.json`と`share/claude_settings_json_managed.*.json`が参照するスクリプトを改名・移動・削除する場合は、
  `agent-toolkit:writing-standards`がhook実装の規約として定める互換スクリプトの残置に従う。
  残置した互換スクリプトはバージョン管理の対象へ含める。
  撤去は新しいエントリーポイントを含む版をbumpして配布した後の版数更新以降であり、かつ旧定義を読み込んだセッションが全て終了したことを
  確認できた場合だけ行う。確認できない場合は残置を維持する
- `agent-toolkit/pyproject.toml`の`dependencies`へパッケージを追加・更新する場合、
  同projectの`uv.lock`も更新する。`agent-toolkit/scripts/`に残す`atk_serve_*_remote_helper.py`だけはPEP 723宣言を維持する
- 同じイベントへフックを追加する場合は、`agent-toolkit/hooks/hooks.json`と
  `share/claude_settings_json_managed.*.json`のいずれでも、そのイベントの既存のエントリーポイントへ相乗りさせることを推奨する。
  登録を並べるとプロセスの起動が増え、ツール呼び出しのたびに遅延が加わる。
  入力契約の違いなどで相乗りできない場合は、その理由を残して登録を並べてよい。
  matcherが互いに素で同時に発火しない登録は、この方針を満たしているものとして扱う。
  イベントごとのエントリーポイントの実装契約は`agent-toolkit:writing-standards`がhook実装の規約として定める

agent-toolkit配下の編集時、dotfiles固有名の混入を`pytools/claude_hook/pretooluse.py`の専用チェックが警告し、一般化した表現への置き換えを求める。
個人プロジェクト名固定リストはそのスクリプト内で定義し、OSS公開プロジェクト名はwarning通知に留める。
スキル名・pytoolsコマンド名・`pytools/_internal/`の複合名モジュール・`scripts/`と`libexec/`のスクリプト名は、`pytools/claude_hook/pretooluse.py`がhook実行時にディレクトリをスキャンして動的に取得する。
外部CLI参照は`_EXTERNAL_CLI_ALLOWED`登録識別子に限り`command -v`等による存在確認を経て許容する。

## agents_serverのツール呼び出しを判定するhook

本節はagent-toolkitのPreToolUseとPostToolUseが`agents_server`のツール呼び出しで確かめる内容と記録する状態を定める。

`agents_server`では`engine`に応じたバックエンドをMCPサーバーが選択する。承認、ユーザー入力、認証更新および一覧操作は公開せず、実行中turnの明示的な中断だけをsession単位の`kill`として公開する。
PreToolUseは`send_message`・`kill`の保存済みsessionと、`<役割名>.subagent.md`の実行命令を持つ起動を確認する。
`start`の`delegate`・`explore`・`write`へ引用の外で`<役割名>.subagent.md`の手順を実行する命令を渡した場合は、タスク起動へ直すよう遮断する。
`Agent`・`Task`の実行命令では、1行目の正式な命令と宣言済み入力以外の行を遮断する。
文書の読解・引用・比較の対象への参照と、引用に載せた実行命令の例は通す。
参照だけから用途を確定できない場合も通し、会話の意味を推定する遮断・警告を加えない。
`start`の入力妥当性検証（`mode`ごとの欠落と混在を含む）は実行基盤へ委ね、入力の実行権限値はそのまま渡す。
`wait`は新しいturnを開始せず既存sessionの現在の状態を返すだけで、誤った作業ディレクトリでの実行を招かないため、PreToolUseのチェック対象へ含めず通過させる。
PostToolUseは成功した開始ツール`start`（全`mode`。統合前の旧名で記録された開始も含む）のcwdと、`wait`・`send_message`・`kill`のsession状態を記録する。

## 複数hook共存時の識別子

同一イベントで共存するhookを識別するため、`atk-auto`の`source`はagent-toolkitなら接頭辞の無い生成元名、他の生成元なら`<所有者>/<生成元>`とする。
XML境界と属性の規約は`agent-toolkit:writing-standards`の`references/claude-hooks-messages.md`「エージェント宛てメッセージの標識」に従う。

## marketplace管理

`update-dotfiles`（`chezmoi apply`後処理）はClaude Code向けagent-toolkitプラグインを自動インストール・更新する。
処理は`pytools/_internal/install_claude_plugins.py`が担う。
`agent-toolkit/rules/`は`post_apply`の`sync_agent_toolkit_rules`が配布先へ同期し、Codex向けのdotfiles固有スキルと`docs`は`setup_codex_links`が原本へリンクする。
生成物の一括同期は`uv run python scripts/sync_generated_files.py`で起動する
（`python`の明示が必須。起動形の詳細は`docs/development/operations.md`を参照）。
marketplaceの配布方式は次のとおり。

- bootstrap: `install-claude.sh`/`install-claude.ps1`がGitHub型として登録する
- chezmoi apply: 後処理がdirectory型（絶対パス直接参照）で維持し、GitHub型登録残存時は自動でマイグレーションする
- ローカル編集の反映: `chezmoi apply`（または`update-dotfiles`）でデプロイし、
  Claude Code再起動か`/reload-plugins`で反映する（version bumpは不要）

Codex向け生成物は`agent-toolkit/.codex-plugin/plugin.json`と`.agents/plugins/marketplace.json`とする。
生成器と生成元の関係は`SKILL.md`「バージョン更新」に従う。
prek経由のpyfltr（書き込みモード）が`sync-generated-files`でCodex向け生成物を毎回再生成する。
Codex hookの定義は、`pytools/_internal/codex_plugin_manifests.py`がイベント名、matcher、入力契約を確認した許可表の分だけを生成する（起動スクリプトは`scripts/sync_codex_plugin_manifests.py`）。
`chezmoi apply`後処理はCodex marketplaceを登録し、agent-toolkit pluginを導入・更新する。
