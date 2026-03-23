# dotfiles

[chezmoi](https://www.chezmoi.io/) で管理されたdotfiles。

## インストール

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install.sh | bash
```

### Windows (cmd)

```cmd
winget install twpayne.chezmoi && chezmoi init ak110 --source %USERPROFILE%\dotfiles --apply && setx PATH "%PATH%;%USERPROFILE%\bin"
```

### Windows 移行（旧バージョンから）

旧 `deploy.bat` で作成されたディレクトリジャンクションを解除してから移行する。

```cmd
REM 1. 旧deploy.batが作成したジャンクションを削除
REM    （rmdir はジャンクションのみ削除し、中身は元の場所に残る）
rmdir "%XDG_CONFIG_HOME%" 2>nul
rmdir "%USERPROFILE%\.ipython" 2>nul
rmdir "%USERPROFILE%\.claude" 2>nul

REM 2. リポジトリを移動
move <既存のパス>\dotfiles %USERPROFILE%\dotfiles

REM 3. chezmoiインストール＆適用
winget install twpayne.chezmoi && chezmoi init --source %USERPROFILE%\dotfiles --apply && setx PATH "%PATH%;%USERPROFILE%\bin"
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
pip install -r ~/dotfiles/requirements.txt
ipython --profile=ipy
```
