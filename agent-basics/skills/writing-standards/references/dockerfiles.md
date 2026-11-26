# Dockerfile記述スタイル

Dockerfileを書く担当がDockerfileに固有の決まりに従い、ユーザーが遅いビルドや再現しないイメージを受け取らないようにする。
Dockerfileを書く時とレビューする時の、BuildKitとAPTのキャッシュ、公開待機設定との組み合わせなどの決まりを示す。
原則9（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）の適用として、Dockerfileに固有の決まりだけを置く。

## 基本

- 先頭の`# syntax`ディレクティブで、BuildKitの安定版のフロントエンドを指定する。
- ベースイメージのtagは最新の安定版を指すものにする（hadolintの`DL3006`、`DL3007`）。
  `image:tag@sha256:...`の形でdigestを固定する時は依存更新ツールを使い、digestとtagの両方をメジャー版まで更新する。
  更新ツールが無ければ`agent-basics:writing-standards/references/dependency-management.md`「最新版への追従」の浮動参照に従う。
- コンテナーは非rootユーザーで実行する（hadolintの`DL3002`）。

## レイヤー設計とキャッシュ

- BuildKitのキャッシュマウント（`RUN --mount=type=cache,...`）でビルドを短くする。
- APTのキャッシュは`sharing=locked`で共有し、Debian系の`/etc/apt/apt.conf.d/docker-clean`（自動削除の設定）を先に消す。

## サプライチェーン保護

- `apt-get install`には`--no-install-recommends`を付ける（hadolintの`DL3015`）。
- コンテナー内のパッケージマネージャーでも公開待機設定を有効にする。
  自リポジトリのパッケージをビルドの中で入れる時は、`agent-basics:writing-standards/references/dependency-management.md`「公開待機設定」の対処に従う。

## hadolint

hadolintで検証してその診断に従い、警告の抑制（インラインのコメント、設定ファイル）は必要な最小の範囲に限る。

## 実行時設定

- `ENTRYPOINT`はシグナルを伝えるため配列形式で書く（hadolintの`DL3025`）。
- `HEALTHCHECK`はWebサーバーと常駐デーモンのコンテナーで設定し、単発で実行するCLIのイメージでは省く。
- 環境変数で変えられる設定は、`ENV`で初期値を定義する。
