from __future__ import annotations

import json
import typing

from agent_toolkit._atk.session_record_format import SessionEvent, claude_events


class Progress:
    """stderrが端末の実行だけ会話の進捗を表示し、ログでは生診断と区別する。  pyfltr: allow-comment

    readerからの呼出は生診断の書込と同じロックの内側で行い、ログと表示の順序を保つ。
    """

    def __init__(self, log: typing.TextIO, stderr: typing.TextIO) -> None:
        self.log = log
        self.stderr = stderr
        self.enabled = stderr.isatty()
        self._seen_tools: set[str] = set()

    def message(self, text: str) -> None:
        self.log.write(f"{text}\n")
        self.log.flush()
        if self.enabled:
            print(text, file=self.stderr, flush=True)

    def receive(self, engine: str, line: str) -> None:
        if not self.enabled:
            return
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(record, dict):
            return
        if engine == "claude":
            events = claude_events([record])[0]
        else:
            if record.get("type") == "thread.started" and isinstance(record.get("thread_id"), str):
                self.message(f"Codexのthread識別子: {record['thread_id']}")
            events = _codex_events(record)
        for text in _projected_messages(events, self._seen_tools):
            self.log.write("".join(f"[progress] {line}\n" for line in _projected_lines(text)))
            self.log.flush()
            print(text, file=self.stderr, flush=True)


def _projected_text(event: SessionEvent) -> str | None:
    if event.kind == "assistant" and event.text:
        return f"assistant: {event.text}"
    if event.kind == "tool_call" and event.name:
        return f"tool: {event.name}"
    return None


def _projected_messages(events: list[SessionEvent], seen_tools: set[str]) -> typing.Iterator[str]:
    for event in events:
        text = _projected_text(event)
        if text is None:
            continue
        if event.kind == "tool_call" and event.call_id is not None:
            if event.call_id in seen_tools:
                continue
            seen_tools.add(event.call_id)
        yield text


def legacy_projection(payload: str, seen_tools: set[str]) -> list[str]:
    """旧ログでモデルの構造化行に直結していた端末用の派生表示を返す。  pyfltr: allow-comment"""
    try:
        record = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if not isinstance(record, dict):
        return []
    events = claude_events([record])[0] if record.get("type") == "assistant" else _codex_events(record)
    return [line for text in _projected_messages(events, seen_tools) for line in _projected_lines(text)]


def _projected_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _codex_events(record: dict[str, typing.Any]) -> list[SessionEvent]:
    event_type = record.get("type")
    item = record.get("item")
    if event_type not in {"item.started", "item.completed"} or not isinstance(item, dict):
        return []
    item_type = item.get("type")
    if item_type == "agent_message" and event_type == "item.completed":
        text = item.get("text")
        return [SessionEvent(kind="assistant", timestamp=None, text=text)] if isinstance(text, str) else []
    tool_names = {
        "command_execution": "command_execution",
        "file_change": "file_change",
        "mcp_tool_call": "mcp_tool_call",
        "web_search": "web_search",
    }
    if not isinstance(item_type, str) or item_type not in tool_names:
        return []
    name = tool_names[item_type]
    if item_type == "mcp_tool_call" and isinstance(item.get("tool"), str):
        name = f"{item.get('server', 'mcp')}/{item['tool']}"
    call_id = item.get("id")
    return [SessionEvent(kind="tool_call", timestamp=None, name=name, call_id=call_id if isinstance(call_id, str) else None)]
