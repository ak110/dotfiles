COMPLETION_KIND = "completion"
CLAUDE_TOOL_NAME = "mcp__plugin_agent-toolkit_agents_server__send_to_user"


def is_completion(message: object, kind: object) -> bool:
    return isinstance(message, str) and (
        kind == COMPLETION_KIND or message.lstrip().partition("\n")[0].strip() == "## 作業完了報告"
    )


def from_repl(tool_use_id: object) -> bool:
    return isinstance(tool_use_id, str) and tool_use_id.startswith("repl_")
