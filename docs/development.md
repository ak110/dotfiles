# 開発

## 初回セットアップ

```bash
make setup
```

### 任意: PowerShell スクリプトの検証環境

pre-commitフックの `powershell-analyzer` および `chezmoi template check` の `.ps1.tmpl` 検証は `pwsh` (PowerShell 7) に依存する。
`pwsh` 上では `PSScriptAnalyzer` モジュールも併せて必要になる。

未導入でもフックは警告を出してスキップするため `make test` は通過する。ローカルで完全検証したい場合は下記の手順で導入する。検証漏れはCIのtest-linuxジョブで担保する。

Ubuntu/Debianの場合は以下のコマンドで一括導入できる。

```bash
make setup-pwsh
```

macOS:

```bash
brew install --cask powershell
pwsh -NoProfile -Command "Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force -SkipPublisherCheck"
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
