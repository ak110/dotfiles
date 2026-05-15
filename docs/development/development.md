# 開発

## 開発環境の構築手順

```bash
make setup
```

PowerShellスクリプトのローカル完全検証は`pwsh`と`PSScriptAnalyzer`に依存する。
未導入でも`make test`は通過し、検証漏れはCIで担保する。
ローカルで完全検証する場合のみ`make setup-pwsh`を実行する。

## 開発コマンド

| コマンド | 用途 |
| --- | --- |
| `make format` | 整形・軽量lint・自動修正 |
| `make test` | 全チェック実行（コミット可否判定） |
| `make update` | 依存更新 |
| `make update-actions` | GitHub Actionsのハッシュピン更新（pinact経由） |

各コマンドの詳細は`Makefile`を参照する。

## サプライチェーン攻撃対策

ロックファイル尊重・公開待機・ピン留め運用の3点を貫徹する。

- ロックファイル尊重: `uv.lock`を再resolveせず使用する（`UV_FROZEN=1`を環境変数で常時適用）
- 公開待機: `exclude-newer`で公開から一定の期間を経たパッケージのみ採用する
- ピン留め運用: GitHub Actionsはコミットハッシュで固定し、pinactで更新を管理する

設定値の詳細は`Makefile`・`.github/workflows/*.yaml`・`.pre-commit-config.yaml`を参照する。
利用者向けのグローバル設定一覧は[docs/guide/security.md](../guide/security.md)を参照する。
