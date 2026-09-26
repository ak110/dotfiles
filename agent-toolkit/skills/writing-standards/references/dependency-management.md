# 依存管理

サプライチェーン攻撃対策とセキュリティ維持の観点から運用する。
依存の追加・更新を専用CLI（`uv add`・`pnpm`・`mise`等）で行う原則は`implementation-time.md`が定める。

## バージョン指定と更新

- バージョン固定は管理コスト増大のため原則採用せず、公開直後の新バージョンを一定期間（目安1日）待つ設定で代替する
 （ツール例: uvの`exclude-newer`、pnpmの`minimum-release-age`）。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/dependency-management.md：バージョン指定と更新：2026年9月16日」にある
- 公開直後の版を一定期間解決対象から外すパッケージマネージャーの設定（uvの`exclude-newer`、pnpmの`minimum-release-age`、miseの`minimum_release_age`など）を「公開待機設定」と呼ぶ。
  公開待機設定が有効な環境では、自パッケージを含む直近版を解決できない。待機期間を満たさない版を下限として要求すると依存解決が失敗し、
  自リポジトリの直近リリースをビルドやCIで取り込む処理（イメージ内の`uv tool install`、リリース直後の自パッケージ参照など）も失敗する。
  対処は次のいずれかとする
  - パッケージ単位の除外（uvでは`exclude-newer-package = { 自パッケージ名 = false }`）
  - 対象工程（Dockerfileの対象RUNなど）だけ環境変数で公開待機設定を無効化する
  - ローカルで生成した成果物（wheel等）を直接渡す
  - 待機期間とパッケージインデックスの伝播（CDN反映の遅延など）の経過を待ってから解決を確認する。配布物の依存下限を公開直後の版へ引き上げる場合はこの対処を選ぶ
- 依存追加時はメンテナンス状況・代替の有無を確認する。使われていない依存は削除する
- 依存更新コマンドが対象パッケージを更新せずエラーも返さない場合、目的のバージョンを明示指定して
  再実行し、解決失敗の出力から制約元の依存パッケージを特定する
- 上書き設定（`[tool.uv] override-dependencies`など）は当該リポジトリの依存解決にのみ適用され
  配布物のメタデータには含まれないため、ユーザー環境へ波及する脆弱性は上書きで迂回せず上流の追従を待つ。
  開発時にのみ用いる依存に限り上書きで迂回してよい。いずれも判断の根拠と解除条件をコメントへ残す
- 依存追加・更新コマンドの出力警告を見逃さない。
  パッケージマネージャーの仕様変更・非推奨化が警告で告知されることがあり、放置すると設定が無効化される

## 脆弱性の確認

- SCAツールで定期的に脆弱性を確認する（`pip-audit`/`pnpm audit`/`cargo-audit`等）

## ロックファイルの運用

- ロックファイルを尊重する: CI/Makefileでは依存解決を再実行せずロックファイルをそのまま使う。
  環境変数で一括適用し（uvの`UV_FROZEN=1`、pnpmの`--frozen-lockfile`等）、
  依存更新コマンドのみ一時解除する（開発者個人のシェルには設定しない）
- 依存ロックファイルは自動生成ファイルとして扱い直接編集しない（詳細は`implementation-time.md`）

## pnpm

- `packageManager`フィールドはSHAハッシュ付きで保持する
  - `corepack use pnpm@<version>`でSHAハッシュ付きに更新する
- pnpmの最新版では`NPM_CONFIG_*`環境変数の読み取りが不安定（`pnpm config get`がenv varを無視するケースがある）。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/dependency-management.md：pnpm：2026年9月16日」にある
  - env var経由の設定反映テストには`npm config get`を使う
- `pnpm-workspace.yaml`の設定は`NPM_CONFIG_*`環境変数より優先される
