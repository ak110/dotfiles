# Agent Skills実装ガイドライン

本書は、Skillの仕様のうち、エージェントの種類を問わず共通して適用できる汎用的な部分を扱う。
Claude Code固有の挙動については、該当する節に「Claude Code固有」と明記する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| YAML frontmatter・ユーザーが起動するスキルの着手手順に関する判断・作業の前 | `agent-skills-metadata.md` |
| SKILL.md本体とreferences/の役割分担・呼び出し側プロンプトの責務分離・失敗事例の反映に関する判断・作業の前 | `agent-skills-structure.md` |
| 知識を提供する文書と手順を担う文書に関する判断・作業の前 | `agent-skills-procedures.md` |
| スキル間操作・スキルのパス解決とfork実行（Claude Code固有）に関する判断・作業の前 | `agent-skills-integration.md` |
