---
paths:
  - "scripts/claude_hook_*.py"
---

# hookスクリプト開発ガイド（プロジェクト固有）

LLMに行動を促すメッセージには`reason`（Stop/PostToolUse）または`hookSpecificOutput.additionalContext`（PreToolUse）を使う。
`systemMessage`はユーザー向け情報通知専用でLLMに届かない。
LLM宛ての出力には自動生成であることを示すプレフィックスとサフィックスを必ず付ける。

- プレフィックス: `[auto-generated: <plugin>/<hook>]`（警告時は`[warn]`タグを並置）
- サフィックス: `(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)`

フィールドの詳細と規約の背景は`plugins/agent-toolkit/skills/writing-standards/references/claude-hooks.md`を参照。
