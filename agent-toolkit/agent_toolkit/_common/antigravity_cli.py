"""Antigravity CLIの非対話引数と、stream-jsonの会話識別を共有する。"""

from typing import Any


def build_command(
    prompt: str, model: str | None, effort: str | None, conversation_id: str | None, *, timeout: int = 3600
) -> list[str]:
    command = ["agy", "-p", prompt, "--output-format", "stream-json", "--print-timeout", f"{timeout}s"]
    if model is not None:
        command += ["--model", model]
    if effort is not None:
        command += ["--effort", effort]
    if conversation_id is not None:
        command += ["--conversation", conversation_id]

    command.append("--dangerously-skip-permissions")
    return command


def event_session_id(payload: dict[str, Any]) -> str | None:
    if (payload.get("type") or payload.get("event")) != "init":
        return None
    value = payload.get("conversation_id") or payload.get("conversationId")
    return value if isinstance(value, str) and value else None
