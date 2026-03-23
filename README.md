# dotfiles

[chezmoi](https://www.chezmoi.io/) managed dotfiles.

## Installation

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install.sh | bash
```

### Windows (PowerShell)

```powershell
winget install twpayne.chezmoi
chezmoi init ak110 --source ~/dotfiles --apply
# PATHに ~/bin を追加（初回のみ）
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$HOME\bin", "User")
```

## Update

```bash
update-dotfiles
```

## Usage (chezmoi)

本リポジトリは [chezmoi](https://www.chezmoi.io/) で管理されている。
ソースディレクトリは `~/dotfiles`。

### ファイルの追加

```bash
# 既存のファイルをchezmoi管理に追加
chezmoi add ~/.some-config

# ファイルが ~/dotfiles/ 内にchezmoiの命名規則で追加される
# 例: ~/.some-config → ~/dotfiles/dot_some-config
```

### ファイルの編集

```bash
# chezmoi経由で編集（エディタが開く）
chezmoi edit ~/.bashrc

# または ~/dotfiles/dot_bashrc を直接編集してから反映
chezmoi apply
```

### 変更の確認

```bash
# ソースとターゲットの差分を確認
chezmoi diff

# 何が変わるか確認してから反映
chezmoi apply --dry-run --verbose
chezmoi apply
```

### 変更のコミット＆プッシュ

```bash
cd ~/dotfiles
git add -A
git commit -m "update dotfiles"
git push
```

### chezmoi命名規則（早見表）

| ソースステート | ターゲット |
|---|---|
| `dot_bashrc` | `~/.bashrc` |
| `dot_config/git/config` | `~/.config/git/config` |
| `private_dot_ssh/` | `~/.ssh/` (mode 700) |
| `private_authorized_keys` | `authorized_keys` (mode 600) |
| `bin/executable_foo` | `~/bin/foo` (実行権限付き) |
| `run_onchange_after_*.sh.tmpl` | 変更時に実行されるスクリプト |

詳細: https://www.chezmoi.io/reference/source-state-attributes/

## VSCode例 (`~/.vscode-server/data/Machine/settings.json`)

```json
{
    "python.linting.pylintArgs": [
        "--rcfile=~/dotfiles/share/vscode/pylintrc"
    ]
}
```

## ipython

```bash
pip install -r ~/dotfiles/requirements.txt
ipython --profile=ipy
```
