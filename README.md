# dotfiles

[chezmoi](https://www.chezmoi.io/) で管理された個人用dotfiles。

## 前提条件(要インストール)

- [Git](https://git-scm.com/install/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

### 例(Linux)

```bash
sudo apt install git
curl -fsSL https://astral.sh/uv/install.sh | sh

# オプション: mise
curl -fsSL https://mise.run | sh

# オプション: Claude Code
curl -fsSL https://claude.ai/install.sh | bash

# オプション: Codex CLI (別途Node.jsが必要)
npm install -g @openai/codex
```

### 例(Windows)

```cmd
winget install --id Git.Git -e --source winget
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

REM オプション: mise
winget install jdx.mise

REM オプション: Claude Code
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"

REM オプション: Codex CLI (別途Node.jsが必要)
npm install -g @openai/codex
```

## インストール

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install.sh | bash
```

### Windows (PowerShell, 推奨)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install.ps1 | iex"
```

#### 簡易版

```cmd
winget install twpayne.chezmoi && chezmoi init ak110 --source %USERPROFILE%\dotfiles --apply && setx PATH "%PATH%;%USERPROFILE%\bin;%USERPROFILE%\.local\bin"
```

## 更新

```bash
update-dotfiles
```

## ドキュメント

- [docs/chezmoi-usage.md](docs/chezmoi-usage.md) — chezmoiの使い方（ファイル追加・編集・命名規則）
- [docs/claude-code.md](docs/claude-code.md) — Claude Code設定管理（claudize）
- [docs/pytools.md](docs/pytools.md) — Pythonコマンドラインツール群
- [docs/ssh-config.md](docs/ssh-config.md) — SSH設定管理
- [docs/security.md](docs/security.md) — セキュリティ
- [docs/development.md](docs/development.md) — 開発
