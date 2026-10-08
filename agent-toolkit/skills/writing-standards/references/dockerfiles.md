# Dockerfile記述スタイル

本書はDockerfileの記述スタイル基準を定める。

## 基本

- syntaxでBuildKitの安定フロントエンドを明示し、必要な機能と再現性を保つ
- ベースイメージは`image:tag@sha256:...`形式でdigest pinする
  RenovateやDependabotで自動更新できるよう`tag`も併記する
- マルチステージで基盤と成果物の責務・変更頻度を分ける
- 非rootユーザーで実行する。`useradd`でユーザー作成後に`USER`命令で切り替える

## レイヤー設計とキャッシュ

- レイヤーは変更頻度と処理の関連で分け、BuildKitのキャッシュマウントを再利用する
- APTキャッシュでは排他共有を使い、Debian系の`/etc/apt/apt.conf.d/docker-clean`による自動削除設定を事前に除去する

## サプライチェーン保護

- `apt-get install`は`--no-install-recommends`を付けて推奨パッケージを除外する
- `dependency-management.md`「公開待機設定」の規定をイメージ内のパッケージマネージャーでも有効にし、公開直後のバージョン導入を抑止する。待機期間の値は同節に従う
- 自リポジトリのパッケージをイメージビルド内で`uv tool install`等する場合は、`dependency-management.md`「公開待機設定」の対処に従う

## hadolint

- hadolintで静的解析し、抑制は必要な範囲に限定する

## 実行時設定

- `ENTRYPOINT`は配列形式（exec form）で書く。文字列形式（shell form）はシグナル伝達などで問題になる
- `HEALTHCHECK`はサーバー用途で設定する。CLIツール用途のイメージでは設定しない
- ユーザーが設定できる項目はENVで初期値を明示する
