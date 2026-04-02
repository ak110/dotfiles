# chezmoiの使い方

リポジトリは `~/dotfiles`、chezmoi のソースディレクトリは
`~/dotfiles/.chezmoi-source`（`.chezmoiroot` で指定）。

## ファイルの追加

```bash
# 既存のファイルをchezmoi管理に追加（命名規則に従ってソースに追加される）
chezmoi add ~/.some-config
```

## ファイルの編集と反映

```bash
# chezmoi経由で編集（エディタが開く）
chezmoi edit ~/.bashrc

# または ~/dotfiles/.chezmoi-source/dot_bashrc を直接編集してから反映
chezmoi apply
```

## 差分の確認

```bash
chezmoi diff
chezmoi apply --dry-run --verbose
```

## コミット＆プッシュ

```bash
cd ~/dotfiles
git add -A
git commit -m "update dotfiles"
git push
```

## 命名規則（早見表）

`.chezmoi-source/` 内のファイルは以下の命名規則に従ってデプロイされる。

- `dot_bashrc` → `~/.bashrc`
- `dot_config/git/config` → `~/.config/git/config`
- `private_dot_ssh/` → `~/.ssh/` (mode 700)
- `private_authorized_keys` → `authorized_keys` (mode 600)
- `bin/executable_foo` → `~/bin/foo` (実行権限付き)
- `run_onchange_after_*.sh.tmpl` → 変更時に実行されるスクリプト

詳細: <https://www.chezmoi.io/reference/source-state-attributes/>
