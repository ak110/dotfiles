# GitHub Actionsワークフロー記述スタイル

## 基本

- ワークフローファイルは`.github/workflows/*.yaml`（または`.yml`）に配置する
- `actionlint`でlintする。`yamllint`も併用してフォーマット系の指摘を捕捉する
- 外部actionは`uses: owner/repo@<commit-sha> # vX.Y.Z`形式でcommit SHA pinする
 （tagはmutableなため改ざんリスクがある）。Renovate/pinact等で自動更新する

## 権限と秘密情報

- ワークフロー全体または個別ジョブで`permissions:`を最小権限に絞る。
  既定のtoken権限に頼らない（例: `contents: read`が基本、書き込みが必要なジョブのみ`contents: write`）
- secretは`${{ secrets.NAME }}`で参照する。stepの`run`にベタ書きしない
- 信頼できないPRからの`pull_request_target`は厳禁。レビュー前の任意コード実行を許してしまう

## 並行制御と冪等性

- 同一リソースを操作するワークフローには`concurrency:`グループを設定する
  - リリース系は`cancel-in-progress: false`で完走を待つ
  - PR CIなどは`cancel-in-progress: true`で古い実行を打ち切る
- リリース・publish系は再実行時の冪等性を意識する。
  既存タグ・既存リリース・既存パッケージの存在を確認し、二重作成を回避する

## 破壊的ステップと事前検証

- 破壊的・公開系ステップ（タグ作成・push、PyPI publish、コンテナーレジストリpush、リリース作成等）の前に
  事前検証ステップを置く。検証失敗時は破壊的ステップに進ませない
- 破壊的ステップが複数ある場合、より復旧コストの高いものを後ろに置く。
  例: PyPI publishは事実上やり直し不可のため、復旧可能なDocker buildより後にする
- リリース直後の自パッケージ参照は伝播待ちや`exclude-newer`に阻まれることがある。
  ローカル成果物（wheel等）を直接渡す、または該当パッケージを除外指定する

## トリガーと最適化

- `on:`では必要なイベントだけを指定する。`paths`/`paths-ignore`で対象を絞り、
  無関係な変更でCIを走らせない
- `actions/cache`等でビルド成果物を再利用する。キーは依存ファイルのhashを含める
- jobの並列度を意識する。独立な処理は別jobに分けて並列化する

## 出力とstep間連携

- step間の値受け渡しは`$GITHUB_OUTPUT`を使う（`echo "key=value" >> "$GITHUB_OUTPUT"`）。
  古い`set-output`は廃止済み
- `run:`ブロックの先頭に`set -euo pipefail`を置き、未定義変数や途中失敗を確実に止める
