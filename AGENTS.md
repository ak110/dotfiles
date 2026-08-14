# AGENTS.md: dotfiles

本リポジトリはchezmoi管理のdotfilesリポジトリであり、`.chezmoi-source/`配下を`~/.*`にデプロイする。
多数の小規模なコマンドラインツールや、Claude Code用の共有設定（ルール・プラグイン）も持つ。

## 開発手順

- `make update`: 依存更新 + prek autoupdate + pinactアクション更新 + 全テスト実行
  - `make update-actions`: GitHub Actionsのハッシュピン更新のみ（mise経由でpinact実行）
- コミット前の検証方法: `make test`
  - 特定ファイルに限定する場合はMCP経由の`run_for_agent`へ当該ファイルのパスを渡す。
    MCPを利用できない場合は`uvx pyfltr run <対象ファイルの絶対パス>`を使う。
    デバッガ・最小再現・環境切り分けでは直接実行してよい。
    `-o addopts=''`で既定オプションを解除する場合は、`-p no:cacheprovider`を併記する
  - 修正後の再実行時は、MCPでは`commands`へ`["mypy", "ruff-check"]`等を渡して限定する。
    CLIフォールバックでは`--commands=mypy,ruff-check`を使う（最終検証はCIに委ねる前提）
- 新規Linux環境では、実ブラウザーテストに必要なChromiumとシステム依存を`make setup-browser`で一度導入する。
  OSパッケージの導入には権限が必要となる場合がある
- `atk serve`又は`claude-plans-viewer`のブラウザーUI、ブラウザーから到達するサーバー処理、静的資産、
  実ブラウザーテストを変更した場合は`make test-browser`を実行する
- コミットメッセージtypeの判定例: [docs/development/commit-types.md](docs/development/commit-types.md)

## 詳細の参照先

- リポジトリ全体の構成・配布対象と開発対象の区別・プラットフォーム対応・bash補完運用・
  PowerShellスクリプト注意事項・ホーム配下編集前の確認手順:
  [docs/development/architecture.md](docs/development/architecture.md)
- 運用機能の詳細（`sync_generated_files.py`の起動形・tmux自動アタッチ・TBD未回答表示・
  常駐サービス・Windows電源設定・post-applyキャッシュ・chezmoiの命名規則）:
  [docs/development/operations.md](docs/development/operations.md)
- 過去のフィードバックから確定した方針・意向: [docs/development/concepts.md](docs/development/concepts.md)
- 再発防止の判断材料となる事故・欠陥: [docs/development/incidents.md](docs/development/incidents.md)

## 編集時に起動するスキル

- `agent-toolkit/`配下・`.claude-plugin/marketplace.json`の編集時はSkillツールで`agent-toolkit-edit`を呼び出す。
  呼び出し漏れは編集時にPreToolUseフックが警告を返す。
  権限設定の配置・marketplace管理・フック実装の配置先判断・version bump手順・worktree編集時の注意も同スキルへ集約する
  - `agent-toolkit/rules/`・`agent-toolkit/skills/`配下のMarkdown編集時は`agent-standards`・`writing-standards`を併用する
- `pytools/`・`scripts/`・`bin/`・`rust/`配下の編集時は`pytools-edit`を呼び出す
  （配置規約・テスト配置・PEP 723・wheel設定・cmdエンコーディングを集約する）
- プラットフォーム対応ファイル（Linux/Windowsのペア）を編集するときは`sync-platform-pair`を呼び出して両側を同期する
- 本リポジトリでコーディングエージェント向け文書の記述指針やフック実装の配置を判断するときは、
  `agent-toolkit:agent-standards`（`references/agent-skills.md`・`references/claude-hooks.md`を含む）と
  `agent-toolkit-edit`を正本とする
  - 公式マーケットプレイスの`plugin-dev`各スキルと`skill-creator`は、frontmatterの項目名と受理値、
    ディレクトリ構造の要件、フックイベントの入出力契約などの上流仕様を確認するために参照する
  - 記述スタイル・構成・記述量の指針は自作規範を優先する
- 本リポジトリでは`claude-code-setup:claude-automation-recommender`が推奨する自動化手段の選定を適用対象外とし、
  `agent-toolkit:agent-standards`の振り分け規定と`agent-toolkit-edit`の「フック実装の配置先」に従う
- コーディングエージェント向け文書を編集する前に、`docs/development/concepts.md`と
  `docs/development/incidents.md`を読み、確定済みの方針・事故対策との整合を確認する。
  編集中に新たな事故又は確定した意向が生じた場合は、対応する文書を更新する

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

### ディレクトリ構造の注意

Claude Code/Codex設定ディレクトリが複数あり、取り違えは影響範囲の異なる事故につながる。指示の対象を必ず確認する。

- `.chezmoi-source/dot_claude/`: 配布元。chezmoiが`~/.claude/`にデプロイする（グローバルユーザー設定の原本）
- `~/.claude/`: デプロイ先。`chezmoi apply`で上書きされるため直接編集してはならない
  - ユーザーが「`~/.claude`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_claude/`
- `.claude/`（本リポジトリルート）: dotfilesリポジトリ自身のClaude Codeプロジェクト設定。配布対象外
  - Codex側でも明示検出させたい場合は`.agents/skills`を`.claude/skills`へのシンボリックリンクにする
- `.chezmoi-source/dot_codex/`: Codex配布元。`~/.codex/`へデプロイする
  - `AGENTS.md`はCodex向けアダプター。`scripts/codex-agents-base.md`と`agent-toolkit/rules/`配下の共有規範から
    `scripts/sync_generated_files.py`が生成するため、手動編集しない（生成差分で上書きされ、手動編集は消失する）
  - 共有ルール・スキルは`setup_codex_links.py`が
    `.chezmoi-source/dot_claude/`または`agent-toolkit/`の原本へリンクを生成する
    （Linux/macOSはシンボリックリンク、Windowsはディレクトリジャンクション。
    chezmoiの`symlink_`はWindowsで特権不足により失敗するため未使用）
  - `~/.codex/skills`にはグローバルに使うスキルだけを置く
- `.chezmoi-source/dot_config/`: XDG準拠ツール設定（`git`・`uv`・`pyfltr`等）の配布元
  - ユーザーが「`~/.config/<tool>`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_config/<tool>/`
- `.chezmoi-source/`配下のファイルを削除・改名した場合、chezmoiは配布先を自動削除しない。
  配布先から除去するには`pytools/post_apply.py`の`_REMOVED_PATHS`に対象パスを追記する。
  改名時は`_REMOVED_PATHS`の`~/.claude`欄（Codex側にもリンクがある対象は`~/.codex`欄も）へ
  旧パスを追記し、`setup_codex_links.py`の`_LINKS`マッピングを新名へ更新する
- `AGENTS.md`（本リポジトリルート）: dotfiles編集者向けのSSOT。Claude Code／Codex双方がここを読む
  - `CLAUDE.md`は`@AGENTS.md`をimportする1行のみのアダプター
