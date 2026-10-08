# GitHub Actionsワークフロー記述スタイル

本書はGitHub Actionsのワークフロー定義の記述スタイル基準を定める。

## 基本

- workflowは標準の配置に置き、actionlintとyamllintで検証する
- 外部actionは`uses: owner/repo@<commit-sha> # vX.Y.Z`形式でcommit SHA pinする
 （tagはmutableなため改ざんリスクがある）。Renovate/pinact等で自動更新する

## 権限と秘密情報

- ワークフロー全体または個別ジョブで`permissions:`を最小権限に設定する
  tokenの権限を明示する（例: `contents: read`が基本、書き込みが必要なジョブのみ`contents: write`）
- secretは`${{ secrets.NAME }}`で参照する。stepの`run`にベタ書きしない
- 信頼できないPRからの`pull_request_target`は厳禁。レビュー前の任意コード実行を許してしまう

## 並行制御と冪等性

- 同一リソースを操作するワークフローには`concurrency:`グループを設定する
  - リリース系は`cancel-in-progress: false`で完走を待つ
  - PR CIなどは`cancel-in-progress: true`で古い実行を打ち切る
- リリース・publish系は再実行時の冪等性を意識する
  既存タグ・既存リリース・既存パッケージの存在を確認し、二重作成を防ぐ

## 破壊的ステップと事前検証

- 破壊的・公開系ステップ（タグ作成・push、PyPI publish、コンテナーレジストリpush、リリース作成等）の前に
  事前検証ステップを置く。検証失敗時は破壊的ステップに進ませない
- 破壊的ステップが複数ある場合、より復旧コストの高いものを後ろに置く
  例: PyPI publishは事実上やり直し不可のため、復旧可能なDocker buildより後にする
- リリース直後の自パッケージ参照が解決できない場合は、`dependency-management.md`「公開待機設定」の対処に従う

## トリガーと最適化

- トリガーは検証が必要なイベントと変更範囲へ対応させる
- キャッシュは依存関係の変化を識別して再利用する
- jobは依存関係を明確にし、独立した処理を並列化する

## 出力とstep間連携

- step間はGITHUB_OUTPUTの公開契約で値を渡す。廃止されたset-outputは使わない
- runは未定義変数と途中失敗を検出し、pipefailを有効にする
