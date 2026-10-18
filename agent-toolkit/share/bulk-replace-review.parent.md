# 一括置換後レビュー担当の起動と受領

```text
起動対象: bulk-replace-review.subagent.md
```

`agent-toolkit:writing-standards`の`references/notation-rules.md`「表記の一括置換」で候補置換を適用したエージェントが、本書に従って一括置換後レビュー担当を起動し、結果を受領する。
レビュー担当の観点と返却形式は`${CLAUDE_PLUGIN_ROOT}/share/bulk-replace-review.subagent.md`が定める。

## 起動

置換した範囲の`git diff --word-diff=plain --word-diff-regex=.`をmanaged-temp（`agent-toolkit:managed-temp`）のファイルへ保存し、`agents_server`の`start`を次の引数で呼んで1件起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 置換した作業ツリーの絶対パス |
| `subagent_md_path` | `bulk-replace-review` |
| `extra_params` | 次の名前付き入力だけ |

- `差分ファイル`: 保存した差分ファイルの絶対パス

差分は読取可能なUTF-8通常ファイルにする。起動側は保存先がUTF-8の通常ファイルで読み取れるか確かめる。不足なら差分の保存と`start`の再発行を案内する。差分の内容の検収はレビュー担当が担う。

## 受領

結果は引数なしの`atk agents wait`で受け取る。問題が報告された文と候補外の文は、`agent-toolkit:writing-standards`の`references/notation-rules.md`「表記の一括置換」の、問題が報告された文と候補外の文を書き直す段落に従って書き直す。
