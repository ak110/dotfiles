# エージェント向け文書品質（基本）

本書は、エージェント向け文書の責務、記述内容、条件の書き方、語調の基準を定める。
エージェント向け文書とは、エージェントが直接読み込む文書（`AGENTS.md`、`CLAUDE.md`、ルール、`SKILL.md`、サブエージェント定義、`references/`、委譲先の手順書や委譲元の契約文書、hook関連の文書など）を指す。
規定の追記と記述量の管理基準は、`agent-documents-additions.md`に従う。
本書が理由として挙げる読み手（LLM）の傾向は、運用の中で観測された傾向に基づくリスクモデルであり、個別の実行環境やモデルで実際に当てはまるかは実際の観測を通じて確認する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 冒頭の責務と目的に関する判断・作業の前 | `agent-documents-basics-purpose.md` |
| 置き場所と配送に関する判断・作業の前 | `agent-documents-basics-placement.md` |
| 読込表に関する判断・作業の前 | `agent-documents-basics-read-tables.md` |
| 定義の所有者・判断の偏りへの対策・既存規範による被覆の判定・主要用語の初出定義先に関する判断・作業の前 | `agent-documents-basics-ownership.md` |
| 記述内容の取捨・条件と範囲の記述に関する判断・作業の前 | `agent-documents-basics-content.md` |
| 語調と現行規則の表現に関する判断・作業の前 | `agent-documents-basics-tone.md` |
