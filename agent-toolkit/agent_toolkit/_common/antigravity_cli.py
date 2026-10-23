"""Antigravity CLIの非対話引数と、stream-jsonの会話識別を共有する。"""

from typing import Any


def build_command(
    prompt: str, model: str | None, effort: str | None, conversation_id: str | None, *, timeout: int = 3600
) -> list[str]:
    """非対話起動と同じ会話の再開を、agyの公開CLI引数で表す。"""
    command = ["agy", "-p", prompt, "--output-format", "stream-json", "--print-timeout", f"{timeout}s"]
    if model is not None:
        command += ["--model", model]
    if effort is not None:
        command += ["--effort", effort]
    if conversation_id is not None:
        command += ["--conversation", conversation_id]
    # 非対話作業では、起動元が認可した変更をツールの確認待ちで止めない。
    command.append("--dangerously-skip-permissions")
    return command


def event_session_id(payload: dict[str, Any]) -> str | None:
    """initイベントが確定した会話識別子を返す。"""
    if (payload.get("type") or payload.get("event")) != "init":
        return None
    value = payload.get("conversation_id") or payload.get("conversationId")
    return value if isinstance(value, str) and value else None
