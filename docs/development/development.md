# 開発

## 開発環境の構築手順

mise 2026.7.12以降を導入し、次を実行する。

```bash
mise bootstrap
```

`mise bootstrap`はリポジトリ用ツールを導入し、`bootstrap`taskでPython依存、prek hook、
コミットメッセージテンプレート、ローカルCLIを設定する。
Python依存の同期では、Makefileから継承する`UV_FROZEN=1`を解除して`uv sync --locked`を実行する。
`make setup`も同じtaskを呼ぶため、`uv.lock`が不整合な場合はどちらも失敗する。

適用内容だけを確認する場合は`mise bootstrap --dry-run`を使う。
宣言済み状態との差分は`mise bootstrap status`で確認する。

PowerShellスクリプトのローカル完全検証は`pwsh`と`PSScriptAnalyzer`に依存する。
未導入でも`make test`は通過し、検証漏れはCIで担保する。
ローカルで完全検証する場合のみ`make setup-pwsh`を実行する。

## 開発コマンド

| コマンド | 用途 |
| --- | --- |
| `make format` | 整形・軽量lint・自動修正 |
| `make test` | 全チェック実行（コミット可否判定） |
| `make update` | 依存更新 |
| `make update-mise-locks` | リポジトリ用と配布用のmise lockfileを更新 |
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

ロックファイル尊重・公開待機・ピン留め運用・脆弱性検知の4点を貫徹する。

- ロックファイル尊重: Python依存は`uv.lock`を再resolveせず使用する。mise管理ツールはルートと配布用設定に対応する2つの`mise.lock`を優先する
- 公開待機: `exclude-newer`およびmiseの`minimum_release_age`で公開から7日を経たツールのみ採用する
- ピン留め運用: GitHub Actionsはコミットハッシュで固定し、pinactで更新を管理する
- 脆弱性検知: dotfilesは実行可能なコマンドラインツール群を配布するため、依存が利用者の実行環境へ
  波及する。Dependabot alertsを有効化し、自動修正PRの作成（Dependabot security updates）は
  無効化する方針を採用する。定期監査ワークフローで未解決アラートを`atk mq process-loop`経由で
  feedback投入する構成とする。
  実測でDependabot alertsは有効である（`gh api repos/ak110/dotfiles/vulnerability-alerts`が204）。
  自動修正PRの作成は無効である（`gh api repos/ak110/dotfiles/automated-security-fixes`が
  `enabled: false`）。いずれも方針どおりの状態にある

設定値の詳細は`Makefile`・`.github/workflows/*.yaml`・`.pre-commit-config.yaml`（prekが読む
設定ファイルで、ファイル名自体は変更しない）を参照する。
利用者向けのグローバル設定一覧は[docs/guide/security.md](../guide/security.md)を参照する。
