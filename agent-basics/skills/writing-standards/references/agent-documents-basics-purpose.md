# エージェント向け文書品質（基本）：冒頭の責務と目的

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-skills-metadata.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills-metadata.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `sub-agents-claude.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/sub-agents-claude.md` |
| 実作業の知見を保存・整理する前 | `investigation-experience.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-experience.md` |

## 冒頭の責務と目的

- 特定の役割が読む文書（agent定義、スキル、委譲先の手順書、委譲元の契約文書など）では、担当する責務、担当しない責務、最初に行う行動を冒頭で明示する。
  読み手は文書内に登場する他者の役割を自身の役割と混同することがあるため、読み手自身の責務を冒頭で明確にする。
- 特定の役割が作業順に従う手順を担う文書（委譲先の手順書や役割ごとの参照資料を含む）は、`agent-skills-procedures.md`「知識を提供する文書と手順を担う文書」の構成に従う。
- 冒頭の1〜2文で、適用判断の基準となる中核的な目的を述べる。
  `description`に記載する範囲は、スキルでは`agent-skills-metadata.md`「YAML frontmatter」、サブエージェント定義では`sub-agents-claude.md`「Claude Codeのfrontmatter」に従う。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| Agent Skills実装ガイドライン：知識を提供する文書と手順を担う文書の基準を適用する前 | `agent-skills-procedures.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills-procedures.md` |
