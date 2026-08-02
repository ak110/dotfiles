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
- 公開待機: Python依存は`exclude-newer`で公開から1日、mise管理ツールは`minimum_release_age`で公開から7日を経たものだけを採用する
- ピン留め運用: GitHub Actionsはコミットハッシュで固定し、pinactで更新を管理する
- 脆弱性検知: dotfilesは実行可能なコマンドラインツール群を配布するため、依存が利用者の実行環境へ
  波及する。Dependabot alertsを有効化し、自動修正PRの作成（Dependabot security updates）は
  無効化する方針を採用する。あわせて`.github/workflows/audit.yaml`が`uv audit`を定期実行し、
  検出結果をSARIFでCode Scanningへ送る。Dependabot alertsの未解決分は
  `atk mq process-loop`がfeedbackとして自動投入する（Code Scanning由来のアラートは
  当該自動投入の対象に含まない）。
  実測でDependabot alertsは有効である（`gh api repos/ak110/dotfiles/vulnerability-alerts`が204）。
  自動修正PRの作成は無効である（`gh api repos/ak110/dotfiles/automated-security-fixes`が
  `enabled: false`）。いずれも方針どおりの状態にある

`pyproject.toml`の`dependencies`または`override-dependencies`でパッケージの版指定を変更した場合、
`uv lock`・`uv sync`・`uv run`の成功だけでは配布経路の成立を確認できない。
`override-dependencies`による上書きは本リポジトリの依存解決にのみ適用され、配布物のメタデータには含まれない。
上書き設定が適用されない状態で依存解決が成立することを
`uvx --exclude-newer "1 day" --from . dirsize --help`で実測する。
当該コマンドは利用者環境と同じ経路で配布物の依存を解決するため、
上書き設定に依存した版指定を検出できる。
`uvx`は`pyproject.toml`の`[tool.uv]`を読まず`exclude-newer`が適用されない。
公開待機を維持するため`--exclude-newer`を明示する。
`dirsize`は`[project.scripts]`が登録するコマンドのうち標準ライブラリだけに依存し副作用を持たないため代表として用いる。
本リポジトリのコマンドはいずれも`--version`を受理しないため`--help`を用いる。
`dotfiles-post-apply`・`update-ssh-config`は引数を解析せず実処理を開始するため代表に選ばない。
当該コマンドが解決するのは配布物のメタデータが宣言する実行時依存に限る。
開発用の依存グループだけに適用される上書きは観測できない。
`make test`は`uvx`が解決する独立した環境で検査ツールを起動するため、
配布経路の依存解決を経由せず当該不整合も検出しない。
実測が失敗した場合は原因を確認する。
通信障害・パッケージ索引の障害・ビルド環境の不備など、版指定以外の原因を解消して再実測する。
変更した版指定に起因する依存解決不能を確認した場合は、配布物のインストールを不能にするため
当該版指定を採用しない。

`override-dependencies`の追加・変更は開発環境の依存解決にも影響する。
上書きにより開発用の依存グループが要求する版が満たされなくなると、当該の依存へ依存する
開発用ツールが起動しなくなる。前段のどの手順もこの不成立を観測しない。
上書きを追加または変更した場合は、`uv lock`で変更をロックファイルへ反映したうえで、
上書き対象のパッケージへ依存する開発用ツールを`uv run --locked <ツール名> --help`のような
軽量な指定で起動し、終了コードが0であることを実測する。
`--locked`は`uv.lock`が変更されないことを表明する指定であり、
ロックファイルへ未反映の変更が残っている場合に実測を失敗させる。
起動しない状態を許容する場合は、影響範囲・許容する理由・解除条件を
`pyproject.toml`の当該`override-dependencies`のコメントへ残す。

設定値の詳細は`Makefile`・`.github/workflows/*.yaml`・`.pre-commit-config.yaml`（prekが読む
設定ファイルで、ファイル名自体は変更しない）を参照する。
利用者向けのグローバル設定一覧は[docs/guide/security.md](../guide/security.md)を参照する。
