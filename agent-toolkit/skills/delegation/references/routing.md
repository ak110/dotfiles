# 委譲の要否判定と委譲単位の確定

本書は、`agent-toolkit/share/rules/common/01-agent.md`「委譲の要否判定」に基づき、委譲するかどうかの判断基準、委譲単位の決定手順、および読み取り専用の探索調査を委譲する際の選定方法を定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 委譲する部分を選び、起動手段とモデルを確定する前 | `runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` |
| 委譲する部分を選び、起動手段とモデルを確定する前 | `process-model-settings.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/process-model-settings.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` |
| 定型処理の実行先をモデル選定前に比較する時 | `agent-basics:writing-standards/references/design-heuristics-decisions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-decisions.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 要否判定に関する判断・作業の前 | `routing-decisions.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing-decisions.md` |
| 委譲単位の確定に関する判断・作業の前 | `routing-units.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing-units.md` |
| 会話を引き継ぐ委譲に関する判断・作業の前 | `routing-fork.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing-fork.md` |
| コンテキスト消費が大きい調査の切り出し・検索結果に基づく探索の分担に関する判断・作業の前 | `routing-investigation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing-investigation.md` |
| 実行結果の検証に関する判断・作業の前 | `routing-experiments.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing-experiments.md` |
