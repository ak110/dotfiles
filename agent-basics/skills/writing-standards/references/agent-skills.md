# Agent Skills実装ガイドライン

本書は、Skillの仕様のうち、エージェントの種類を問わず共通して適用できる汎用的な部分を扱う。
Claude Code固有の挙動については、該当する節に「Claude Code固有」と明記する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-documents-additions-volume.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-additions-volume.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-documents-basics-read-tables.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-read-tables.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-documents-basics-ownership.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-ownership.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| YAML frontmatter・ユーザーが起動するスキルの着手手順に関する判断・作業の前 | `agent-skills-metadata.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills-metadata.md` |
| SKILL.md本体とreferences/の役割分担・呼び出し側プロンプトの責務分離・失敗事例の反映に関する判断・作業の前 | `agent-skills-structure.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills-structure.md` |
| 知識を提供する文書と手順を担う文書に関する判断・作業の前 | `agent-skills-procedures.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills-procedures.md` |
| スキル間操作・スキルのパス解決とfork実行（Claude Code固有）に関する判断・作業の前 | `agent-skills-integration.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills-integration.md` |
