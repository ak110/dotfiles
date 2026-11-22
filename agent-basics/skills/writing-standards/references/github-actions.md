# GitHub Actionsワークフロー記述スタイル

GitHub Actionsのワークフローを書く時とレビューする時の、外部actionの固定、権限、公開を伴うステップの順序などの決まりを示す。
原則9（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）の適用である。
公開を伴うステップの規定は原則4（`agent-basics:writing-standards/references/quality-principles-constraints.md`）の適用である。

## 基本

- ワークフローは`actionlint`と`yamllint`で検証し、その診断に従う。
- 外部actionは`uses: owner/repo@<commit-sha> # vX.Y.Z`の形で固定し、Renovateやpinactなどでメジャー版まで更新する。

## CIと公開の設計

変更の検証、統合、全体CI、配布の順に、守る対象（安定版、ユーザー、配布物など）と通過に必要な品質を決める。
現状維持、重複の削除、検証の集約、既存結果の再利用を比べる。
再利用できるのは、対象内容、環境、検証範囲が一致し、必須のジョブがその内容に対して実行され成功した場合に限る。

dotfilesのように各レーンから`develop`に集めて全体CIを実行し、`master`へのPRを配布の境界にする方式は、変更群を検証しながら安定版の更新を別に制御できる。
pyfltrのように検証済みの変更群を`master`へpushし、全体CIを通した後で必要な時にリリースする方式は、同じ安定版の隔離が不要な場合にブランチと統合操作が少なく済む。
名前やイベントを変えても、実行する検証と直列の待ちが同じなら時短にはならない。

比較には、最初の変更と全ての変更が公開されるまでの時間、runnerの稼働量、人がする操作、移行・保守の費用を使う。
実測と見込みを分け、削る仕事と維持する品質を対応付ける。

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
