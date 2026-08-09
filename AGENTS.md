# AGENTS.md: dotfiles

本リポジトリはchezmoi管理のdotfilesリポジトリであり、`.chezmoi-source/`配下を`~/.*`にデプロイする。
多数の小規模なコマンドラインツールや、Claude Code用の共有設定（ルール・プラグイン）も持つ。

## 開発手順

- `make update`: 依存更新 + prek autoupdate + pinactアクション更新 + 全テスト実行
  - `make update-actions`: GitHub Actionsのハッシュピン更新のみ（mise経由でpinact実行）
- コミット前の検証方法: `make test`
  - 特定ファイルに限定する場合はMCP経由の`run_for_agent`へ当該ファイルのパスを渡す。
    MCPを利用できない場合は`uvx pyfltr run <対象ファイルの絶対パス>`を使う。
    デバッガ・最小再現・環境切り分けでは直接実行してよい
  - 修正後の再実行時は、MCPでは`commands`へ`["mypy", "ruff-check"]`等を渡して限定する。
    CLIフォールバックでは`--commands=mypy,ruff-check`を使う（最終検証はCIに委ねる前提）

## 詳細の参照先

- リポジトリ全体の構成・配布対象と開発対象の区別・プラットフォーム対応・bash補完運用・
  PowerShellスクリプト注意事項・ホーム配下編集前の確認手順:
  [docs/development/architecture.md](docs/development/architecture.md)
- 運用機能の詳細（`sync_generated_files.py`の起動形・tmux自動アタッチ・TBD未回答表示・
  常駐サービス・Windows電源設定・post-applyキャッシュ・chezmoiの命名規則）:
  [docs/development/operations.md](docs/development/operations.md)

## 注意点

- `.claude`を含むディレクトリが3系統あり、配布元・デプロイ先・リポジトリ専用と役割が異なる
 （`.chezmoi-source/dot_claude/`/`~/.claude/`/`.claude/`）。
  指示の対象を必ず確認する（詳細は「固有差分」の「ディレクトリ構造の注意」を参照）
- 権限設定を変更する場合は、対象が全利用者向けかを先に判定する。
  全利用者向けの内容は配布原本`share/claude_settings_json_managed*.json`へ置く
  （`pytools/_internal/update_claude_settings.py`が`~/.claude/settings.json`へ反映する）。
  特定ホスト・本リポジトリ限定の内容はリポジトリ直下の`.claude/settings.local.json`（バージョン管理対象外）へ置く
  - 読み取り専用コマンドには、引数なしの`Bash`許可を適用する
  - auto modeの拒否への対応要否は`agent-toolkit/skills/agent-standards/references/auto-mode.md`
    「ルール区分」節（`soft_deny`・`hard_deny`の定義）で判定する
- `.chezmoi-source/dot_codex/`はCodex用の配布元で`~/.codex/`へデプロイされる
  - `.chezmoi-source/dot_codex/AGENTS.md`は`scripts/codex-agents-base.md`と
    `agent-toolkit/rules/`配下の共有規範から`scripts/sync_generated_files.py`が生成するため、手動編集しない
  - Claude Code固有の`99-claude-code.md`はCodex向けAGENTS.mdの生成対象から除外する
  - agent-toolkitのスキルはCodex plugin marketplaceで配布し、`post_apply`で導入・更新する。
    dotfiles固有スキルとplugin非対応のagents・rulesは、`post_apply`の専用ステップで原本へリンクする
  - Codex向けplugin・marketplace manifestもClaude Code向けmanifestから統合生成ランナーが生成する
- 生成物の一括同期は`uv run python scripts/sync_generated_files.py`で起動する
  （`python`の明示が必須。起動形の詳細は[operations.md](docs/development/operations.md)を参照）
- `.chezmoi-source/`配下のファイルを削除・改名した場合、chezmoiは配布先を自動削除しない
  - 配布先から除去するには`pytools/post_apply.py`の`_REMOVED_PATHS`に対象パスを追記する
  - 改名時は`_REMOVED_PATHS`の`~/.claude`欄（Codex側にもリンクがある対象は`~/.codex`欄も）へ
    旧パスを追記し、`setup_codex_links.py`の`_LINKS`マッピングを新名へ更新する
- プラットフォーム対応ファイル（Linux/Windowsのペア）を編集するときは`sync-platform-pair`スキルを呼び出して両側を同期する
- `bin/`配下の`*.cmd`はCP932（Shift_JIS）で書かれている。UTF-8前提のEdit/Writeツールでは
  文字化けや破損のリスクがあるため、ASCIIのみの修正は`sed -i`で対応する
- リポジトリ内リソースを参照するスクリプトは`Path.home()`起点ではなく`Path(__file__)`起点で解決する
 （CIチェックアウトや利用者環境で`$HOME`と`~/dotfiles`が一致しない場合にimportが破綻するため）
- 単純なコマンドラッパーの新規追加には`scripts/new-bin-cmd.py <name> <command...>`を使う
 （リポジトリ直下の`bin/<name>`と`bin/<name>.cmd`のペアを生成する）
- `agent-toolkit/`配下・`.claude-plugin/marketplace.json`の編集時はSkillツールで`agent-toolkit-edit`を呼び出す。
  呼び出し漏れは編集時にPreToolUseフックが警告を返す
  - marketplace管理・フック実装の配置先判断・version bump手順も同スキルへ集約する
  - `agent-toolkit/rules/`・`agent-toolkit/skills/`配下のMarkdown編集時は`agent-standards`・`writing-standards`を併用する
- `pytools/`トップレベルには`project.scripts`から参照される公開CLIモジュール
  （単一ファイル`<name>.py`またはサブパッケージ`<name>/`配下形態）を置き、bash補完（argcomplete）に対応する
  - サブパッケージは`__init__.py`が`_cli.py`の`main`を再エクスポートし、`project.scripts`はパッケージ名の`main`を参照する
- privateなヘルパー（chezmoi運用補助・共通ユーティリティなど）は`pytools/_internal/`配下に集約する
- エージェント・hook・自動化など手で起動しないスクリプトは`scripts/`配下へ置く
 （`[project.scripts]`登録は行わず、PEP 723形式の単独実行スクリプトとして書く）
- 高頻度起動するhook・statusLine相当のスクリプトは、Windowsでの`uv run`起動コストを考慮し、
  ネイティブバイナリ化を実装方式の第一候補として検討する（先行事例は`rust/claude-statusline/`）
- Claude Codeのhookから起動するPEP 723スクリプトは`uv run --no-project --script`形式で呼び出す
  （対象は`agent-toolkit/hooks/hooks.json`と`share/claude_settings_json_managed.*.json`）
- PEP 723スクリプト（`agent-toolkit/scripts/atk.py`等）の`dependencies`へパッケージを追加・更新する場合、
  リポジトリ本体の`pyproject.toml`にも同一制約で登録する（テスト実行が間接依存で偶然解決する状態を防ぐため）
- Pythonテストコードはソースモジュールと同一ディレクトリに`<name>_test.py`として配置する
 （`pytools/`・`scripts/`・`agent-toolkit/`配下いずれも同方式）
  - テスト共通ヘルパーは`pytools/`配下では`pytools/_internal/_test_helpers.py`へ集約する。
    `agent-toolkit/`配下のテストは配布物独立性を保つため`pytools/_internal/`配下を参照せず、
    共通化が必要な場合は`agent-toolkit/scripts/`配下に独自ヘルパーを置く
  - `pytools`パッケージ配布物にテストコードを含めないため、
    `[tool.hatch.build.targets.wheel]`の`exclude`で`*_test.py`と`_test_helpers.py`を除外する
  - `scripts/`配下はpytestのprependモードで`sys.path`へ自動追加されるためテストから直接importできる。
    importしたいスクリプトはアンダースコア区切りで命名し、shebang付きスクリプトは`chmod +x`する
- `pytools/_internal/claude_common.py`は共通基盤モジュール（`find_dotfiles_root()`・`run_subprocess()`・
  `atomic_write_*()`等）を提供する。新規ヘルパーを書き起こす前に公開APIを確認し、重複定義を避ける
- `process-feedbacks`スキル完了後はコミット作成に加えて`git push`まで実施する（dotfilesリポジトリ運用ルール）
  - フィードバック投入元（feedback-inbox）の整合性を保つため、ローカルに留めず即時公開する
- 作業用の複製（git worktree等）で配布物（`agent-toolkit/`配下等）を改訂しても、
  実行中のhook・検査には当該セッションでは反映されない。稼働中の版は
  `~/.claude/plugins/installed_plugins.json`の`installPath`で確認する。
  hookに新規にブロックされた場合は、まず作業ツリーと稼働中の版との差を疑い、
  当該hookが参照する配布先のファイルを`diff`等で比較してから対応する

## 固有差分

### ロールとファイル群の対応

本リポジトリと配布物には複数のロールが関与する。ファイル群を編集する際は対象読者を意識する。

- dotfiles利用者: chezmoiソース・`bin`・`pytools`等を自分の環境にインストールして使う人
- agent-toolkit利用者: `agent-toolkit`プラグインをマーケットプレイス経由で使う人（dotfiles利用者含む）
  - 配布ルール（`~/.claude/rules/agent-toolkit/`）も導入済み前提で記述してよい
- 全プロジェクト編集者: あらゆるプロジェクトで編集作業をするコーディングエージェント
  - 配布物（`agent-toolkit`本体・`~/.claude/rules/agent-toolkit/`配下）を実行時にロードする
- dotfiles編集者: 本リポジトリや`agent-toolkit`本体を修正するコーディングエージェント
  - 全プロジェクト編集者の対象に加え、リポジトリ直下の`.claude/`と`AGENTS.md`もロードする
   （Claude Codeは`CLAUDE.md`経由のfile importで読む）

各ファイル群の対象読者と役割。

| ファイル群 | 対象読者 | 役割 |
| --- | --- | --- |
| `agent-toolkit/skills/`配下 | 全プロジェクト編集者 | スキルの指示本体 |
| `agent-toolkit/agents/`配下 | 全プロジェクト編集者 | サブエージェントの指示本体 |
| `agent-toolkit/agents/`配下のfrontmatterコメント | dotfiles編集者 | 連携先や注意事項などの編集用メタ情報 |
| `.chezmoi-source/dot_claude/`配下 | 全プロジェクト編集者・dotfiles利用者 | 常時自動ロードされる行動原則（dotfiles利用者には配布先`~/.claude/`相当） |
| `.chezmoi-source/dot_codex/`配下 | 全プロジェクト編集者 | Codex向けのユーザー設定とClaude Code側原本へのリンク |
| `docs/guide/claude-code-guide.md` | agent-toolkit利用者 | プラグインの導入・更新手順 |
| `.claude/`（リポジトリ直下） | dotfiles編集者 | 本リポジトリ開発時のみ参照されるClaude Codeプロジェクト設定 |
| `AGENTS.md`（本ファイル） | dotfiles編集者 | 本リポジトリの修正方針・固有知見。`CLAUDE.md`は`@AGENTS.md`importの1行アダプター |
| `pytools/`・`bin/`・`scripts/` | dotfiles利用者・dotfiles編集者 | コマンドラインツールと開発スクリプト |

### コミットメッセージtypeの判定例

本リポジトリには配布物と本リポジトリ専用設定が混在するため、変更対象に応じてtypeを使い分ける。

- 配布物の振る舞いを変える変更は`feat`/`fix`/`perf`相当
  - `.chezmoi-source/`配下: dotfiles利用者の環境に展開されるため、利用者振る舞いを変える変更は機能変更
  - `agent-toolkit/`配下: プラグイン利用者のエージェントの振る舞いが変わる（スキル・サブエージェント・ルール・`references/`）
  - `pytools/`・`bin/`配下: dotfiles利用者向けCLIツールのため、挙動変更はそのまま機能変更
- 本リポジトリ専用設定の変更は`chore`相当（リポジトリ直下の`.claude/`・本ファイル`AGENTS.md`・アダプター`CLAUDE.md`）
- 配布物の利用者向け説明の変更は`docs`相当（`README.md`・`docs/guide/`配下など）
- 軽微な誤字修正・スタイル調整・コメント整形などは内容にかかわらず`chore`に倒してよい

### ディレクトリ構造の注意

Claude Code/Codex設定ディレクトリが複数あり、取り違えは影響範囲の異なる事故につながる。指示の対象を必ず確認する。

- `.chezmoi-source/dot_claude/`: 配布元。chezmoiが`~/.claude/`にデプロイする（グローバルユーザー設定の原本）
- `~/.claude/`: デプロイ先。`chezmoi apply`で上書きされるため直接編集してはならない
  - ユーザーが「`~/.claude`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_claude/`
- `.claude/`（本リポジトリルート）: dotfilesリポジトリ自身のClaude Codeプロジェクト設定。配布対象外
  - Codex側でも明示検出させたい場合は`.agents/skills`を`.claude/skills`へのシンボリックリンクにする
- `.chezmoi-source/dot_codex/`: Codex配布元。`~/.codex/`へデプロイする
  - `AGENTS.md`はCodex向けアダプター。共有ルール・スキルは`setup_codex_links.py`が
    `.chezmoi-source/dot_claude/`または`agent-toolkit/`の原本へリンクを生成する
    （Linux/macOSはシンボリックリンク、Windowsはディレクトリジャンクション。
    chezmoiの`symlink_`はWindowsで特権不足により失敗するため未使用）
  - `~/.codex/skills`にはグローバルに使うスキルだけを置く
- `.chezmoi-source/dot_config/`: XDG準拠ツール設定（`git`・`uv`・`pyfltr`等）の配布元
  - ユーザーが「`~/.config/<tool>`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_config/<tool>/`
- `AGENTS.md`（本リポジトリルート）: dotfiles編集者向けのSSOT。Claude Code／Codex双方がここを読む
  - `CLAUDE.md`は`@AGENTS.md`をimportする1行のみのアダプター
