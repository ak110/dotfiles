"""Claude Code agent-toolkit: LLM宛てメッセージ整形共通モジュール。"""

# LLM宛てメッセージの共通サフィックス。
# 詳細はskills/claude-code-standards/references/claude-hooks.mdを参照。
_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"


def llm_notice(body: str, hook_id: str, *, tag: str = "") -> str:
    """LLM宛てメッセージを標準プレフィックス/サフィックス付きで整形する。

    Args:
        body: メッセージ本文。
        hook_id: hook識別子（例: `agent-toolkit/pretooluse`）。
        tag: `warn`等を渡すとプレフィックスに並置する（`[auto-generated: ...][warn]`）。
    """
    prefix = f"[auto-generated: {hook_id}]"
    if tag:
        prefix = f"{prefix}[{tag}]"
    return f"{prefix} {body} {_MESSAGE_SUFFIX}"
