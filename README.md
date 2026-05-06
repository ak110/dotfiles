# dotfiles

[![CI][ci-badge]][ci-url]

[ci-badge]: https://github.com/ak110/dotfiles/actions/workflows/ci.yaml/badge.svg
[ci-url]: https://github.com/ak110/dotfiles/actions/workflows/ci.yaml

[chezmoi](https://www.chezmoi.io/)で管理する個人用dotfiles。

## 特徴

- chezmoi管理によるホーム配下（`~/.*`）の一括デプロイ
- LinuxとWindowsの両対応
- Python製コマンドラインツール群（`pytools`）の同梱
- Claude Code/Codex用の共通ルール・スキル・プラグインの配布
- サプライチェーン攻撃対策設定のグローバル適用（uv/npmの公開待機、`pinact`によるGitHub Actionsのコミットハッシュ固定）

## 前提条件

- [Git](https://git-scm.com/install/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

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

Claude Code・Codex CLI・miseなどの周辺ツールは任意。

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

## 使い方

```bash
update-dotfiles
```

## ドキュメント

- [docs/index.md](docs/index.md) — ドキュメント入口
- [docs/guide/index.md](docs/guide/index.md) — 利用者向け（Claude Code/Codex設定・pytools・SSH・セキュリティ）
- [docs/development/development.md](docs/development/development.md) — 開発者向け
