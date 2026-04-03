# 開発

## 初回セットアップ

```bash
make setup
```

## チェックの実行

```bash
make test
```

## その他のコマンド

```bash
make format   # 整形 + 軽量lint + 自動修正（開発時の手動実行用）
make update   # 依存アップグレード＋全チェック
```

## VSCode (`~/.vscode-server/data/Machine/settings.json`)

```json
{
    "python.linting.pylintArgs": [
        "--rcfile=~/dotfiles/share/vscode/pylintrc"
    ]
}
```

## ipython

```bash
uv pip install -r ~/dotfiles/requirements.txt
ipython --profile=ipy
```
