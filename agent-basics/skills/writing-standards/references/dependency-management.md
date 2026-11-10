# 依存管理

本書では、パッケージやライブラリの依存管理と、コンテナーイメージ、言語処理系、CIや自動ビルドが取得するツール・actionの版の選び方を定める。
公開待機設定とロックファイルの運用を合わせ、最新版への追従とサプライチェーン保護を両立する。
依存の追加や更新を専用CLI（`uv add`、`pnpm`、`mise`など）で行う基本原則は`implementation-time.md`に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `dockerfiles.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/dockerfiles.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `github-actions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/github-actions.md` |

## 最新版への追従

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 外部部品の共通の版方針を判断へ用いる前 | `quality-principles-dependencies.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/quality-principles-dependencies.md` |

`quality-principles-dependencies.md`「外部部品の版の追従」を適用する。
版の上限、メジャー版だけを指すタグ、旧版の厳密指定などが最新の安定版の取り込みを妨げる場合は、一時的な措置の条件に従い、理由と解除条件を指定箇所のコメントへ残す。
組み合わせて動く処理系・パッケージマネージャーなどは、組の全部を同じ更新経路へ載せる。
digestやcommit SHAで固定する場合も、Renovate、Dependabot、pinactなどの更新ツールか定期的な更新手順で、メジャー版を含めて更新する。
更新経路を置けないリポジトリでは、提供元の最新安定版を指す浮動参照（`lts`、`stable`など）を使い、定期CIで失敗を検出して追随改修する。
提供元のサポートやセキュリティ修正が終わった版は更新する。

## バージョン指定と更新

- 新たに依存を採用する際は、将来にわたる保守の負担と代替手段の有無を確かめ、採用の必要性を検討し、不要となった依存関係は速やかに整理する。
- 依存更新コマンドを実行しても対象パッケージが更新されずエラーも出力されない場合は、更新先のバージョンを明示的に指定して再実行し、依存解決エラーの出力ログから更新を阻害している制約元を特定する。
- 依存の上書き設定（`[tool.uv] override-dependencies`など）は、そのリポジトリ内での依存解決にのみ有効であり、作成される配布物のメタデータには反映されない。
  エンドユーザー環境へ波及する脆弱性は上書き設定で回避せず上流パッケージの修正リリースを待つ。
  開発環境のみで使用する依存関係に限定して上書きを適用し、いずれの場合も設定理由と解除条件をコメントとしてコード内に残す。
- 依存の追加や更新時に出力される警告メッセージにはすべて目を通し、仕様変更や非推奨化への対応要否を判断する。

## 公開待機設定

- 最新版への追従を妨げる更新経路のない厳密な固定は採用せず、公開されたばかりの新しいバージョンを一定期間（目安として1日程度）待機してから取り込む設定を活用する。
  監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/dependency-management.md：公開待機設定：2026年9月16日」にある。
  本項の待機設定はパッケージマネージャーが解決する依存関係を対象とする。
  コンテナーのベースイメージやCIの外部actionの書式は`dockerfiles.md`と`github-actions.md`、版の選び方は「最新版への追従」に従う。
- パッケージが公開された直後のバージョンを一定期間だけ解決対象から除外する仕組み（uvの`exclude-newer`、pnpmの`minimum-release-age`、miseの`minimum_release_age`など）を「公開待機設定」と呼ぶ。
  この設定が有効な環境では、自身がリリースした直近のパッケージも含めて解決対象から除外されるため、待機期間に満たない最新バージョンを下限として要求すると、ビルドやCIで直近の自前リリースを取り込む処理（コンテナー内での`uv tool install`など）が失敗する。
  この事態には以下のいずれかの方法で対処する。
  - プロジェクト設定において対象パッケージを個別に除外する（uvでは`pyproject.toml`の`[tool.uv]`配下に`exclude-newer-package = { 自パッケージ名 = false }`と記述する）。
  - 対象の処理ステップ（Dockerfile内の特定の`RUN`命令など）に限り、環境変数を使って公開待機設定を一時的に無効化する。
  - ローカル環境でビルドした成果物（wheelファイルなど）を直接渡してインストールする。
  - 待機期間の経過とパッケージインデックスへの反映（CDNのキャッシュ更新など）を待ってから依存解決を再確認する。
    配布物自体の依存下限バージョンを公開直後のバージョンへ引き上げる場合は、この方法を選択する。
  - 配布物の作者自身が提供しているパッケージを、プロジェクト外部から実行するコマンド（`uvx`など）で要求する場合は、待機する代わりにコマンドの起動引数でそのパッケージのみを除外対象として指定する（uvでは`--exclude-newer-package <パッケージ名>=false`）。
    そのパッケージが依存している他の外部パッケージには、引き続き通常の公開待機設定が適用される。
- パッケージ単位の除外設定を記述する場所は、コマンドの実行形式に応じて判断する。
  プロジェクト全体の依存解決では`pyproject.toml`の`[tool.uv]`に記述し、`uvx`のようにプロジェクト設定を読み込まない単発コマンドではコマンドライン引数で指定する。
  ユーザーごとのグローバル設定（uvでは`~/.config/uv/uv.toml`など）に除外設定を記述することは避ける。
  uvはグローバル設定の除外内容を各プロジェクトの`uv.lock`内（`[options.exclude-newer-package]`）に記録してしまうため、意図せず無関係なプロジェクトのロックファイルまで変更され、同じグローバル設定を持たない環境で`uv lock --check`が失敗する原因となる。

## 脆弱性の確認

- ソフトウェア構成分析（SCA）ツールを用いて、依存パッケージの脆弱性を定期的に監査する。

## ロックファイルの運用

- ロックファイルによる固定は、依存更新の専用コマンドで最新版へ更新し続ける運用と組にする。
- CIやMakefileによるビルド処理では依存解決を再実行せず、既存のロックファイルをそのまま利用する。
  環境変数を用いて一括で固定モードを適用し（uvの`UV_FROZEN=1`、pnpmの`--frozen-lockfile`など）、依存更新する専用コマンドの実行時のみ一時的に固定を解除する（開発者個人のシェル環境変数としては恒常的に設定しない）。
- 依存ロックファイルは自動生成される成果物として扱い、手作業で直接編集することは行わない（`implementation-time.md`を参照）。

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 配布物の版指定の検証・Python対応版の追加に関する判断・作業の前 | `dependency-management-distribution.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/dependency-management-distribution.md` |
| pnpmに関する判断・作業の前 | `dependency-management-pnpm.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/dependency-management-pnpm.md` |
