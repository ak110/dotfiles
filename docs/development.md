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

## UV_FROZENによるlockfile尊重（サプライチェーン攻撃対策）

CI/`make`などの自動実行環境で`uv sync`/`uv run`が依存解決を再実行せず`uv.lock`をそのまま使うよう、環境変数`UV_FROZEN=1`を有効化している。
意図しない再resolveでロックファイルが書き換わるリスクを抑え、グローバル設定の`exclude-newer`（[docs/security.md](security.md)参照）と組み合わせて二重防御として機能する。

- `make format`/`make test`/`make setup`は`Makefile`の`export UV_FROZEN := 1`で自動適用される
- CIは`.github/workflows/test.yml`の該当ステップ・ジョブの`env.UV_FROZEN`で自動適用される
- `git commit`経由のpre-commitフックは`.pre-commit-config.yaml`のlocal hookのentryに`--frozen`を明示している

開発者のシェルでは`UV_FROZEN`を設定しない前提なので、依存の追加・更新は通常どおり`uv add`/`uv remove`/`uv lock --upgrade-package`を使えばよい。
`make update`も内部で自動的にUV_FROZENを外すため、そのまま実行してよい。

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
