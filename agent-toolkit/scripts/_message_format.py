"""Claude Code agent-toolkit: コーディングエージェント宛てメッセージ整形共通モジュール。

LLMに行動を促すメッセージには`reason`（Stop/PostToolUse）または
`hookSpecificOutput.additionalContext`（PreToolUse）を使う。
`systemMessage`はユーザー向け情報通知専用でLLMに届かない。

LLM宛て出力には自動生成を示すプレフィックスとサフィックスを必ず付ける。
本モジュールの`llm_notice`関数が以下のフォーマットで整形する。

- プレフィックス: `[auto-generated: <plugin>/<hook>]`（警告時は`[warn]`タグを並置）
- サフィックス: `(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)`

フィールドの詳細と規約の背景は
`agent-toolkit/skills/claude-code-standards/references/claude-hooks.md`を参照する。
"""

_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"


def llm_notice(body: str, hook_id: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス/サフィックス付きで整形する。

    Args:
        body: メッセージ本文。
        hook_id: hook識別子（例: `agent-toolkit/pretooluse`）。
        tag: `warn`等を渡すとプレフィックスに並置する（`[auto-generated: ...][warn]`）。
    """
    prefix = f"[auto-generated: {hook_id}]"
    if tag:
        prefix = f"{prefix}[{tag}]"
    return f"{prefix} {body} {_MESSAGE_SUFFIX}"
