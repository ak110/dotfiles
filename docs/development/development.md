# 開発

## 開発環境の構築手順

恒久作業ツリーの初期セットアップでは、mise 2026.8.8以降とuvを導入し、次を実行する。
uvは公式インストーラーを使用する。
uvはbootstrap自身の実行基盤であるため、ルートのmise設定では管理しない。

```bash
mise bootstrap
```

`mise bootstrap`はリポジトリ用ツールを導入し、`bootstrap`タスクでPython依存、prek hook、
コミットメッセージテンプレート、ローカルCLIを設定する。
Python依存の同期では、Makefileから継承する`UV_FROZEN=1`を解除して`uv sync --locked`を実行する。
`make setup`も同じタスクを呼ぶため、`uv.lock`が不整合な場合はどちらも失敗する。

適用内容だけを確認する場合は`mise bootstrap --dry-run`を使う。
宣言済み状態との差分は`mise bootstrap status`で確認する。

専用worktreeや回収予定の検証用複製の準備では、[dotfiles-development](../../.claude/skills/dotfiles-development/SKILL.md)の「開発手順」で、mise trustとローカル依存の同期を行う。
初期セットアップは共有CLI、Git hookとcommit templateも登録するため、一時作業場所の準備には使わない。
共有CLIの導入元は、回収後も存続する恒久作業ツリーに保つ。

PowerShellスクリプトのローカル完全検証は`pwsh`と`PSScriptAnalyzer`に依存する。
未導入でも`make test`は通過し、検証の抜けはCIで担保する。
ローカルで完全検証する場合のみ`make setup-pwsh`を実行する。

## 開発コマンド

| コマンド | 用途 |
| --- | --- |
| `make format` | 整形・軽量lint・自動修正 |
| `make test` | 全チェック実行（コミット可否判定） |
| `make update` | 依存更新（リポジトリ直下と`agent-toolkit/`の`uv.lock`を含む） |
| `make update-mise-locks` | リポジトリ用と配布用のmise lockfileを更新 |
| `make update-actions` | GitHub Actionsのハッシュピン更新（pinact経由） |

各コマンドの詳細は`Makefile`を参照する。

### CI環境のDocker再現検証

CI固有の失敗（依存ライブラリ不足等）をローカルで調査する場合、CIワークフローが使用する
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

- ロックファイル尊重: Python依存は`Makefile`とCIが常時有効化する`UV_FROZEN=1`により、`uv.lock`を再resolveせず使用する。mise管理ツールはルートと配布用設定に対応する2つの`mise.lock`を優先する
- 公開待機: Python依存は`pyproject.toml`の`exclude-newer`、mise管理ツールは`mise.toml`の`minimum_release_age`が定める期間を公開から経たものだけを採用する
- ピン留め運用: GitHub Actionsはコミットハッシュで固定し、pinactで更新を管理する
- 脆弱性検知: dotfilesは実行可能なコマンドラインツール群を配布するため、依存がエンドユーザーの実行環境へ
  波及する。Dependabot alertsを有効化し、自動修正PRの作成（Dependabot security updates）は
  無効化する方針を採用する。あわせて`.github/workflows/audit.yaml`が`uv audit`を定期実行し、
  検出結果をSARIFでCode Scanningへ送る。Dependabot alertsの未解決分は、`agent-toolkit:process-wi`が
  1回の実行ごとに行う自動コードレビュー監査（`atk review-audit pending`）で拾い、削除済みマニフェストに
  紐づく誤検知は却下し、実在する脆弱性は依存更新へ回す。`atk wi process-loop`の待機中確認は、
  未判定のアラートがあればprocess-wiの実行を起動するだけで、AWIを起票しない。Code Scanning由来のアラートは
  これらの処理の対象に含めない

設定値の詳細は`Makefile`・`.github/workflows/*.yaml`・`.pre-commit-config.yaml`（prekが読む
設定ファイルで、ファイル名自体は変更しない）を参照する。
エンドユーザー向けのグローバル設定一覧は[docs/guide/security.md](../guide/security.md)を参照する。

## MCPサーバーの起動失敗の診断

`agent-toolkit`が配布するMCPサーバー登録は、`uvx`へ版の範囲指定を渡す形を維持する。
版指定の形式はパッケージインデックスへの問い合わせ有無を変えない。
インデックスの応答はHTTPのキャッシュ制御に従って一定時間だけ再利用され、
その期間を過ぎると版の指定方法によらず再検証の通信が発生する。
インデックスへ再検証を求める通信に失敗すると`uvx`は標準出力へ何も書かないまま終了し、
MCPクライアントは初期化応答を得られないまま接続断となる。
完全一致の版指定へ変更してもこの通信挙動は変わらないため、版指定の変更を対処に用いない。
インデックスへの到達性が回復してから再試行すると、この失敗は解消する。
到達性が回復していない間は、再試行しても同じ原因で失敗する。

`uvx`が版指定を解決できない場合は、終了コードと出力で原因を判別する。
公開待機の設定により対象版が除外された場合は終了コード1となり、`No solution found`と
`exclude-newer`が対象を限定したことを示すhintを出力する。この場合は待機期間の経過後に同じ指定で解決できる。
インデックスへ到達できない場合は終了コード2となり、再試行回数と接続エラーを出力する。
この場合は前項のとおり到達性の回復を待つ。

## 依存の版指定を変えるときの検証

`pyproject.toml`の`dependencies`または`override-dependencies`でパッケージの版指定を変更した場合、
`uv lock`・`uv sync`・`uv run`の成功だけでは配布時の依存解決の成立を確認できない。
`override-dependencies`による上書きは本リポジトリの依存解決にのみ適用され、配布物のメタデータには含まれない。
上書き設定が適用されない状態で依存解決が成立することを
`uvx --exclude-newer "1 day" --from . dirsize --help`で実際に動かして確かめる。
この`uvx`コマンドはエンドユーザーの環境と同じ方法で配布物の依存を解決する。
そのため、上書き設定に依存した版指定を検出できる。
`uvx`は`pyproject.toml`の`[tool.uv]`を読まず`exclude-newer`が適用されない。
公開待機を維持するため`--exclude-newer`を明示する。
`dirsize`は`[project.scripts]`が登録するコマンドのうち標準ライブラリだけに依存し副作用を持たないため代表として用いる。
本リポジトリのコマンドはいずれも`--version`を受理しないため`--help`を用いる。
ただし、この方法で解決できるのは、配布物のメタデータが宣言する実行時依存に限る。
開発用の依存グループだけに適用される上書きは観測できない。
`make test`は`uvx`が解決する独立した環境で各種チェックツールを起動するため、
配布時の依存解決を経由しないため、上書き設定に依存した版指定の不整合も検出しない。
動作確認が失敗した場合は原因を確認する。
通信障害・パッケージ索引の障害・ビルド環境の不備など、版指定以外の原因を解消して再度動かして確かめる。
変更した版指定が原因で依存を解決できないと確認した場合は、配布物をインストールできなくなるため、
変更後の版指定は採用しない。

`override-dependencies`の追加・変更は開発環境の依存解決にも影響する。
上書きによって開発用の依存グループが要求する版を満たせなくなると、その依存を使う
開発用ツールが起動しなくなる。前段のどの手順もこの不成立を観測しない。
上書きを追加または変更した場合は、`uv lock`で変更をロックファイルへ反映したうえで、
上書き対象のパッケージへ依存する開発用ツールを`uv run --locked <ツール名> --help`のような
軽量な指定で起動し、終了コードが0であることを実際に確認する。
`--locked`は`uv.lock`が変更されないことを表明する指定であり、
ロックファイルへ未反映の変更が残っている場合にこの確認を失敗させる。
開発用ツールを起動できない状態のまま許容する場合は、影響範囲・許容する理由・解除条件を
`pyproject.toml`の該当する`override-dependencies`の設定に付けるコメントへ残す。

版指定を変更する場合は、変更後に`uv lock`でロックファイルへ反映する。
版指定の変更後と、特定パッケージの版を引き上げられるかを判断する場合は、
`uv tree --locked --universal --all-groups --invert --package <名前>`で逆依存を全件列挙する。
`--frozen`はロックファイルを更新せず、版指定の変更前に生成された依存関係を観測し得るため用いない。
列挙した逆依存は実行時依存と開発用依存へ分けて確認する。
実行時依存へ到達する場合、依存解決の成立だけでは上書きしたパッケージのAPIに関する機能上の非互換を
観測できない。そのAPIを読み込む呼び出し箇所を特定し、最小の実行または対応するテストで検証する。
各逆依存がPyPIの`requires_dist`で課す制約も個別に確認する。
依存解決は複数制約の連立であり、上限制約と他の逆依存が課す下限の共通部分が空である場合は成立しない。

`uv lock`は制約が緩んでも既存のロック内容を保持し、自動では版を上げない。
特定パッケージの引き上げは`--upgrade-package <名前>`、全体更新は`--upgrade`を指定する。
制約を変更したのに版が変わらない場合は、他の依存の制約が原因だと決めつける前に、再解決の範囲を確認する。

## リリース手順

`develop`から`master`へのリリースPRの作成、マージおよびマージ後の同期と検収は、`dotfiles-release`スキル（`.claude/skills/dotfiles-release/SKILL.md`）が定める。
リリース運用の方針と経緯は[concepts-workflows.md](concepts-workflows.md)の「developとmasterのリリース運用」にある。
