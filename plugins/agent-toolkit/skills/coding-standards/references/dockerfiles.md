# Dockerfile記述スタイル

## 基本

- 冒頭に`# syntax=docker/dockerfile:1`を必ず記述する。
  BuildKitの最新安定フロントエンドが使われ、`heredoc`・`--mount`等の機能が確実に有効になる
- ベースイメージは`image:tag@sha256:...`形式でdigest pinする。
  RenovateやDependabotで自動更新できるよう`tag`も併記する
- マルチステージビルドで「変更頻度の低い基盤」と「変更頻度の高い成果物導入」を分離し、
  キャッシュの再利用効率を上げる
- 非rootユーザーで実行する。`useradd`でユーザー作成後に`USER`命令で切り替える

## レイヤー設計とキャッシュ

- 関連する処理は1 RUNにまとめてレイヤー数を抑える。ただし変更頻度が異なる処理は分割する
- BuildKitのキャッシュマウントを活用する
  - APT: `--mount=type=cache,target=/var/cache/apt,sharing=locked`
   （Debian系では`/etc/apt/apt.conf.d/docker-clean`の自動削除設定を事前に除去する）
  - npm/pnpm/uv等のパッケージキャッシュも同様にマウントする

## サプライチェーン保護

- `apt-get install`は`--no-install-recommends`を付けて推奨パッケージを除外する
- パッケージマネージャーの`exclude-newer`系設定で公開直後のバージョン導入を抑止する
  - uv: `~/.config/uv/uv.toml`に`exclude-newer = "1 day"`
  - pnpm: `pnpm config set minimum-release-age 1440 --global`（分単位）
- 自リポジトリのパッケージをイメージビルド内で`uv tool install`等する場合、
  `exclude-newer`設定により直近リリース版が解決できず失敗する。
  uvでは`exclude-newer-package = { 自パッケージ名 = false }`で例外指定する
 （または環境変数で対象RUNだけ無効化する）

## hadolint

- `hadolint`でlintする。プロジェクト全体で抑止したいルールはDockerfile冒頭に
  `# hadolint global ignore=DL3007`のように記載する
- 個別行の抑止は直前行に`# hadolint ignore=DL3008`を置く

## 実行時設定

- `ENTRYPOINT`は配列形式（exec form）で書く。文字列形式（shell form）はシグナル伝達などで問題になる
- `HEALTHCHECK`はサーバー用途で設定する。CLIツール用途のイメージでは設定しない
- 環境変数で利用者が上書き可能にする項目（キャッシュディレクトリ、各種閾値等）は
  Dockerfile冒頭の`ENV`で既定値を明示する
