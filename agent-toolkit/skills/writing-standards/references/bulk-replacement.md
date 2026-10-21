# 表記の一括置換

多数の箇所で同じ働きの語へ替える場合（1対1の表記統一と、文脈で有限の候補から選べる反復表現）は、候補置換と変更箇所のレビューを組にする。代表例で候補が指示対象、主体、条件、否定と例外を保てるかを試し、保てる部分を一括置換し、候補外と意味を保ちにくい部分だけ個別に書き直す。直接修正の方が低費用で品質を保てる少量の箇所は、QCDに従い担当自身が直す。

- 置換前に`git status --porcelain=v1 -- <対象パス>`で対象に未コミットの変更が無いことを確かめる。変更したパスだけを戻す場合も、着手前に自分の未コミット変更が無かった対象に限る
- 適用直前に`file-editing.md`「ファイル更新時の編集ツールの前提」で対象ごとの読取状態を確かめる
- `git diff --word-diff=plain --word-diff-regex=.`の文字単位差分をmanaged-tempへ保存し、本書「一括置換後レビュー担当の起動と受領」に従って変更箇所を全件レビューさせる。誤置換が広がる場合は候補を狭めて再試行する
- 指摘された文と候補外の文は自分で書き直すか、`textlint-violations.md`「文の書き直しの委譲と検収」に従って委ねて検収し、最後に通常のtextlintと対象のテストを実行する

## 一括置換後レビュー担当の起動と受領

```text
起動対象: bulk-replace-review.subagent.md
```

`agent-toolkit:writing-standards`の`references/notation-rules.md`「表記の一括置換」で候補置換を適用したエージェントが、本書に従って一括置換後レビュー担当を起動し、結果を受領する。
レビュー担当の観点と返却形式は`${CLAUDE_PLUGIN_ROOT}/share/bulk-replace-review.subagent.md`が定める。

### 起動

置換した範囲の`git diff --word-diff=plain --word-diff-regex=.`をmanaged-temp（`agent-toolkit:managed-temp`）のファイルへ保存し、`agents_server`の`start`を次の引数で呼んで1件起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 置換した作業ツリーの絶対パス |
| `subagent_md_path` | `bulk-replace-review` |
| `extra_params` | 次の名前付き入力だけ |

- `差分ファイル`: 保存した差分ファイルの絶対パス

差分は読取可能なUTF-8通常ファイルにする。起動側は保存先がUTF-8の通常ファイルで読み取れるか確かめる。不足なら、起動側が差分をUTF-8の通常ファイルとして保存し直し、`start`を再発行する。差分の内容の検収はレビュー担当が担う。

### 受領

結果は引数なしの`atk agents wait`で受け取る。問題が報告された文と候補外の文は、`agent-toolkit:writing-standards`の`references/notation-rules.md`「表記の一括置換」の、問題が報告された文と候補外の文を書き直す段落に従って書き直す。
