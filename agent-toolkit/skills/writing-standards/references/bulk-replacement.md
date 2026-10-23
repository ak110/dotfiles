# 表記の一括置換

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/bulk-replacement.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/bulk-replacement.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/bulk-replacement.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `textlint-violations.md#「文の書き直しの委譲と検収」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/textlint-violations.md#「文の書き直しの委譲と検収」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/bulk-replace-review.subagent.md` | 判断 | `read:agent-toolkit:*/share/bulk-replace-review.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |

## 運用条件

- `git diff --word-diff=plain --word-diff-regex=.`の文字単位差分をmanaged-tempへ保存し、本書「一括置換後レビュー担当の起動と受領」に従って変更箇所を全件レビューさせる。誤置換が広がる場合は候補を狭めて再試行する

- 指摘された文と候補外の文は自分で書き直すか、`textlint-violations.md`「文の書き直しの委譲と検収」に従って委ねて検収し、最後に通常のtextlintと対象のテストを実行する

## 一括置換後レビュー担当の起動と受領

```text
起動対象: bulk-replace-review.subagent.md
```

本書の冒頭の基準で候補置換を適用したエージェントが、次の手順で一括置換後レビュー担当を起動し、結果を受領する。
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

結果は引数なしの`atk agents wait`で受け取る。問題が報告された文と候補外の文は、本書の冒頭の書き直しの基準に従って書き直す。
