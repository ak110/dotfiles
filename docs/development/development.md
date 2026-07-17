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

### CI環境のDocker再現検証

CI固有の失敗（依存ライブラリ不足等）をローカルで切り分ける場合、CIワークフローが使用する
コンテナーイメージをホストのリポジトリをマウントして起動し、コンテナー内でパッケージインストール・
依存関係同期を試行する。

```bash
docker run --rm -it --user root -v "$PWD:/work" -w /work ghcr.io/ak110/pyfltr:latest bash
```

`--user root`かつボリュームマウント構成でコンテナー内から書き込みを行うと、
ホスト側ファイル所有者がrootへ変わる。`.venv`配下等が汚染されるとホスト側のPython実行が
`Permission denied`で失敗するため、検証後は所有権をホスト側ユーザーへ復旧する。

```bash
sudo chown -R "$(id -u):$(id -g)" .
uv sync --reinstall  # .venvを再構築する場合
```

## サプライチェーン攻撃対策

ロックファイル尊重・公開待機・ピン留め運用の3点を貫徹する。

- ロックファイル尊重: `uv.lock`を再resolveせず使用する（`UV_FROZEN=1`を環境変数で常時適用）
- 公開待機: `exclude-newer`および`mise`の`minimum_release_age`で公開から一定の期間を経たパッケージのみ採用する
- ピン留め運用: GitHub Actionsはコミットハッシュで固定し、pinactで更新を管理する

設定値の詳細は`Makefile`・`.github/workflows/*.yaml`・`.pre-commit-config.yaml`を参照する。
利用者向けのグローバル設定一覧は[docs/guide/security.md](../guide/security.md)を参照する。
