# 一括置換後レビュー担当の起動と受領

```text
起動対象: bulk-replace-review.subagent.md
```

`agent-toolkit:writing-standards`の`references/notation-rules.md`「表記の一括置換」で候補置換を適用した主体が、本書に従って一括置換後レビュー担当を起動し、結果を受領する。
レビュー担当の観点と返却形式は`${CLAUDE_PLUGIN_ROOT}/share/bulk-replace-review.subagent.md`が定める。

## 起動

置換した範囲の`git diff --word-diff=plain --word-diff-regex=.`をセッションのmanaged-temp（`agent-toolkit:managed-temp`）のファイルへ保存する。
その後、1件のレビュー担当を起動する。

`agents_server`の`start`を次の引数で呼ぶ。起動の定型と適用する義務は`agent-toolkit:delegation`の「`<役割名>.parent.md`を持つ委譲の起動」に従う。

| 引数 | 値 |
| --- | --- |
| `cwd` | 置換した作業ツリーの絶対パス |
| `subagent_md_path` | `bulk-replace-review` |
| `extra_params` | 次の名前付き入力だけ |
| `mode` | 指定しない |
| `model_type` | 指定しない（サーバーが工程別設定を使う） |

- `差分ファイル`: 保存した差分ファイルの絶対パスと行数

## 受領

結果は引数なしの`atk agents wait`で受け取る。問題が報告された文と候補外の文は、`agent-toolkit:writing-standards`の`references/notation-rules.md`「表記の一括置換」の手順4に従って書き直す。
