# Dockerfile記述スタイル

本書はDockerfileの記述スタイル基準を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `dependency-management.md#「公開待機設定」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/dependency-management.md#「公開待機設定」` |

## 基本

- syntaxでBuildKitの安定フロントエンドを明示する
- ベースイメージは`image:tag@sha256:...`形式でdigest pinし、RenovateやDependabotで自動更新できるよう`tag`も併記する
- マルチステージで基盤と成果物の責務・変更頻度を分ける
- 非rootユーザーで実行する

## レイヤー設計とキャッシュ

- レイヤーは変更頻度と処理の関連で分け、BuildKitのキャッシュマウントを再利用する
- APTキャッシュでは排他共有を使い、Debian系の`/etc/apt/apt.conf.d/docker-clean`による自動削除設定を事前に除去する

## サプライチェーン保護

- `apt-get install`は`--no-install-recommends`を付ける
- `dependency-management.md`「公開待機設定」をイメージ内のパッケージマネージャーでも有効にし、自リポジトリのパッケージをイメージ内で`uv tool install`等する場合は同節の対処に従う

## hadolint

- hadolintで静的解析し、抑制は必要な範囲に限定する

## 実行時設定

- `ENTRYPOINT`は配列形式（exec form）で書く
- `HEALTHCHECK`はサーバー用途で設定し、CLIツール用途のイメージでは設定しない
- ユーザーが設定できる項目はENVで初期値を明示する
