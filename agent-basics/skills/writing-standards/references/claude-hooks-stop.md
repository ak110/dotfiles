# Stop/SubagentStopフック

本書では、StopフックおよびSubagentStopフックにおける遮断の適用条件、再帰呼び出しへの対策、入力データの扱い、Codex環境との差異、および委譲先でフックが発火する際の注意点を定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 遮断と警告を選ぶ前 | `claude-hooks-block-warn-behavior.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 遮断の条件と再帰呼び出し対策・入力に関する判断・作業の前 | `claude-hooks-stop-common.md` |
| Codexの差分に関する判断・作業の前 | `claude-hooks-stop-codex.md` |
| 委譲先での発火に関する判断・作業の前（操作対象外: hookの適用範囲の設計） | `claude-hooks-stop-delegates.md` |
