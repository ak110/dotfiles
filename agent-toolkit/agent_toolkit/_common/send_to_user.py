COMPLETION_KIND = "completion"
COMPLETION_ENDING = "以上で、このセッションの作業は全て終わった。"
CLAUDE_TOOL_NAME = "mcp__plugin_agent-toolkit_agents_server__send_to_user"


def normalize_completion_message(message: str) -> str:
    endings = (COMPLETION_ENDING, COMPLETION_ENDING.removesuffix("。"))
    end = len(message)
    while end:
        if message[end - 1].isspace() or message[end - 1] == "…":
            end -= 1
        elif ending := next((suffix for suffix in endings if message.endswith(suffix, 0, end)), ""):
            end -= len(ending)
        else:
            break
    return f"{message[:end]}\n\n{COMPLETION_ENDING}" if end else COMPLETION_ENDING


def is_completion(message: object, kind: object) -> bool:
    return isinstance(message, str) and (
        kind == COMPLETION_KIND or message.lstrip().partition("\n")[0].strip() == "## 作業完了報告"
    )


def from_repl(tool_use_id: object) -> bool:
    return isinstance(tool_use_id, str) and tool_use_id.startswith("repl_")
