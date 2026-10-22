# 遮断・警告フックの成立条件と設計の型

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-block-warn.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn.md` |

## 遮断と警告の選択

警告とした判定のうち、同一セッションでの反復が母集団の欠落や工程の停止を招くもの（復元できない結果に当たる）は、`warning_formatter`へ`escalate_on_repeat=True`を明示し、1件目を警告、2件目以降を遮断とする。指定しない場合は偽とする。`removable_cause=True`は反復注記に使い、遮断には昇格の明示を要する。後続の編集で是正できる文体などの警告は昇格させない。昇格したblockはPreToolUseの`exit_with`が終了コード2で返し、保留した警告もstderrへ配送する。
