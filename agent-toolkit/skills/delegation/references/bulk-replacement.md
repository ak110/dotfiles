# 表記の一括置換

品質基準の正式な提供元は`agent-basics:writing-standards/references/bulk-replacement.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/bulk-replacement.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/bulk-replacement.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/textlint-violations.md#「文の書き直しの委譲と検収」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/textlint-violations.md#「文の書き直しの委譲と検収」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |

## 運用条件

- `git diff --word-diff=plain --word-diff-regex=.`による文字単位の差分をmanaged-tempへ保存し、後述の「一括置換後レビュー担当の起動と受領」に従って変更箇所を全件レビューさせる。意図しない誤置換が多発する場合は、置換候補を絞り込んで再試行する。

- 指摘を受けた文や置換候補外の文は自身で書き直すか、`agent-toolkit:delegation/references/textlint-violations.md`「文の書き直しの委譲と検収」に従って委譲して検収する。修正完了後に通常のtextlintおよび関連するテストを実行する。

## 一括置換後レビュー担当の起動と受領

```text
起動対象: bulk-replace-review.subagent.md
```

本書冒頭の基準に従って候補置換を適用したエージェントは、以下の手順で一括置換後レビュー担当を起動して結果を受け取る。
レビュー担当の観点は、`agent-toolkit:/share/bulk-replace-review.subagent.md`に従う（所在参照）。返却形式は後述の「一括置換後レビューの返却契約」を使用する。

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

<!-- return-contract:bulk-replace-review:start -->
<!-- 生成元: share/bulk-replace-review.subagent.md「出力」。手動編集しない。 -->
### 一括置換後レビューの返却契約

次の固定形式のみを返却する。`未解決の指摘数`には、問題が見つかった箇所の件数を指定する。

```text
状態: completed
未解決の指摘数: <0以上の整数>
読んだ変更箇所数: <件数>
<問題のある箇所ごとのファイルと行、観点、問題点、修正案。0件なら全件で問題が無いこと>
```

続行不能と追加報告行は、共通契約`../../../share/rules-subagent.md`「返却形式の受け渡し」に従う。
<!-- return-contract:bulk-replace-review:end -->
