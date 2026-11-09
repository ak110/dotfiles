# ドキュメント品質

文章全般の品質基準は`agent-basics:writing-standards/references/writing.md`、入力素材の基準は同referencesの`input-material.md`で定義する。
呼び出し元の読込条件に合う資料を選び、本書の運用条件と品質基準の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/writing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/writing.md` |
| ユーザーの入力素材を整理して取り込む前 | `agent-basics:writing-standards/references/input-material.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/input-material.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/references/git-identifier.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/git-identifier.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/textlint-violations.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/textlint-violations.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/textlint-violations.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/textlint-violations.md` |

## 文章の共通規定

メインエージェントのユーザー向け発話には、`agent-toolkit:/share/rules/rules-main.md`「ユーザー向け発話ルール」も適用する。

- コミットの提示方法は、読み手の属性に合わせて適切に選択する。
  人間向けには日付、件名、PR/MR番号、タグを用いて提示し、再取得の用途に必要な場合のみ一意な短縮OIDを添える。
  エージェント向けには一意な短縮OIDを用い、完全なOIDは`agent-toolkit:commit`の`references/git-identifier.md`に従ってGit操作の直前に解決する。

## ユーザー入力素材の取扱い

文章の起草を`agents_server`の`start`で`write`モードに委ねる際の条件や検収基準は、`agent-toolkit:delegation/references/textlint-violations.md`「文の書き直しの委譲と検収」に従う。
また、textlintの指摘への対応方針は`agent-basics:writing-standards/references/textlint-violations.md`「textlint指摘への修正方針」に従う。
