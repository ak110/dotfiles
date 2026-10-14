# GitHub Actionsワークフロー記述スタイル

本書はGitHub Actionsのワークフロー定義の記述スタイル基準を定める。

## 基本

- workflowはactionlintとyamllintで検証する
- 外部actionは`uses: owner/repo@<commit-sha> # vX.Y.Z`形式でcommit SHA pinし（tagはmutableなため）、Renovateやpinactなどで更新する

## 権限と秘密情報

- `permissions:`をワークフローまたはジョブ単位で最小権限にする（`contents: read`を基本とし、書き込みが必要なジョブだけ`contents: write`）
- secretは`${{ secrets.NAME }}`で参照し、値を`run`へ直接書かない
- 信頼できないPRで`pull_request_target`を使わない（レビュー前の任意コード実行を許すため）

## 並行制御と冪等性

- 同一リソースを操作するワークフローには`concurrency:`を設定する。リリース系は`cancel-in-progress: false`、PR CIなどは`cancel-in-progress: true`とする
- リリース・publish系は既存のタグ・リリース・パッケージを確認し、再実行で二重作成しない

## 破壊的ステップと事前検証

- 破壊的・公開系ステップ（タグ作成・push、PyPI publish、コンテナーレジストリpush、リリース作成など）の前に事前検証を置き、失敗時は進ませない
- 破壊的ステップが複数ある場合は、復旧コストの高いものを後ろに置く（やり直せないPyPI publishはDocker buildより後）
- リリース直後の自パッケージ参照が解決できない場合は、`dependency-management.md`「公開待機設定」の対処に従う

## トリガーと出力

- トリガー、キャッシュキー、jobの依存関係は検証の必要性と依存の変化に対応させる
- step間はGITHUB_OUTPUTで値を渡し、廃止されたset-outputは使わない
- `run`は未定義変数と途中失敗を検出し、pipefailを有効にする
