# 遮断・警告フックの成立条件と設計の型

本書は、遮断（block）や警告（warn）を行うフックを設置するかどうかの判断基準、遮断と警告の選択基準、発火対象とするエージェントの限定方法、「事前に防ぐ型」と「遮断後に対処する型」の選定方針を定める。
通知本文の整形基準は`claude-hooks-messages-format.md`「通知本文の整形」に、`Stop`および`SubagentStop`に固有の条件は`claude-hooks-stop.md`に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 遮断・警告フックの成立条件に関する判断・作業の前 | `claude-hooks-block-warn-eligibility.md` |
| 遮断と警告の選択・迂回手段に関する判断・作業の前 | `claude-hooks-block-warn-behavior.md` |
| 発火対象のエージェントの限定・設計の型に関する判断・作業の前 | `claude-hooks-block-warn-design.md` |
