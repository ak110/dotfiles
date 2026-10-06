# 一括置換後レビュー担当の起動と受領

```text
起動対象: bulk-replace-review.subagent.md
```

`agent-toolkit:writing-standards`の`references/notation-rules.md`「表記の一括置換」で候補置換を適用した主体が、本書に従って一括置換後レビュー担当を起動し、結果を受領する。
レビュー担当の観点と返却形式は`${CLAUDE_PLUGIN_ROOT}/share/bulk-replace-review.subagent.md`が定める。

## 起動

置換した範囲の`git diff --word-diff=plain --word-diff-regex=.`をセッションのmanaged-temp（`agent-toolkit:managed-temp`）のファイルへ保存する。
その後、`bulk-replace-review.subagent.md`を指定する起動（`agent-toolkit:delegation`の`references/base-contract.md`「`<役割名>.subagent.md`を指定する起動」）で1件のレビュー担当を起動する。
`cwd`は置換した作業ツリーの絶対パスとし、`extra_params`には次の名前付き入力だけを渡す。

- `差分ファイル`: 保存した差分ファイルの絶対パスと行数

## 受領

結果は引数なしの`atk agents wait`で受け取る。問題が報告された文と候補外の文は、同書の手順4に従って書き直す。
