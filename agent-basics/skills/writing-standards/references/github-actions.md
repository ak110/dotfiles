# GitHub Actionsワークフロー記述スタイル

GitHub Actionsのワークフローを書く時とレビューする時の、外部actionの固定、権限、公開を伴うステップの順序などの決まりを示す。
原則9（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）の適用である。
公開を伴うステップの規定は原則4（`agent-basics:writing-standards/references/quality-principles-constraints.md`）の適用である。

## 基本

- ワークフローは`actionlint`と`yamllint`で検証し、その診断に従う。
- 外部actionは`uses: owner/repo@<commit-sha> # vX.Y.Z`の形で固定し、Renovateやpinactなどでメジャー版まで更新する。

## 権限と秘密情報

- `permissions:`はワークフローかジョブの単位で最小にする（基本は`contents: read`）。
- シークレットは`${{ secrets.NAME }}`で参照し、`run`の中へ直接書くことを避ける。
- 信頼できない外部のプルリクエストに`pull_request_target`を使わない（レビュー前のコードが任意のコマンドを実行できるため）。

## 並行制御と冪等性

- 同じ資源を操作するワークフローには`concurrency:`を設定し、リリースは`cancel-in-progress: false`、PR向けのCIは`cancel-in-progress: true`にする。
- リリースと公開は、既存のタグ、GitHub Release、公開済みのパッケージを確かめ、再実行で二重に作らない形にする。

## 破壊的ステップと事前検証

- 取り消せない変更や公開のステップ（タグのpush、PyPIへの公開、レジストリへのpush、GitHub Releaseの作成など）の前に事前検証を置き、失敗したら後続を止める。
- 破壊的なステップが複数ある時は、復旧の費用が高いものを後ろに置く（例: PyPIへの公開はDockerイメージのビルドより後）。
- リリース直後に自分のパッケージを解決できない時は、`agent-basics:writing-standards/references/dependency-management.md`「公開待機設定」の対処に従う。

## トリガーと出力

- ステップ間の値は`$GITHUB_OUTPUT`で渡す（非推奨の`::set-output`はactionlintの`deprecated-commands`が検出する）。
- `run`のシェルは未定義の変数と途中の失敗で止め、`pipefail`を有効にする。
  後段が入力を打ち切るパイプは`agent-basics:writing-standards/references/bash.md`「基本」に従う。
