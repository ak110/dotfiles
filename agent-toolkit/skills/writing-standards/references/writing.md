# ドキュメント品質

品質基準の正式な提供元は、`agent-basics:writing-standards`の`references/writing.md`である。呼び出し元が指定した条件や読込範囲は、同名の正式な品質資料へそのまま引き継ぐ。節の指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を維持する。

以下はagent-toolkitが所有する運用条件である。呼び出し元の条件と読込範囲に従い、ここに記載された運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を守り、選択した資料の指定範囲を取得してから、その基準に基づく具体的な判断へ進む。起動行に記載された資料は、起動時の本文とともに読み込まれる。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/writing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/writing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/git-identifier.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/git-identifier.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `textlint-violations.md#「文の書き直しの委譲と検収」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/textlint-violations.md#「文の書き直しの委譲と検収」` |

## 文章の共通規定

メインエージェントのユーザー向け発話には、`agent-toolkit/share/rules-main.md`「ユーザー向け発話ルール」も適用する。

- コミットの提示方法は、読み手の属性に合わせて適切に選択する。人間向けには日付、件名、PR/MR番号、タグを用いて提示し、再取得の用途に必要な場合のみ一意な短縮OIDを添える。エージェント向けには一意な短縮OIDを用い、完全なOIDは`agent-toolkit:commit`の`references/git-identifier.md`に従ってGit操作の直前に解決する。

## ユーザー入力素材の取扱い

文章の起草を`agents_server`の`start`で`write`モードに委ねる際の条件や検収基準は、`textlint-violations.md`「文の書き直しの委譲と検収」に従う。また、textlintの指摘への対応方針は同書「textlint指摘への修正方針」に従う。
