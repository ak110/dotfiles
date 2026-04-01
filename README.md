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

## 使い方

リポジトリは `~/dotfiles`、chezmoi のソースディレクトリは
`~/dotfiles/.chezmoi-source`（`.chezmoiroot` で指定）。

### ファイルの追加

```bash
# 既存のファイルをchezmoi管理に追加（命名規則に従ってソースに追加される）
chezmoi add ~/.some-config
```

### ファイルの編集と反映

```bash
# chezmoi経由で編集（エディタが開く）
chezmoi edit ~/.bashrc

# または ~/dotfiles/.chezmoi-source/dot_bashrc を直接編集してから反映
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

`.chezmoi-source/` 内のファイルは以下の命名規則に従ってデプロイされる。

- `dot_bashrc` → `~/.bashrc`
- `dot_config/git/config` → `~/.config/git/config`
- `private_dot_ssh/` → `~/.ssh/` (mode 700)
- `private_authorized_keys` → `authorized_keys` (mode 600)
- `bin/executable_foo` → `~/bin/foo` (実行権限付き)
- `run_onchange_after_*.sh.tmpl` → 変更時に実行されるスクリプト

詳細: <https://www.chezmoi.io/reference/source-state-attributes/>

## pytools (Pythonコマンドラインツール群)

`pytools/` ディレクトリに格納されたPythonパッケージ。`chezmoi apply` 時に `uv tool install` で自動インストールされる。

### コマンド一覧

- `claudize` — Claude Code設定を初期化・同期
- `py-imageconverter` — 画像変換（リサイズ、フォーマット変換、メタデータ削除）
- `py-rename` — 正規表現でファイルリネーム
- `py-rmdirs` — 正規表現でディレクトリ削除
- `py-pdf-to-image` — PDFを画像に変換（要Poppler）
- `check-image-sizes` — 画像サイズの分布を分析
- `git-justify` — Gitコミット日時を営業時間内に調整
- `mvdir` — ディレクトリをマージ
- `update-ssh-config` — SSH config/authorized_keysを生成

### 手動インストール

```bash
uv tool install --editable ~/dotfiles
```

## SSH設定管理 (`update-ssh-config`)

SSH configとauthorized_keysを分割ファイルから生成するコマンド。
詳細は [docs/ssh-config.md](docs/ssh-config.md) を参照。

```bash
update-ssh-config
```

## 開発

### 初回セットアップ

```bash
make setup
```

### チェックの実行

```bash
make test
```

### その他のコマンド

```bash
make fix      # ruff自動修正
make format   # フォーマットのみ
make update   # 依存アップグレード＋全チェック
```

## サプライチェーン保護

パッケージマネージャーに対するサプライチェーン攻撃を緩和するため、公開から一定期間が経っていないパッケージのインストールをブロックしている。
`chezmoi apply` / `update-dotfiles` 実行時に自動適用される。

| ツール                | 設定                             | スコープ                            |
|-----------------------|----------------------------------|-------------------------------------|
| uv (uvx含む)          | `exclude-newer = "1 day"`        | グローバル (`~/.config/uv/uv.toml`) |
| npm / pnpm (pnpx含む) | `minimum-release-age=1440` (1日) | グローバル (`~/.npmrc`)             |

一時的に無効化したい場合（急ぎで最新版が必要な場合など）は、以下を参照。

```bash
# uv
uv pip install --exclude-newer 0seconds <package>

# npm / pnpm
npm install --minimum-release-age=0 <package>
pnpm install --config.minimum-release-age=0 <package>
```

### GitHub Actions ピン留め

GitHub Actions のアクションは [pinact](https://github.com/suzuki-shunsuke/pinact) でコミットハッシュにピン留めしている。
`make update` 実行時に自動更新される（mise 未導入時はスキップ）。

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
