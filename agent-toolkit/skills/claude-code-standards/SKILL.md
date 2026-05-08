---
name: claude-code-standards
description: >
  Claude Code設定ファイル（`CLAUDE.md`・`.claude/rules/`・`.claude/skills/`・hooks関連ファイルなど）の
  新規作成・修正・計画・レビュー時に`agent-toolkit:writing-standards`と必ず併用して呼び出す。
# 編集時の注意点:
# 一般ドキュメント品質方針はwriting-standardsが担当する。
# 本スキルはClaude Code設定ファイル固有の追加事項のみを扱う補完スキル。
# 単独では不足するため、必ずwriting-standardsと両方読み込む前提で記述する。
---

# Claude Code設定ファイル品質

`CLAUDE.md`・`.claude/*`・skills・agents・hooks・pluginsなどのClaude Code設定ファイルが対象となる。
これらはLLMのコンテキストへ直接投入されるため、通常のMarkdown品質基準に加えて専用の記述原則が必要となる。
本スキルはClaude Code固有事項のみを補い、一般ドキュメント品質方針（章構成・改訂運用・Markdown細則・
README規約・技術文書の書き方など）は`agent-toolkit:writing-standards`が担当する。
両者を必ず併用する。

## 公式スキル

公式マーケットプレイス（`anthropics/claude-plugins-official`）から以下のプラグイン・スキルが提供されている。
関連するものを必ず参照する。
インストールされていない場合はユーザーにインストールを促す。

- `skill-creator:skill-creator`
- `plugin-dev:skill-development`
- `plugin-dev:agent-development`
- `plugin-dev:hook-development`
- `plugin-dev:plugin-structure`

## 追加リファレンス

上記スキルを前提とした補足情報。
こちらも関連するものを必ず参照する。

- `references/claude-common.md`: 共通原則（種類を問わず必ず読む）
- `references/claude-skills.md`: スキル編集時
- `references/claude-hooks.md`: hook・hookから呼び出されるスクリプトの編集時
