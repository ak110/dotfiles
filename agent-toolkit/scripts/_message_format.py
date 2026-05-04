"""Claude Code agent-toolkit: LLM 宛てメッセージ整形共通モジュール。

`pretooluse.py` / `posttooluse.py` / `stop_advisor.py` から import して使う。

公開 API: `llm_notice(body, hook_id, tag)` のみ。
PEP 723 ヘッダーなし（通常モジュールとして import 可能にするため）。
"""

# LLM 宛てメッセージの共通サフィックス。
# 詳細は skills/writing-standards/references/claude-hooks.md を参照。
_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"


def llm_notice(body: str, hook_id: str, *, tag: str = "") -> str:
    """LLM 宛てメッセージを標準プレフィックス / サフィックス付きで整形する。

    Args:
        body: メッセージ本文。
        hook_id: hook 識別子（例: `agent-toolkit/pretooluse`）。
        tag: `warn` 等を渡すとプレフィックスに並置する（`[auto-generated: ...][warn]`）。
    """
    prefix = f"[auto-generated: {hook_id}]"
    if tag:
        prefix = f"{prefix}[{tag}]"
    return f"{prefix} {body} {_MESSAGE_SUFFIX}"
