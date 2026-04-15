---
paths:
  - "scripts/claude_hook_*.py"
---

# hookスクリプト開発ガイド（プロジェクト固有）

LLMに行動を促すメッセージには`reason`（Stop/PostToolUse）または`hookSpecificOutput.additionalContext`（PreToolUse）を使う。
`systemMessage`はユーザー向け情報通知専用でLLMに届かない。
フィールドの詳細は`plugins/agent-toolkit/skills/claude-meta-rules/references/claude-hooks.md`を参照。
