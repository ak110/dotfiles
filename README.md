# dotfiles

[![CI][ci-badge]][ci-url]

[ci-badge]: https://github.com/ak110/dotfiles/actions/workflows/ci.yaml/badge.svg
[ci-url]: https://github.com/ak110/dotfiles/actions/workflows/ci.yaml

[chezmoi](https://www.chezmoi.io/)で管理する個人用dotfilesである。

## 特徴

- chezmoi管理によるホーム配下（`~/.*`）の一括デプロイ
- LinuxとWindowsの両対応
- Python製コマンドラインツール群（`pytools`）の同梱
- Agent Plugins、Claude Code、Codexで同じルートを共有するagent-toolkitの配布
- サプライチェーン攻撃対策設定のグローバル適用（uv/npmの公開待機、`pinact`によるGitHub Actionsのコミットハッシュ固定）

agent-toolkitは、Agent Plugins、Claude Code、Codexで共有できるコーディングエージェント向けツールキットである。
単体導入にはClaude Code CLI、Codex CLI、uvを使用する。
dotfiles配布では、`chezmoi apply`後の処理がこれらの導入と更新を担う。
概要と導入は[agent-toolkitのREADME](agent-toolkit/README.md)と[agent-basicsのREADME](agent-basics/README.md)、
詳細な設定・操作は[agent-toolkit利用ガイド](docs/guide/claude-code-guide.md)、
Codex固有の詳細は[Codex利用ガイド](docs/guide/codex-guide.md)を参照する。

## 前提条件

- [Git](https://git-scm.com/install/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Node.jsとnpm](https://nodejs.org/ja/download)（fullモードでは後処理でも導入する）
- [PowerShell 7](https://learn.microsoft.com/ja-jp/powershell/scripting/install/installing-powershell-on-windows)（WindowsでのCodex CLI導入に推奨）

Windows PowerShellでは、環境によって公式インストーラーが使用する`Get-FileHash`を解決できず、Codex CLIの導入に失敗する。

agent-toolkitのGit呼び出しはGit 2.34.1以上に対応する。
Git 2.34.1が受理しない引数（`git worktree list -z`など）を使わない。
worktree登録の列挙は共通実装`agent-toolkit/agent_toolkit/_git/worktrees.py`がGitの保存形式から読む。
CIの`pytest (3.15)`はUbuntu公式パッケージのGit 2.34.1を使い、通常のテストを実行する。
ローカルでは環境変数`AGENT_TOOLKIT_TEST_GIT`へGitの実行ファイルの絶対パスを指定して同じテストを実行できる。

### 例（Linux）

```bash
sudo apt install git
curl -fsSL https://astral.sh/uv/install.sh | sh
```

### 例（Windows）

```cmd
winget install --id=Git.Git -e --source=winget
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## インストール

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install.sh | bash
```

### Windows（PowerShell）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install.ps1 | iex"
```

#### 簡易版

```cmd
winget install twpayne.chezmoi && chezmoi init ak110 --source=%USERPROFILE%\dotfiles --apply && setx PATH "%PATH%;%USERPROFILE%\bin;%USERPROFILE%\.local\bin"
```

## 設定（任意）

任意の設定は[dotfilesの任意設定](docs/guide/dotfiles-settings.md)、miseの導入失敗時は[導入の補足](docs/guide/dotfiles-installation.md)を参照。

## 導入モード

既定の`full`はAIエージェントと開発環境を導入する。
開発しないサーバーには`minimal`を指定できる。
初回導入では、取得した`install.sh`を`bash install.sh --mode minimal`で、Windowsでは`install.ps1 -Mode minimal`で実行する。

| 対象 | full | minimal |
| --- | --- | --- |
| Git・uv・chezmoi・mise、fzf・gh・glab・glow・jq・ripgrep、tmuxプラグイン、pytools | 導入 | 導入 |
| Claude Code・Codex・Antigravity・OpenCode・Gemini CLI・Herdr、プラグイン・MCP・statusline | 導入 | 導入を省く |
| dotnet・node・rust・gcloud・aws-cdk、開発用linter・formatter | 導入 | 導入を省く |

モードはagent-toolkitの設定ファイルの`dotfiles_mode`へ保存され、次回以降の更新にも適用される。
設定だけを変更する場合は、`atk config set dotfiles_mode minimal`を実行する。
`atk config get dotfiles_mode`で現在値を確認できる。
この2コマンドはAI用のPython依存環境が未準備でも使える。

```bash
update-dotfiles --mode minimal
update-dotfiles --prune
update-dotfiles --mode full
```

minimalでは通常の`update-dotfiles`で、対象外の導入済みツールと整理可能なキャッシュを削除する。
fullで同じ整理を明示的に行う場合は`--prune`を指定する。
minimalでは共有ランタイム領域とAIエージェントの実体も対象となる。
Claude CodeとCodexの設定・会話履歴は保持する。
使用中のエージェントや旧Python環境を検出した場合は保持し、終了後の`update-dotfiles --prune`の再実行を案内する。
fullの通常更新では共有Python依存環境を保持し、どちらのモードでも使用中の旧環境は削除しない。

## 使い方

```bash
update-dotfiles
```

更新が失敗したときや、直近の更新で何が実行されたかを確認したいときは、直近1回の実行の保存ログを表示する。

```bash
update-dotfiles logs
```

容量の確認は[開発環境の容量レポート](docs/guide/dotfiles-capacity.md)を参照。

## ドキュメント

- [docs/index.md](docs/index.md): ドキュメントの総合案内
- [docs/guide/index.md](docs/guide/index.md): 利用者向け（Claude Code/Codex設定・SSH・セキュリティ）
- [docs/development/index.md](docs/development/index.md): 開発者向け
