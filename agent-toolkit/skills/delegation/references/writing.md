# ドキュメント品質

品質基準の正式な提供元は、`agent-basics:writing-standards/references/writing.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/writing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/writing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/references/git-identifier.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/git-identifier.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/textlint-violations.md#「文の書き直しの委譲と検収」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/textlint-violations.md#「文の書き直しの委譲と検収」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/textlint-violations.md#「textlint指摘への修正方針」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/textlint-violations.md#「textlint指摘への修正方針」` |

## 文章の共通規定

メインエージェントのユーザー向け発話には、`agent-toolkit:/share/rules-main.md`「ユーザー向け発話ルール」も適用する。

- コミットの提示方法は、読み手の属性に合わせて適切に選択する。人間向けには日付、件名、PR/MR番号、タグを用いて提示し、再取得の用途に必要な場合のみ一意な短縮OIDを添える。エージェント向けには一意な短縮OIDを用い、完全なOIDは`agent-toolkit:commit`の`references/git-identifier.md`に従ってGit操作の直前に解決する。

## ユーザー入力素材の取扱い

文章の起草を`agents_server`の`start`で`write`モードに委ねる際の条件や検収基準は、`agent-toolkit:delegation/references/textlint-violations.md`「文の書き直しの委譲と検収」に従う。また、textlintの指摘への対応方針は`agent-basics:writing-standards/references/textlint-violations.md`「textlint指摘への修正方針」に従う。
