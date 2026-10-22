# 依存管理

依存の採用・追加・更新、版指定、公開待機設定とロックファイルの運用を定め、サプライチェーン攻撃対策とセキュリティ維持へつなげる。
依存の追加・更新を専用CLI（`uv add`・`pnpm`・`mise`等）で行う原則は`implementation-time.md`が定める。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `dockerfiles.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/dockerfiles.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `github-actions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/github-actions.md` |

## バージョン指定と更新

- 採用時は維持可能性と代替を評価し、不要な依存は残さない
- 依存更新コマンドが対象を更新せずエラーも返さない場合は、目的の版を明示指定して再実行し、解決失敗の出力から制約元を特定する
- 上書き設定（`[tool.uv] override-dependencies`など）はそのリポジトリの依存解決にだけ適用され、配布物のメタデータの外にある。エンドユーザー環境へ波及する脆弱性は上書きで迂回せず上流の追従を待ち、開発時にだけ用いる依存に限り上書きしてよい。いずれも根拠と解除条件をコメントへ残す
- 追加・更新の警告を全て読み、仕様変更と非推奨化への対処を判断する

## 公開待機設定

- バージョン固定は管理コストが大きいため原則採用せず、公開直後の新バージョンを一定期間（目安1日）待つ設定で代替する。
  監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/dependency-management.md：公開待機設定：2026年9月16日」にある。
  本項はパッケージマネージャーが解決する依存を対象とし、コンテナーのベースイメージとCIの外部actionは`dockerfiles.md`と`github-actions.md`に従う
- 公開直後の版を一定期間解決対象から外す設定（uvの`exclude-newer`、pnpmの`minimum-release-age`、miseの`minimum_release_age`など）を「公開待機設定」と呼ぶ。
  有効な環境では自パッケージを含む直近版を解決できず、待機期間を満たさない版を下限に要求すると、ビルドやCIで自リポジトリの直近リリースを取り込む処理（イメージ内の`uv tool install`など）も失敗する。対処は次のいずれかとする
  - プロジェクトの設定でのパッケージ単位の除外（uvでは`pyproject.toml`の`exclude-newer-package = { 自パッケージ名 = false }`）
  - 対象工程（Dockerfileの対象RUNなど）だけ環境変数で公開待機設定を無効化する
  - ローカルで生成した成果物（wheel等）を直接渡す
  - 待機期間とパッケージインデックスの伝播（CDN反映の遅延など）を待ってから解決を確認する。配布物の依存下限を公開直後の版へ引き上げる場合はこの対処を選ぶ
  - 配布物の作者自身が公開するパッケージを、配布物がプロジェクトの外で起動するコマンド（`uvx`など）で要求する場合は、待機に代えてその起動の引数でそのパッケージだけを除外してよい（uvでは`--exclude-newer-package <パッケージ名>=false`）。そのパッケージの依存には公開待機設定が引き続き適用される
- パッケージ単位の除外の置き場所は起動の種類で決める。プロジェクトの依存解決では`pyproject.toml`の`[tool.uv]`、`uvx`のようにプロジェクトの設定を読まない起動ではコマンドラインとする。
  ユーザー単位のグローバル設定（uvでは`~/.config/uv/uv.toml`）は置き場所から外す。uvはグローバル設定の除外を各プロジェクトの`uv.lock`（`[options.exclude-newer-package]`）へ記録するため、無関係なプロジェクトのロックファイルまで変わり、同じ設定を持たない環境で`uv lock --check`が失敗する

## 配布物の版指定の検証

- 配布物がエンドユーザーの環境でパッケージマネージャーへ渡す版指定を変える場合は、統合前に、エンドユーザーと同じ解決方法と配布する公開待機設定の値で変更後の版指定を解決し、終了コードで成功を確かめる。
  uvでは、プロジェクトの外で`uvx --exclude-newer "<配布する待機期間>" --from '<変更後の版指定>' <コマンド> --version`のように実行する。
  `uvx`、`uv tool install`、`uv run --script`、`uv run --no-project --with`はプロジェクトの`[tool.uv]`とロックファイルを使わず、実行環境のユーザー設定で解決する。このため、プロジェクト内の`uv lock`・`uv sync`・`uv run`の成功、ロックファイルへの取り込み、インデックスへの公開、解決器を差し替えたテストの成功は根拠から外す。
  解決できない場合は「公開待機設定」のうち依存下限を引き上げる場合の対処を適用してから統合する

## Python対応版の追加

Pythonの対応版を追加する場合は、対象PythonとCIのOS・アーキテクチャで導入される依存を特定し、選ばれるwheelの互換性と、sdistへ進む依存のビルド要件を確かめる。ローカルのwheelキャッシュによる同期成功は、キャッシュの無いCIでのビルド成立の根拠にしない（観測した条件での成立として記録する）。
互換wheelを持つ版への更新と、CIでビルドを成立させる案を比べる。コンパイラー・ヘッダーの追加は対象依存の実際のビルド要件に限り、対象環境で導入されない依存はマーカーやextrasを根拠に判定対象から外す。
採用版、Python版、CIイメージとアーキテクチャ、配布形式、観測日と再検証手段を記録する。観測例は`docs/development/audit-records.md`「agent-basics/skills/writing-standards/references/dependency-management.md：Python対応版の追加：2026年10月10日」にある。

## 脆弱性の確認

- SCAで定期的に脆弱性を監査する

## ロックファイルの運用

- CI/Makefileでは依存解決を再実行せずロックファイルをそのまま使う。環境変数で一括適用し（uvの`UV_FROZEN=1`、pnpmの`--frozen-lockfile`等）、依存更新コマンドだけ一時解除する（開発者個人のシェルには設定しない）
- 依存ロックファイルは自動生成ファイルとして扱い直接編集しない（`implementation-time.md`）

## pnpm

- `packageManager`フィールドはSHAハッシュ付きで保持し、`corepack use pnpm@<version>`で更新する
- pnpmの最新版では`NPM_CONFIG_*`環境変数の読み取りが不安定（`pnpm config get`が環境変数を無視するケースがある）ため、環境変数経由の設定反映の確認には`npm config get`を使う。
  監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/dependency-management.md：pnpm：2026年9月16日」にある
- `pnpm-workspace.yaml`の設定は`NPM_CONFIG_*`環境変数より優先される
