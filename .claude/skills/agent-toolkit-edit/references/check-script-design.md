# 機械チェックスクリプトの設計原則

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/check-script-design.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/check-script-design.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/check-script-design.md` |

## 構造化文書の解析

設計の詳細は`agent-toolkit/agent_toolkit/_plan/structure/__init__.py`のモジュールdocstringを参照する。
