# 固定課題による多ターン試行

<!-- 節をまたぐ手順を通し番号で示す。 -->
<!-- markdownlint-disable MD029 -->

多ターン試行は、独立したユーザー役エージェントが質問に応じて段階的に情報を開示し、評価対象エージェントが対話を重ねて提出した成果物を採点する手動検証である。
進行を担当する仲介エージェントが両者のやりとりを中継し、対話履歴全体と提出された成果物をもとに採点する。
目的に応じ、批判的通読、単ターンのシナリオ評価、読み取り専用文書の判定試行も選べる。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/delegation/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 素材と準備に関する判断・作業の前 | `multi-turn-trials-preparation.md` | 判断 | `read:agent-toolkit:*/skills/refine-prompt/references/multi-turn-trials-preparation.md` |
| 両役の入力と返却・仲介と終了に関する判断・作業の前 | `multi-turn-trials-dialogue.md` | 判断 | `read:agent-toolkit:*/skills/refine-prompt/references/multi-turn-trials-dialogue.md` |
| 保存対話と採点・改訂前後の比較と受領に関する判断・作業の前 | `multi-turn-trials-grading.md` | 判断 | `read:agent-toolkit:*/skills/refine-prompt/references/multi-turn-trials-grading.md` |
| 実セッション由来の小課題による比較・大きな削減の3条件比較に関する判断・作業の前 | `multi-turn-trials-comparisons.md` | 判断 | `read:agent-toolkit:*/skills/refine-prompt/references/multi-turn-trials-comparisons.md` |
