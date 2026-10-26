# 表記の一括置換

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/bulk-replacement.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選択した資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断へ進む。起動時の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/bulk-replacement.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/bulk-replacement.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `textlint-violations.md#「文の書き直しの委譲と検収」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/textlint-violations.md#「文の書き直しの委譲と検収」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |

## 運用条件

- `git diff --word-diff=plain --word-diff-regex=.`による文字単位の差分をmanaged-tempへ保存し、後述の「一括置換後レビュー担当の起動と受領」に従って変更箇所を全件レビューさせる。意図しない誤置換が多発する場合は、置換候補を絞り込んで再試行する。

- 指摘を受けた文や置換候補外の文は自身で書き直すか、`textlint-violations.md`「文の書き直しの委譲と検収」に従って委譲して検収する。修正完了後に通常のtextlintおよび関連するテストを実行する。

## 一括置換後レビュー担当の起動と受領

```text
起動対象: bulk-replace-review.subagent.md
```

本書冒頭の基準に従って候補置換を適用したエージェントは、以下の手順で一括置換後レビュー担当を起動して結果を受け取る。
レビュー担当の観点および返却形式は、`${CLAUDE_PLUGIN_ROOT}/share/bulk-replace-review.subagent.md`に従う（所在参照）。

### 起動

置換した範囲の`git diff --word-diff=plain --word-diff-regex=.`をmanaged-temp（`agent-toolkit:managed-temp`）内のファイルへ保存し、`agents_server`の`start`を以下の引数で呼び出して1件起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 置換した作業ツリーの絶対パス |
| `subagent_md_path` | `bulk-replace-review` |
| `extra_params` | 次の名前付き入力だけ |

- `差分ファイル`: 保存した差分ファイルの絶対パス

差分ファイルは読み取り可能なUTF-8の通常ファイルとする。起動側は、保存先がUTF-8の通常ファイルとして読み取れる状態にあることを確認する。満たしていない場合はUTF-8の通常ファイルとして保存し直した上で`start`を再発行する。差分内容の妥当性確認はレビュー担当が担う。

### 受領

結果は引数なしの`atk agents wait`で受け取る。問題が報告された文や置換候補外の文は、本書冒頭の基準に従って書き直す。
