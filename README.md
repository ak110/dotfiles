# dotfiles

[chezmoi](https://www.chezmoi.io/) で管理されたdotfiles。

## 前提条件(要インストール)

- [Git](https://git-scm.com/install/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

### 例(Linux)

```bash
sudo apt install git
curl -LsSf https://astral.sh/uv/install.sh | sh

# オプション: nvm, node.js
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
nvm install --lts
```

### 例(Windows)

```cmd
winget install --id Git.Git -e --source winget
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## インストール

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install.sh | bash
```

### Windows (cmd)

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
