# dotfiles

[![CI](https://github.com/ak110/dotfiles/actions/workflows/ci.yaml/badge.svg)](https://github.com/ak110/dotfiles/actions/workflows/ci.yaml)

[chezmoi](https://www.chezmoi.io/) で管理された個人用dotfiles。

## 特徴

- chezmoi管理によるホーム配下(`~/.*`)の一括デプロイ
- LinuxとWindowsの両対応
- Python製コマンドラインツール群(`pytools`)の同梱
- Claude Code用の共通ルール・プラグインの配布
- サプライチェーン攻撃対策設定のグローバル適用（uv/npmの公開待機、`pinact`によるGitHub Actionsのコミットハッシュ固定）

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

- [docs/index.md](docs/index.md) — ドキュメント入口
- [docs/guide/index.md](docs/guide/index.md) — 利用者向け（chezmoi使い方・Claude Code設定・pytools・SSH・セキュリティ）
- [docs/development/development.md](docs/development/development.md) — 開発者向け

## 参考

```bash
# Claude Codeのバージョン固定化
\rm ~/.local/bin/claude
npm install -g @anthropic-ai/claude-code@2.1.98
# 戻す場合
npm uninstall -g @anthropic-ai/claude-code
curl -fsSL https://claude.ai/install.sh | bash
```
