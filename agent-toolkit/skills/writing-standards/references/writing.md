# ドキュメント品質

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/writing.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/writing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/writing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/git-identifier.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/git-identifier.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `textlint-violations.md#「文の書き直しの委譲と検収」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/textlint-violations.md#「文の書き直しの委譲と検収」` |

## 文章の共通規定

メインエージェントのユーザー向け発話には、`agent-toolkit/share/rules-main.md`「ユーザー向け発話ルール」も適用する。

- 書き手はcommitの示し方を読み手に合わせて選ぶ。人向けには日付、件名、PR・MR番号、tagで示し、再取得に使う場合だけ一意な短縮OIDを添える。エージェント向けには一意な短縮OIDを使い、完全OIDは`agent-toolkit:commit`の`references/git-identifier.md`に従って操作の直前に解決する

## ユーザー入力素材の取扱い

文章の起草を`agents_server`の`start`の`write`へ委ねる場合の条件と検収は`textlint-violations.md`「文の書き直しの委譲と検収」に、textlintの指摘への対応は同書「textlint指摘への修正方針」に従う。
