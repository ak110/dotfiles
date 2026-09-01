---
name: agent-toolkit-edit
description: >
  `agent-toolkit/`配下のプラグイン（スキル・サブエージェント・フックスクリプト・marketplace記述）、
  `agent-toolkit/rules/`配下のルールファイル（配布先`~/.claude/rules/agent-toolkit/`）、
  `.claude-plugin/marketplace.json`を編集するときに使う。
  「agent-toolkit編集」「version bump」「marketplace管理」「セッション状態フラグ」などのキーワードでも起動する。
---

# agent-toolkit（Agent Plugins・Claude Code・Codex）

## ファイル構成と参照方向

- `agent-toolkit/`配下: Agent Plugins・Claude Code・Codexが共有するプラグインルート
- `agent-toolkit/rules/`配下: ルールファイル（`01-agent.md`は基本原則、`02-agent-operations.md`は製品横断の実行運用、`99-claude-code.md`はClaude Code固有事項を担う）
- `~/.claude/rules/agent-toolkit/`: ルールファイルの配布先（直接編集不可）
- `agent-toolkit/rules/`配下はサブディレクトリを設けずフラット構造を保つ
- 配布物完結の環境変数は`AGENT_TOOLKIT_<PURPOSE>`形式とする
  （代表例は`AGENT_TOOLKIT_PRIVATE_NOTES`。`atk mq`管理repoのroot、既定`~/private-notes/`）。
  個人環境完結は`DOTFILES_`を使う。個別の環境変数の一覧と用途は
  `<plugin root>/skills/agent-standards/references/claude-hooks.md`が扱う

参照方向はdotfilesリポジトリ→プラグイン、およびプラグイン↔ルールファイルを許容する。
配置先は「いつコンテキストへ読み込ませたいか」で判断する。

- 常時自動ロードしたい一般指針はルールファイルへ置く
- 特定タスクでのみ必要な指針はスキル本体に残す
- 配置先は表層識別子ではなく、規範の成立条件が依存する対象で判定する
- プロジェクト固有のツール、データ、命名、CI、運用経路へ依存する内容はプロジェクト側へ置く
- 固有要素を同種の任意要素へ置換しても判定基準、工程順序、停止条件が成立する内容だけを配布物候補とする

### MCPサーバー識別子とホスト別ツール名

- MCPサーバー識別子にはハイフンを使わず、アンダースコアで構成する。MCPツール名はホストごとの修飾規則が異なるため、片方の綴りを別ホストへ流用しない
- Claude CodeのMCPツール名は`mcp__plugin_<plugin-name>_<server-key>__<tool>`、CodexのMCPツール名は`mcp__<server-key>__<tool>`で修飾する。hook matcher・権限設定・文書の列挙は対象ホストの綴りへそろえる

## 簡潔化・削減編集での消失検査

簡潔化・整理・削減を目的とする編集では、ベースコミットとの差分から削除された規範・手順・判定基準の
記述を洗い出し、各削除について重複解消・意図的な設計転換・発火条件消滅のいずれかの理由を
進捗ログまたはコミットメッセージへ記録する。
理由を示せない削除は復元するか、独立コンテキストのレビューで実害の有無を確認してから確定する。

## 配布物としての記述方針

配布先のエンドユーザーは本リポジトリのdotfiles利用者とは限らないため、手元プロジェクト固有の前提を断定的に書かない。

- 自己言及的な表現・特定設定値の前提・特定ディレクトリ構成の前提を断定せず、
  異なり得る条件は条件付き表現（「`～`設定が有効な場合、」など）で書く
- 仕様参照としてのルール名・設定キー名・選択肢の説明は記述してよい
- 配布物のdocstring・コメント・本文には配布物自身の挙動・仕様のみを記述し、
  エンドユーザー環境側の連携設計（個人フックとの優先順序など）は書かない
- 配布物の出力文字列・フックメッセージ・docstringにリポジトリ管理外の個人メモファイル名を含めない
- 配布物内の記述が参照するSSOTは配布物内に配置し、dotfiles固有ファイル・非配布対象ファイルを参照先にしない
- 配布物文面は実ファイル編集時に`scripts/claude_hook_pretooluse.py`の固有名検査を適用し、
  検出した個人環境固有の識別子を一般化表現へ置き換える
- 配布物スキル本文でhook内部の実装挙動
  （ハッシュ照合・SHA256記録・ブロック機構・状態フラグ書き込み等）を説明する記述を書かない。
  エンドユーザーには挙動の観測結果（特定操作がブロックされる・警告が返る等）のみを提示する。
  - 例外: SSOT目的で状態フラグ一覧・hook間連携仕様を集約する節
    （`<plugin root>/skills/agent-standards/SKILL.md`「セッション状態フラグ」節等）は本規定の対象外とする

スキル・サブエージェント編集時は次を守る。

- 名前付きサブエージェントの定義と起動文を編集する場合は、`agent-toolkit:agent-standards`の
  `agent-toolkit/skills/agent-standards/references/agent-skills.md`を適用する
- 呼び出し元の専用の参照文書を起動契約、agent定義を受信側の恒常手順としてペアで更新する
- 呼び出し元スキルと参照文書からagent定義をReadする手順を除外する
- 独立入口間の重複は、各入口の読込コンテキストを実測し、
  参照だけでは実行判断に必要な情報が欠ける場合に限って許容する
- 相互参照が発生する共通観点は横断スキル配下`references/`へ集約してよい
- 並行する手順を別スキルに新設する際は、既存スキルの表記との整合を確認する
- 「実行時エラーで判明する仕様」「具体例」は再発リスクと影響度を踏まえて保持判断する

### プラグイン内リソースの参照書式

エンドユーザー環境で実行される実行時パス（`hooks.json`の`command`・エージェント/スキル本文の実行コマンド例）は`${CLAUDE_PLUGIN_ROOT}/<相対パス>`形式に統一する。
プラグイン配布物のルートはインストール先で動的に解決されるため、dotfilesリポジトリ相対パスはエンドユーザー環境で実行不能となる。
規範文書内で役割を説明する言及（「〜は`agent-toolkit/scripts/<name>.py`が担う」等）はリポジトリ相対表記のままでよい。
判定基準は当該パスをエンドユーザー環境で実行するか否かとする。
Agent PluginsのMCP定義をCodexへ射影する場合は、`args`・`cwd`・`env`の各値に含まれる`${CLAUDE_PLUGIN_ROOT}`を`${PLUGIN_ROOT}`へ変換する。Claude Code側の実行時パスは前項の形式を維持する。

## スキル間の連携

`agent-toolkit:plan-mode`から作業を開始し、承認後はAgentツールで`agent-toolkit:plan-executor`を
起動して引き継ぐ。工程の詳細は各スキル・agent定義を正本とする。
計画ファイルの実装者向け領域にあるレビューステップへ
`レビューは実施しない（ユーザー指示）`とあればレビュー工程をスキップする。

## バージョン更新

本節のバージョン更新規定は`agent-toolkit/`配下（agent-toolkitプラグイン配布物）のみを対象とする。
詳細手順は`references/version-bump.md`に集約する。
全レーン後に版数を更新する時点では、`agent-toolkit/skills/process-feedbacks/references/finish-session.md`を全文読む。
`.claude/skills/agent-toolkit-edit/references/version-bump.md`の手順へ入る前に同ファイルを全文読む。
`agent-toolkit/`配下を変更対象に含む計画を作成する場合は、計画の起草前に同文書「plan modeでの取り扱い」節を読み、
実装資料の変更説明へ記載すべきファイル群を確定する。
rebase・merge時の版数競合は`references/version-bump.md`「競合解決と統合後の確認」節に従って解決する。
`version`／`description`は以下の箇所で完全に同一文字列に保つ。

- `agent-toolkit/.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`の`plugins[]`内`name == "agent-toolkit"`のエントリ
整合性は`agent-toolkit/scripts/pretooluse_test.py`の`TestManifestSsot`が検査し、`uvx pyfltr run`で自動的に失敗する。
Agent Plugins向け`plugin.json`・`mcp.json`とCodex向けmanifestは、この2ファイルと
`agent-toolkit/.mcp.json`を正本として`scripts/sync_codex_plugin_manifests.py`が生成する。
Agent Plugins・Codex向け生成物を手動編集してはならない。

## 同期先ドキュメント

複数ファイルへまたがる機構又は委譲構造を新設又は変更する実装では、`docs/development/design.md`へ目的、構造の理由、知識境界及び却下した代替案を追加又は更新する。

- コーディングエージェント向け文書を編集する場合は、`AGENTS.md`が定めるフィードバック由来文書の参照・更新規範に従う
- `docs/guide/claude-code-guide.md`「agent-toolkit」セクションのチェック内容要約は、要約が変わる変更時に更新する。
  対象は新しいcheck追加・既存check削除・検出範囲の大きな変更・依存ツールの変更・新規プラグイン追加を含む
- `install-claude.sh`の`FILES`・`install-claude.ps1`の`$files`・
  `agent-toolkit/rules/`配下のmdファイル一覧は完全一致を保つ
  （整合性は`install_script_ssot_test.py`が検査し、`scripts/gen-install-files.py`を含む`uv run python scripts/sync_generated_files.py`が一覧を自動同期する）
- 配布物スキル本体の外部インターフェース（判定区分・出力フォーマット・後始末コマンド分岐・サマリー表現など）へ
  新規追加・削除・改名を加える場合は連携整合を保つ。
  既知の呼び出し元スキル群を`grep -rn`で洗い出し、連携先の対応記述を同一計画内で同時更新する
- `agent-toolkit/rules/01-agent.md`と`02-agent-operations.md`の編集は`.chezmoi-source/dot_codex/AGENTS.md`の再生成差分を生じさせる。
  計画の実装資料へ編集する正本だけを変更説明として記載する。
  `uv run python scripts/sync_generated_files.py`と生成器出力との一致確認は実装者向け領域へ記載し、
  自動生成先は変更対象の説明へ重複して記載しない
- `99-claude-code.md`の編集はCodex向けAGENTS.mdの生成差分を生じさせないが、Claude配布一覧とバージョン更新の規定は適用する

## セッション状態フラグ

`agent-toolkit`プラグインが定義する全フラグ一覧のSSOTは`agent-toolkit:agent-standards`スキル本体（SKILL.md）
「セッション状態フラグ」節に置く。フラグを追加・変更する際は当該節を更新する。

SKILL.mdを`Read`で読むだけではPreToolUseフックの`agent_toolkit_edit_skill_invoked`フラグが立たず
警告が返るため、必ずSkillツールで起動する。

## 権限設定の配置

権限設定を変更する場合は、対象が全エンドユーザー向けかを先に判定する。
全エンドユーザー向けの内容は配布原本`share/claude_settings_json_managed*.json`へ置く
（`pytools/_internal/update_claude_settings.py`が`~/.claude/settings.json`へ反映する）。
特定ホスト・本リポジトリ限定の内容はリポジトリ直下の`.claude/settings.local.json`（バージョン管理対象外）へ置く。
読み取り専用コマンドには、引数なしの`Bash`許可を適用する。

プラグインの有効・無効は、永続的な設定値だけで再現できる場合に
`share/claude_settings_json_managed*.json`の`enabledPlugins`へ置く。設定反映前に
インストール済みプラグインへCLI遷移が必要な場合だけ
`pytools/_internal/install_claude_plugins.py`のauto一覧を使う。既存の歴史的重複は
新規登録の既定にしない。

## worktreeでの編集時の注意

作業用の複製（git worktree等）で配布物（`agent-toolkit/`配下等）を改訂しても、
実行中のhook・検査には当該セッションでは反映されない。稼働中の版は
`~/.claude/plugins/installed_plugins.json`の`installPath`で確認する。
hookに新規にブロックされた場合は、まず作業ツリーと稼働中の版との差を疑い、
当該hookが参照する配布先のファイルを`diff`等で比較してから対応する。

常駐するMCPサーバープロセス（`agent-toolkit/scripts/agents_server_mcp.py`等）は、起動時に読み込んだ
Pythonモジュールを保持し続けるため、`agent-toolkit/scripts/`配下の修正は当該プロセスの再起動まで反映されない。
修正の確定後も同じ事象を観測した場合は、修正が無効であると結論する前に当該プロセスが読み込んだ版を確定する。
起動時刻だけでは判別できない。別の作業ツリーや別のplugin rootから起動したプロセスは、
修正commitより後に起動していても当該修正を含まないファイルを読み込み得るためである。
次の順で確定する。

1. `ps -eo pid,cmd`で稼働プロセスのPIDと起動スクリプトの絶対パスを取得し、当該パスが対象の配布経路であることを確認する
2. `stat -c %Y /proc/<PID>`でプロセス起動時刻を、`stat -c %Y <当該パス配下の対象ファイル>`でファイル更新時刻を取得する
   （`ps -o lstart=`の出力は実行環境のロケールにより`date -d`が解釈できないため、時刻の比較には用いない）
3. ファイル更新時刻がプロセス起動時刻より後であれば、当該プロセスは修正前の版を保持している

過去に終了したプロセスについては同じ証拠を回収できない。
当時の起動スクリプトのパスと起動時刻を保持していない場合は、版差を原因として断定せず、
現行経路での再現可否を実測した範囲だけを結論とする。

## 編集手順

push前にbumpが必須（同じバージョンでは`claude plugin update`が「最新です」と返しエンドユーザーへ配信されないため）。

1. 「バージョン更新」の判定基準に該当する場合は`scripts/agent_toolkit_bump.py {patch|minor|major}`を実行する
2. `description`を変更する場合はSSOTの2ファイルを手で同期する
3. `scripts/sync_codex_plugin_manifests.py`を実行してAgent Plugins・Codex向け派生JSONを同期する
4. 必要なら`docs/guide/claude-code-guide.md`のチェック内容リストを更新する
5. MCP経由の`run_for_agent`へ`work_dir`として対象リポジトリルートの絶対パス、`paths`として
   `["."]`を渡し、SSOTテストを含む全テストが成功することを確認する。
   必要に応じて`commands`配列でSSOTテストなど特定ツールを指定する。
   MCPを利用できない場合は`uvx pyfltr run-for-agent`を使う
6. 変更をコミットする

次のいずれかを変更した場合は、変更後の互換起動条件を確認する。

- `agent-toolkit/agents/`配下のagent定義
- `agent-toolkit/skills/delegation/references/runtime-routing.md`「工程別モデル設定」のキー
- `agent-toolkit/share/codex-agents-base.md`のCodex互換起動条件

`agent-toolkit/agents/`配下の全定義を扱えることを人手で確認する。
3つの対応関係は機械判定できる入力を持たないため、自動検査では保証しない。

## フック実装の配置先（個人フックと配布物）

PreToolUseフックの配置先は複数ある。汎用機能はプラグインへ、dotfiles固有の前提に依存する機能は個人フックへ配置する。
類似チェックが既に片方に存在する場合はそちらへ統合する（SSOT原則）。判断に迷う場合はユーザーへ確認する。

- `scripts/claude_hook_pretooluse.py`（個人フック）: chezmoi経由で自分の`~/.claude/settings.json`にのみマージされる。
  dotfiles固有の運用前提（`~/.claude/`がchezmoi配布先、個人の命名規約など）に依存するチェック向け。
  配置した場合は`share/claude_settings_json_managed.posix.json`および同`win32.json`の
  `matcher`に新しいツール名を追加する必要があるか確認する
- `agent-toolkit/`（プラグイン）: `.claude-plugin/marketplace.json`経由で他者にも配布される。
  汎用的な制約・自動化（一般的な文字化け検出、PowerShell互換性チェックなど）向け。
  配置した場合は「バージョン更新」節の手順に従う
- Claude Codeのhookから起動するPEP 723スクリプトは`uv run --no-project --script`形式で呼び出す
  （対象は`agent-toolkit/hooks/hooks.json`と`share/claude_settings_json_managed.*.json`）
- `agent-toolkit/hooks/hooks.json`と`share/claude_settings_json_managed.*.json`が参照するスクリプトを改名・移動・削除する場合は、
  `agent-toolkit:agent-standards`の`agent-standards/references/claude-hooks.md`が定める互換入口の残置に従う。
  残置した互換入口はバージョン管理の対象へ含める。
  撤去は、新しい入口を含む版をbumpして配布した後の版数更新以降であり、かつ旧定義を読み込んだセッションが全て終了したことを
  確認できた場合だけ行う。確認できない場合は残置を維持する
- PEP 723スクリプト（`agent-toolkit/scripts/atk.py`等）の`dependencies`へパッケージを追加・更新する場合、
  リポジトリ本体の`pyproject.toml`にも同一制約で登録する（テスト実行が間接依存で偶然解決する状態を防ぐため）

agent-toolkit配下の編集時、dotfiles固有名の混入を`scripts/claude_hook_pretooluse.py`の専用チェックがブロックする。
個人プロジェクト名固定リストは当該スクリプト内で定義し、OSS公開プロジェクト名はwarning通知に留める。
スキル名・pytoolsコマンド名・scripts名は、`scripts/claude_hook_pretooluse.py`がhook実行時にディレクトリをスキャンして動的に取得する。
外部CLI参照は`_EXTERNAL_CLI_ALLOWED`登録識別子に限り`command -v`等の存在検査経由で許容する。

## 複数hook共存時の識別子

agent-toolkitのhookがエンドユーザー環境の他hookと同一イベントで共存する場合がある。
自身のhookメッセージを他hookから判別するため、`[auto-generated: agent-toolkit/<hook>]`形式のプレフィックスを行頭に置く。
プレフィックス・サフィックスの規約は`agent-toolkit/skills/agent-standards/references/claude-hooks.md`の
「コーディングエージェント宛てメッセージの標識」節に従う。

## marketplace管理

`update-dotfiles`（`chezmoi apply`後処理）はClaude Code向けagent-toolkitプラグインを自動インストール・更新する。
処理は`pytools/_internal/install_claude_plugins.py`が担う。
dotfiles固有スキルとplugin非対応のagents・rulesは、`post_apply`の専用ステップで原本へリンクする。
生成物の一括同期は`uv run python scripts/sync_generated_files.py`で起動する
（`python`の明示が必須。起動形の詳細は`docs/development/operations.md`を参照）。
marketplace配布経路は次のとおり。

- bootstrap: `install-claude.sh`/`install-claude.ps1`がGitHub型として登録する
- chezmoi apply: 後処理がdirectory型（絶対パス直接参照）で維持し、GitHub型登録残存時は自動でマイグレーションする
- ローカル編集の反映: `chezmoi apply`（または`update-dotfiles`）でデプロイし、
  Claude Code再起動か`/reload-plugins`で反映する（version bumpは不要）

Codex向け生成物は`.codex-plugin/plugin.json`と`.agents/plugins/marketplace.json`とする。
生成器と正本の関係は「バージョン更新」節に従う。
prek経由のpyfltr（書き込みモード）が`sync-generated-files`でCodex向け生成物を毎回再生成する。
Codex hookの定義は、`scripts/sync_codex_plugin_manifests.py`がイベント名、matcher、入力契約を確認した許可表の分だけを生成する。
`chezmoi apply`後処理はCodex marketplaceを登録し、agent-toolkit pluginを導入・更新する。

## コミットメッセージ方針と.gitmessage

`<plugin root>/skills/commit/SKILL.md`のコミットメッセージ方針と`.gitmessage`は配布範囲が異なるため意図的に重複させる。SSOT化しない。
