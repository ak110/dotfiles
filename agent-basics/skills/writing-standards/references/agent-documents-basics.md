# エージェント向け文書品質（基本）

本書は、エージェント向け文書の責務、記述内容、条件の書き方、語調の基準を定める。
エージェント向け文書とは、エージェントが直接読み込む文書（`AGENTS.md`、`CLAUDE.md`、ルール、`SKILL.md`、サブエージェント定義、`references/`、委譲先の手順書や委譲元の契約文書、hook関連の文書など）を指す。
規定の追記と記述量の管理基準は、`agent-documents-additions.md`に従う。
本書が理由として挙げる読み手（LLM）の傾向は、運用の中で観測された傾向に基づくリスクモデルであり、個別の実行環境やモデルで実際に当てはまるかは実際の観測を通じて確認する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-documents-additions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-additions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-skills-metadata.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills-metadata.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `sub-agents-claude.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/sub-agents-claude.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-skills.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `sub-agents.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/sub-agents.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `sub-agents-contracts.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/sub-agents-contracts.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-documents-additions-additions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-additions-additions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-actions.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-prevention.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `writing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/writing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/defined-names.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/defined-names.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `writing-readers.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/writing-readers.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `quality-principles-constraints.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/quality-principles-constraints.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `read-method.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/read-method.md` |
| 実作業の知見を保存・整理する前 | `investigation-experience.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-experience.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 冒頭の責務と目的に関する判断・作業の前 | `agent-documents-basics-purpose.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-purpose.md` |
| 置き場所と配送に関する判断・作業の前 | `agent-documents-basics-placement.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-placement.md` |
| 読込表に関する判断・作業の前 | `agent-documents-basics-read-tables.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-read-tables.md` |
| 定義の所有者・判断の偏りへの対策・既存規範による被覆の判定・主要用語の初出定義先に関する判断・作業の前 | `agent-documents-basics-ownership.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-ownership.md` |
| 記述内容の取捨・条件と範囲の記述に関する判断・作業の前 | `agent-documents-basics-content.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-content.md` |
| 語調と現行規則の表現に関する判断・作業の前 | `agent-documents-basics-tone.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-tone.md` |
