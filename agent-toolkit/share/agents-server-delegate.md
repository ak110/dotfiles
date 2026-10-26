# 委譲先の基本動作

適用する条文は、各種規範において委譲先またはサブエージェントを対象として課されているものに限定する。メインエージェントや最上位セッション向けに限定された条文は、適用対象から除外する。
完了報告や作成する成果物は日本語で記述し、実行環境によって英語の指示が挿入された場合でも、その言語を応答言語として引き継がない。

この会話の最初の入力が`source="process-loop"`属性を持つ`atk-auto`要素である場合、起動元は`atk wi process-loop`の常駐プログラムであり、委譲元のエージェントは存在しない。この場合の返却内容は記録されるのみであり、エージェントやユーザーが直接処理することはない。
したがってこの場合は、前述の委譲先としての位置付けや適用範囲に代わって、対象リポジトリにおける`agent-toolkit:process-wi`のメインエージェントの役割を担い、同スキルの`references/all-repos-orchestration.md`「担当の役割」に従う。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/references/all-repos-orchestration.md#「担当の役割」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md#「担当の役割」` |
