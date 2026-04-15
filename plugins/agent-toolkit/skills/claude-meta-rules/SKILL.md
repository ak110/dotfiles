---
name: claude-meta-rules
description: CLAUDE.md・.claude/rules/・.claude/skills/・hooksなどClaude Code設定系ファイルの記述ガイドライン。CLAUDE.md、`.claude/rules/`配下の.md、`.claude/skills/`配下のSKILL.md、プラグインのhook設定やhookスクリプト、`plugin.json`・`marketplace.json`などClaude Codeの設定ファイル群を編集・追加する時に呼び出すこと。Claudeの訓練データに無い新機能仕様の補完と、コンテキスト汚染を避ける記述原則をまとめている。対象別の詳細は references/ 配下を必要に応じて読む。
user-invocable: true
---

# CLAUDE.md・ルール・スキル・フック記述ガイドライン

Claude Code設定ファイル群はLLMのコンテキストへ直接投入される。
記述した内容がそのまま生成候補に影響するため、以下の共通原則と対象別ガイドに従う。

## 対象別リファレンスの使い分け

編集対象に応じて、以下のreferencesを読み込む。

- `references/claude-md-guide.md`: CLAUDE.md・ルール・スキルの使い分け、コンテキスト汚染の回避、システムプロンプトとの整合性
- `references/claude-rules.md`: `.claude/rules/` 配下のルールファイル編集ガイドライン（`paths` frontmatter、ファイル構成、記述のコツ）
- `references/claude-skills.md`: `.claude/skills/` 配下のスキル編集ガイドライン（ディレクトリ構造、progressive disclosure、YAML frontmatter、変数）
- `references/claude-hooks.md`: hook・プラグインスクリプトの出力フィールドガイドライン（`systemMessage` / `reason` / `additionalContext` の使い分け、メッセージ記述言語）

複数対象を編集する場合は該当するreferencesを必要な分だけ読む。

## 共通の記述原則（全対象）

### コンテキスト汚染の回避

- 不適切な表現や禁止パターンの具体例をそのまま書かない（コンテキストに混入し生成されやすくなるため）
  - 具体例が必要な場合は抽象化した表現にとどめる
- 禁止事項と推奨事項を明確に分離する（セクションや箇条書きの階層で区切る）

### 理由を添える

- 「なぜそうするか」を添える。モデルは理由を理解すると想定外のケースにも適切に対応できる
- 具体的な推奨例を含めると遵守率が上がる
- 矛盾する指示がないか定期的に確認する（複数ファイル間の矛盾はモデルが任意に選択する原因になる）

### システムプロンプト・組み込みツールとの整合性

- 表現を極力システムプロンプトと合わせる（訳語として自然な用語を使用する）
  - 例えばユーザーが「〇〇と追記して」と要求した場合も、システムプロンプトで定義された用語を優先する（文章が大幅に変わる場合は確認をとる）
- 極力システムプロンプトと矛盾しない、かつ重複しない指示にする
  - やむを得ずシステムプロンプトと矛盾する指示を書く場合はその旨注記する
