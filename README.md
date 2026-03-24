# dotfiles

[chezmoi](https://www.chezmoi.io/) で管理されたdotfiles。

## 前提条件(要インストール)

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Git](https://git-scm.com/install/)

## インストール

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install.sh | bash
```

### Windows (cmd)

```cmd
winget install twpayne.chezmoi && chezmoi init ak110 --source %USERPROFILE%\dotfiles --apply && setx PATH "%PATH%;%USERPROFILE%\bin"
```

## 更新

```bash
update-dotfiles
```

## 使い方

ソースディレクトリは `~/dotfiles`。

### ファイルの追加

```bash
# 既存のファイルをchezmoi管理に追加（命名規則に従ってソースに追加される）
chezmoi add ~/.some-config
```

### ファイルの編集と反映

```bash
# chezmoi経由で編集（エディタが開く）
chezmoi edit ~/.bashrc

# または ~/dotfiles/dot_bashrc を直接編集してから反映
chezmoi apply
```

### 差分の確認

```bash
chezmoi diff
chezmoi apply --dry-run --verbose
```

### コミット＆プッシュ

```bash
cd ~/dotfiles
git add -A
git commit -m "update dotfiles"
git push
```

### 命名規則（早見表）

| ソースステート                 | ターゲット                   |
|--------------------------------|------------------------------|
| `dot_bashrc`                   | `~/.bashrc`                  |
| `dot_config/git/config`        | `~/.config/git/config`       |
| `private_dot_ssh/`             | `~/.ssh/` (mode 700)         |
| `private_authorized_keys`      | `authorized_keys` (mode 600) |
| `bin/executable_foo`           | `~/bin/foo` (実行権限付き)   |
| `run_onchange_after_*.sh.tmpl` | 変更時に実行されるスクリプト |

詳細: https://www.chezmoi.io/reference/source-state-attributes/

## SSH設定管理 (`update-ssh-config`)

SSH configとauthorized_keysを分割ファイルから生成するコマンド。詳細は [docs/ssh-config.md](docs/ssh-config.md) を参照。

```bash
update-ssh-config
```

## 開発

### 初回セットアップ

```bash
make setup
```

### チェックの手動実行

```bash
make check
```

## その他

### VSCode (`~/.vscode-server/data/Machine/settings.json`)

```json
{
    "python.linting.pylintArgs": [
        "--rcfile=~/dotfiles/share/vscode/pylintrc"
    ]
}
```

### ipython

```bash
uv pip install -r ~/dotfiles/requirements.txt
ipython --profile=ipy
```
