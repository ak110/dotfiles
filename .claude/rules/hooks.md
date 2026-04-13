---
paths:
  - "scripts/claude_hook_*.py"
---

# hookスクリプト開発ガイド（プロジェクト固有）

LLMに行動を促すメッセージには`reason`（Stop/PostToolUse）または`hookSpecificOutput.additionalContext`（PreToolUse）を使う。
`systemMessage`はユーザー向け情報通知専用でLLMに届かない。
フィールドの詳細は`~/.claude/rules/agent-basics/claude-hooks.md`を参照。
