"""run-skillのCLIイベントを既存の表示イベントへ変換し、端末へ進捗を届ける。"""

from __future__ import annotations

import json
import typing

from agent_toolkit._atk.session_record_format import SessionEvent, claude_events


class Progress:
    """stderrが端末の実行だけ進捗を表示し、表示した内容を生診断と同じログに残す。

    readerからの呼出は生診断の書込と同じロックの内側で行い、ログと表示の順序を保つ。
    """

    def __init__(self, log: typing.TextIO, stderr: typing.TextIO) -> None:
        self.log = log
        self.stderr = stderr
        self.enabled = stderr.isatty()
        self._seen_tools: set[str] = set()

    def message(self, text: str) -> None:
        """開始情報または正規化した進捗をログへ記録し、TTYへ直ちに表示する。"""
        self.log.write(f"{text}\n")
        self.log.flush()
        if self.enabled:
            print(text, file=self.stderr, flush=True)

    def receive(self, engine: str, line: str) -> None:
        """1件のJSONイベントから発言とtool呼出だけを表示する。生診断は呼出元が保存する。"""
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
        for event in events:
            if event.kind == "assistant" and event.text:
                self.message(f"assistant: {event.text}")
            elif event.kind == "tool_call" and event.name:
                if event.call_id is not None:
                    if event.call_id in self._seen_tools:
                        continue
                    self._seen_tools.add(event.call_id)
                self.message(f"tool: {event.name}")


def _codex_events(record: dict[str, typing.Any]) -> list[SessionEvent]:
    """execのitemイベントをlogsと同じ表示型へ変換する。reasoningとtool結果は表示しない。"""
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
