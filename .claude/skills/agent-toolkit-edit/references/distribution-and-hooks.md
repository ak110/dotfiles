# agent-toolkitの実装と配布の規約

本書は`.claude/skills/agent-toolkit-edit/SKILL.md`の読込表から読む参照資料であり、`agent-toolkit/`の実装モジュールの配置、`atk`の出力、MCPサーバーとhookの実装と登録、権限設定、配布と反映の規約を持つ。版数更新と同期先の文書は`SKILL.md`が定める。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:*/skills/agent-toolkit-edit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../dotfiles-development/SKILL.md#「テスト配置」` | 判断 | `section:*/skills/dotfiles-development/SKILL.md#「テスト配置」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「バージョン更新」` | 判断 | `section:*/skills/agent-toolkit-edit/SKILL.md#「バージョン更新」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-messages.md#「エージェント宛てメッセージの標識」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/claude-hooks-messages.md#「エージェント宛てメッセージの標識」` |

## agent_toolkitパッケージの配置と層

- `agent-toolkit/agent_toolkit/`直下には配布物の外部から絶対パスで解決される公開スクリプトだけを置き、接頭辞`_`を付けずに命名する。対象は`hook.py`・`atk.py`・`agents_server_mcp.py`・`wait_ci.py`・`_managed_temp.py`である。`_managed_temp.py`は外部の許可判定がパスを解決するため名前を維持する（`script_prefix_invariant_test.py`が除外する）
- リモートホストで実行する`atk_serve_plans_remote_helper.py`と`atk_serve_sessions_remote_helper.py`は`agent-toolkit/scripts/`直下の公開スクリプトとする。同じcheckoutの`agent_toolkit`を`sys.path`経由でimportし、SSH先は標準ライブラリと`platformdirs`・`watchdog`だけを与えるため、両ヘルパーがimportするモジュールはそれらだけに依存させる
- 実装モジュールは`_common`・`_git`・`_plan`・`_atk`・`_agents_server`・`_hooks`のサブパッケージへ置き、この順を依存の層とする。importは後ろの層から前の層への向きと、同じ層の中に限る。新しいモジュールは読み込む主体が属するサブパッケージへ置き、hook以外からも使うものは`_hooks`へ置かない
- 直下の公開スクリプトと`agent-toolkit/skills/*/scripts/`配下は起動スクリプトとして全層をimportできるが、`_hooks`をimportできるのは`hook.py`だけとする
- テスト専用の共有ヘルパーは層の外の`_testing`へ置き、`*_test.py`・`conftest.py`・`_testing`配下だけがimportする
- サブパッケージ内は絶対importを使う。`scripts/check_script_imports.py`は相対importを解析しないため、相対importにすると層の自動チェックの被覆が失われる。同スクリプトが層違反、`hook.py`以外からの`_hooks`のimport、許可外からの`_testing`のimportを報告する
- モジュール名は所属を表す接頭辞を除き、組込み名や標準ライブラリのトップレベル名と重ならない名前にする
- テストの配置は`dotfiles-development`「テスト配置」に従う

## atkの実行結果出力

`atk`のサブコマンドや出力の行を追加・変更する場合は、`agent-toolkit/agent_toolkit/_atk/outcome.py`の接頭辞と区分を使い、接頭辞の文字列を直接書かない。新しいリーフサブコマンドは同モジュールの区分表へ加える（対応は`atk_help_test.py`が検証する）。
出力する行は結果と、失敗・警告とその次の操作に限る。リーフの登録や区分の変更では、結果行が区分の出力先と順序を満たすこと（子プロセスの標準出力が成功行より前に出る場合を含む）をテストで確かめる。
目的と却下した代替案は`docs/development/design-cli.md`「atkの結果行の接頭辞と出力先」にある。

## MCPサーバー識別子とホスト別ツール名

- MCPサーバー識別子はハイフンを使わずアンダースコアで構成する
- MCPツール名はClaude Codeでは`mcp__plugin_<plugin-name>_<server-key>__<tool>`、Codexでは`mcp__<server-key>__<tool>`と修飾される。hook matcher・権限設定・文書の列挙は対象ホストの綴りにそろえ、片方の綴りを流用しない

## プラグイン内リソースの参照書式

エンドユーザー環境で実行するパス（`hooks.json`の`command`、エージェント・スキル本文の実行コマンド例）は`${CLAUDE_PLUGIN_ROOT}/<相対パス>`で書く。プラグインのルートはインストール先で動的に決まり、リポジトリ相対パスはエンドユーザー環境で実行不能となる。役割を説明する言及はリポジトリ相対表記のままでよい。
Agent PluginsのMCP定義をCodexへ射影する際は、`args`・`cwd`・`env`の`${CLAUDE_PLUGIN_ROOT}`を`${PLUGIN_ROOT}`へ変換する。

## 権限設定の配置

- 全エンドユーザー向けの権限は配布原本`share/claude_settings_json_managed*.json`へ置く（`pytools/_internal/update_claude_settings.py`が`~/.claude/settings.json`へ反映する）。特定ホスト・本リポジトリ限定の内容はリポジトリ直下の`.claude/settings.local.json`（管理対象外）へ置く。読み取り専用コマンドには引数なしの`Bash`許可を適用する
- 秘匿ファイルの読み取りを禁止する`permissions.deny`は配布原本へ置かず、そのファイルを持つプロジェクトの`.claude/settings.json`へ置く。`Read(*.key)`のようなディレクトリを含まないグロブは任意の深さに一致し、全プロジェクトのディレクトリ走査が確認ダイアログの対象になる
- プラグインの有効化は、永続的な設定値だけで再現できる場合は`share/claude_settings_json_managed*.json`の`enabledPlugins`へ置く。設定反映前にCLI操作が要る場合だけ`pytools/_internal/install_claude_plugins.py`のauto一覧を使う

## worktreeでの編集時の注意

作業用の複製で改訂した配布物が実行中のhookや自動チェックへ反映されるのは、配布と再起動の後である。稼働中の版は`~/.claude/plugins/installed_plugins.json`の`installPath`で確認し（`~/.claude/plugins/data/`は展開先ではない）、保持済みのplugin rootが失効したら同ファイルから再解決する。hookに新たにブロックされた場合は、作業ツリーと稼働中の版のファイルを比較してから対応する。

常駐MCPサーバー（`agents_server_mcp.py`など）は起動時に読み込んだモジュールを保持するため、`agent-toolkit/agent_toolkit/`の修正はプロセスの再起動後に反映される。修正後も同じ事象を観測した場合は、修正が無効と結論する前にプロセスが読み込んだ版を確定する。判定には起動スクリプトのパスと時刻の両方を使う（別の作業ツリーやplugin rootから起動したプロセスは、修正より後の起動でも修正を含まないことがある）。

- `ps -eo pid,cmd`で起動スクリプトの絶対パスが対象の配布先であることを確かめ、`stat -c %Y /proc/<PID>`のプロセス起動時刻と、そのパス配下の対象ファイルの更新時刻を比べる（ファイルが新しければ修正前の版を保持している）。`ps -o lstart=`はロケールで表記が変わるため比較に使えない
- 終了済みのプロセスで当時のパスと起動時刻が無い場合は、版差を原因と決めつけず、現行の配布先での再現可否だけを結論とする

## フック実装の配置先（個人フックと配布物）

PreToolUseなどのフックは、dotfiles固有の運用前提（配布先構成・個人の命名など）を前提とするなら個人フック、その前提を持たない汎用機能ならプラグインへ置き、類似チェックがある側への統合を優先する（努力目標）。

- `pytools/claude_hook/pretooluse.py`（個人フック）: chezmoi経由で自分の`~/.claude/settings.json`にだけマージされる。新しいツール名を扱う場合は`share/claude_settings_json_managed.posix.json`と`.win32.json`の`matcher`を確認する
- `agent-toolkit/`（プラグイン）: `.claude-plugin/marketplace.json`経由で他者にも配布されるため、`SKILL.md`「バージョン更新」に従う
- agent-toolkitのCLI・hook・MCPは、`uv run --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py <対象>`から起動する。launch.pyは標準ライブラリだけでrootとdataを解決してから、`uv run --project <plugin root> --locked --no-default-groups`で依存を配布root外へ置く。登録済みの`skills/*/scripts/`は`atk run-script`で同じ環境内から実行する。SSH先の`atk_serve_*_remote_helper.py`だけは独立したPEP 723スクリプトとして起動し、PEP 723宣言を維持する
- `agent-toolkit/pyproject.toml`の`dependencies`を変えたら同projectの`uv.lock`も更新する
- `hooks.json`と`share/claude_settings_json_managed.*.json`が参照するスクリプトを改名・移動・削除する場合は、互換スクリプトを残してバージョン管理へ含める。稼働中のセッションは旧パスを参照し続けるためである（規約は`agent-basics:writing-standards`のhook実装の規約）。撤去は新しいエントリーポイントを含む版を配布した後の版数更新以降で、旧定義を読み込んだセッションの全終了を確認できた場合だけとする。配布先がインストールを拒否する配置は新配布物へ残さず、旧版の実体保持と更新時の移行で旧呼出しを保つ
- 同じイベントへフックを追加する場合は、既存のエントリーポイントへの相乗りを推奨する（登録を並べるとツール呼び出しごとの起動が増える）。入力契約の違いで相乗りできない場合は理由を残して並べてよく、matcherが互いに素な登録はこの方針を満たすとみなす

agent-toolkit配下の編集では、`pytools/claude_hook/pretooluse.py`がdotfiles固有名の混入を警告する。個人プロジェクト名の固定リストは同スクリプト内にあり、OSS公開プロジェクト名はwarningに留める。スキル名・pytoolsコマンド名・`pytools/_internal/`の複合名モジュール・`scripts/`と`libexec/`のスクリプト名は実行時にディレクトリから動的に取得する。外部CLI参照は`_EXTERNAL_CLI_ALLOWED`の登録識別子に限り、`command -v`等の存在確認を経て許容する。

## agents_serverのツール呼び出しを判定するhook

`agents_server`は`engine`に応じたバックエンドを選ぶ。承認、ユーザー入力、認証更新、一覧操作は公開せず、実行中turnの中断だけをsession単位の`kill`として公開する。

- PreToolUseは`send_message`・`kill`の保存済みsessionと、`<役割名>.subagent.md`の実行命令を持つ起動を確認する
- `start`の`delegate`・`explore`・`write`へ引用の外で`<役割名>.subagent.md`の手順の実行命令を渡した場合は、タスク起動へ直すよう遮断する。`Agent`・`Task`の実行命令では、1行目の正式な命令と宣言済み入力以外の行を遮断する
- 文書の読解・引用・比較の対象への参照、引用に載せた実行命令の例、参照だけでは用途を確定できない場合は通し、会話の意味を推定する遮断・警告を加えない
- `start`の入力検証（`mode`ごとの欠落と混在を含む）と、`send_message`・`kill`の入力形式（`prompt`・`instruction`・`extra_params`・`session_id`の欠落と併用）の検証は実行基盤へ委ね、原因と次の操作はツールのエラー応答で返す。hookが同じ受理条件を持つと、ツールが入力形式を追加したときに正当な入力を誤って警告するためである。実行権限値はそのまま渡す。`wait`は新しいturnを開始しないためチェック対象外とする
- PostToolUseは成功した`start`（全`mode`。旧名で記録された開始も含む）のcwdと、`wait`・`send_message`・`kill`のsession状態を記録する

## 複数hook共存時の識別子

`atk-auto`の`source`はagent-toolkitでは接頭辞の無い生成元名、他の生成元なら`<所有者>/<生成元>`とする。XML境界と属性の規約は`agent-basics:writing-standards`の`references/claude-hooks-messages.md`「エージェント宛てメッセージの標識」に従う。

## marketplace管理

- `update-dotfiles`（`chezmoi apply`後処理）は`pytools/_internal/install_claude_plugins.py`でClaude Code向けagent-toolkitを自動インストール・更新する。`agent-toolkit/rules/`は`post_apply`の`sync_agent_toolkit_rules`が配布先へ同期し、Codex向けのdotfiles固有スキルと`docs`は`setup_codex_links`が原本へリンクする
- 生成物の一括同期は`uv run python scripts/sync_generated_files.py`で起動する（`python`の明示が必須。詳細は`docs/development/operations.md`）
- marketplaceは、bootstrapでは`install-claude.sh`/`install-claude.ps1`がGitHub型として登録し、`chezmoi apply`の後処理がdirectory型（絶対パス参照）で維持する（GitHub型の残存は自動移行する）。ローカル編集は`chezmoi apply`（または`update-dotfiles`）の後、Claude Code再起動か`/reload-plugins`で反映される（version bumpは不要）
- Codex向け生成物は`agent-toolkit/.codex-plugin/plugin.json`と`.agents/plugins/marketplace.json`で、prek経由のpyfltrの`sync-generated-files`が毎回再生成する。Codex hookの定義は`pytools/_internal/codex_plugin_manifests.py`がイベント名、matcher、入力契約を確認した許可表の分だけを生成する（起動は`scripts/sync_codex_plugin_manifests.py`）。`chezmoi apply`後処理がCodex marketplaceを登録し、pluginを導入・更新する
