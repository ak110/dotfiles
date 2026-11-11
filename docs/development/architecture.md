# アーキテクチャ

本リポジトリはchezmoi管理のdotfilesリポジトリ。
主要なコンポーネントとその役割を以下に示す。

- `.chezmoiroot`でソースステート（`.chezmoi-source/`）とプロジェクトインフラを分離する
- `.chezmoi-source/`内がchezmoiのソースディレクトリ（`dot_`プレフィックス→`~/.*`にデプロイ）
- `.chezmoi-source/dot_claude/`: Claude Code用のユーザー設定。
  `~/.claude/`へデプロイする
- `.chezmoi-source/dot_codex/`: Codex用のユーザー設定。
  `~/.codex/`へデプロイする
- `.chezmoi-source/dot_gemini/`: Antigravity CLI用のユーザー設定（`GEMINI.md`と`antigravity-cli/skills/`）。
  `~/.gemini/`へデプロイする
- `agent-toolkit/`: Agent Plugins、Claude Code、Codexが共有するプラグインルートであり、OpenCode向けスキルと規範の原本（「agent-toolkitのホスト別配布」を参照）
- `agent-toolkit-codex/`: `agent-toolkit/`から生成するCodex向けプラグインルート。
  Gitで追跡せず、`update-dotfiles`のpost-applyが生成する
- `plugins/`: dotfilesユーザーが置く拡張ディレクトリ（Git追跡対象外）。
  直下の各プラグインの`.chezmoi-source/`と`post_apply.py`を本体反映後に名前順で適用する
- `bin/`: ユーザーのPATHへ追加して使うコマンド（「対象環境とスクリプトの配置」を参照）
- `completions/`: 事前生成したbash補完スクリプト。
  「bash補完（`completions/`）」を参照
- `docs/`: エンドユーザー向けガイド（`docs/guide/`）と開発者向け文書（`docs/development/`）
- `.claude/`: 本リポジトリの開発で使うClaude Codeのプロジェクト設定とプロジェクトスキル（配布対象外）
- `.claude-plugin/`: agent-toolkitを配布するマーケットプレイスの定義（`marketplace.json`）
- `.github/`: GitHub ActionsのワークフローとCIで使う共有アクション
- `pytools/`: Pythonコマンドラインツール群（`uv tool install`でインストール）
- `rust/`: Rust製コマンドラインツール群（CIでビルドしGitHub Releaseへ配布）
- `scripts/`: 開発とCIで使うスクリプトの置き場（prek・Makefile・pyfltr・CIから呼ぶ。エンドユーザー環境では実行しない）
- `libexec/`: エンドユーザー環境（LinuxとWindows）で他のプログラムから起動される実行ファイルの置き場（`bin/`のランチャー、systemd unit、Claude Codeのhook定義などが起動する）
- `share/`: エンドユーザー環境の処理が読むデータの置き場（Claude Code・Codexの管理対象設定、ユーザー環境変数など）
- テンプレートからリポジトリルートのファイルを参照する場合は`{{ .chezmoi.workingTree }}`を使用
  - 例: `{{ include (joinPath .chezmoi.workingTree "pyproject.toml") }}`

- [対象環境と補完](architecture-platforms.md)
- [ホスト別配布とOpenCode](architecture-distribution.md)
- [post-applyとPython依存環境](architecture-post-apply.md)
- [agents_serverとCodexへの配布](architecture-agents-codex.md)
- [ホーム設定の編集](architecture-home.md)
- [Antigravityへの配布](architecture-antigravity.md)
