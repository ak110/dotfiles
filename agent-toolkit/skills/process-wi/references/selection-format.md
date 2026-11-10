# 選定YAMLの項目と省略規則

選定YAMLは、対象リポジトリごとに生成して保存する。
登録全体の統括でも対象別の取得済み候補から作成し、複数リポジトリの候補数やレーンを1つのYAMLにまとめない。
候補の受け取り手順は[all-repos-orchestration.md](all-repos-orchestration.md)を、候補の分類と検収手順は[selection-procedure.md](selection-procedure.md)を参照する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `all-repos-orchestration.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `selection-procedure.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure.md` |
| 初期配分の見込みと実装中の編集範囲を区別する時 | `../../../share/exec-subagent-implementation.md` | 判断 | `read:agent-toolkit:*/share/exec-subagent-implementation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `run-lanes-integration.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes-integration.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `selection-procedure-addition.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure-addition.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 選定の生成入力・初回配分の算定根拠・共通の読取りと重なりに関する判断・作業の前 | `selection-format-generation.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-format-generation.md` |
| 選定結果の書式・欄の意味に関する判断・作業の前 | `selection-format-schema.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-format-schema.md` |
| 見込みの補正の算出・予測補正記録に関する判断・作業の前 | `selection-format-prediction.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-format-prediction.md` |
