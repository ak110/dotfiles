# 開発

## 開発環境の構築手順

```bash
make setup
```

### 任意: PowerShellスクリプトのローカル完全検証環境

`.ps1.tmpl`検証は`pwsh`（PowerShell 7）と`PSScriptAnalyzer`モジュールに依存する。
未導入でも`make test`は通過する（フックが警告を表示してスキップ）。検証漏れはCIが担保する。
ローカルで完全検証する場合のみ導入。

```bash
make setup-pwsh  # Ubuntu/Debian
```

## 開発コマンド

```bash
make format   # 整形 + 軽量lint + 自動修正（開発時の手動実行用）
make update   # 依存アップグレード＋全チェック（pinactによるアクション更新含む）
make test     # 全チェック
```

## サプライチェーン攻撃対策

パッケージマネージャーに対するサプライチェーン攻撃を緩和するため、
公開から一定期間が経っていないパッケージのインストールをブロックしている。
グローバル設定はchezmoiで配布され、`chezmoi apply`/`update-dotfiles`実行時に自動適用される。
利用者向けの一覧は[docs/guide/security.md](../guide/security.md)を参照。

CI/`make`などの自動実行環境で`uv sync`/`uv run`が依存解決を再実行せず`uv.lock`をそのまま使うよう、
環境変数`UV_FROZEN=1`を常時有効化している。
意図しない再resolveでロックファイルが書き換わるリスクを抑え、
グローバル設定の`exclude-newer`と組み合わせて二重防御として機能する。

- `make format`/`make test`/`make setup`は`Makefile`の`export UV_FROZEN := 1`で自動適用される
- CIは`.github/workflows/*.yaml`の`env.UV_FROZEN`で自動適用される
- `git commit`経由のpre-commitフックは`.pre-commit-config.yaml`のlocal hookのentryに`--frozen`を明示している

開発者のシェルでは`UV_FROZEN`を設定しない前提なので、
依存の追加・更新は通常どおり`uv add`/`uv remove`/`uv lock --upgrade-package`を使用。
`make update`も内部で自動的にUV_FROZENを外すため、そのまま実行できる。

GitHub Actionsのアクションはコミットハッシュにピン留めして実行する
（[pinact](https://github.com/suzuki-shunsuke/pinact)で管理）。
ローカル更新は`make update-actions`（mise経由で`pinact run --update --min-age=1`を実行）で行い、
CIは`pinact run --check`（バージョン固定）で検証する。
pinactのCIバージョンを更新する場合は全プロジェクトのワークフローを一括更新。
