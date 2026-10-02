"""Claude CodeとCodexの起動結果から子セッションIDを取得する。"""

import json
import pathlib
import typing


def delegated_session_ids(path: pathlib.Path, engine: str) -> frozenset[str]:
    """起動ツールの結果に記録された子セッションIDを返す。

    記録を毎回読み直す。一覧の走査で変更のない記録を読み直さないための保持は、
    `session_watch.RecordSummaryIndex`が記録の実パスごとに担う。
    """
    call_ids: set[str] = set()
    children: set[str] = set()

    def add_result(value: typing.Any) -> None:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return
        if not isinstance(value, dict):
            return
        structured = value.get("structuredContent")
        if isinstance(structured, dict):
            value = structured
        for key in ("session_id", "sessionId", "threadId", "conversationId"):
            child = value.get(key)
            if isinstance(child, str) and child:
                children.add(child)
                break
        excluded = value.get("excluded_candidates")
        if isinstance(excluded, list):
            for candidate in excluded:
                child = candidate.get("session_id") if isinstance(candidate, dict) else None
                if isinstance(child, str) and child:
                    children.add(child)

    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if "agents_server" not in line and not call_ids:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if engine == "claude":
                    message = record.get("message")
                    content = message.get("content") if isinstance(message, dict) else None
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        name = block.get("name")
                        if (
                            block.get("type") == "tool_use"
                            and isinstance(name, str)
                            and (
                                name.startswith("mcp__agents_server__start")
                                or name.startswith("mcp__plugin_agent-toolkit_agents_server__start")
                            )
                        ):
                            call_id = block.get("id")
                            if isinstance(call_id, str):
                                call_ids.add(call_id)
                        if block.get("type") == "tool_result" and block.get("tool_use_id") in call_ids:
                            add_result(record.get("toolUseResult"))
                            mcp_meta = record.get("mcpMeta")
                            if isinstance(mcp_meta, dict):
                                add_result(mcp_meta.get("structuredContent"))
                            call_ids.discard(block.get("tool_use_id"))
                else:
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    item = payload.get("item")
                    if (
                        record.get("type") == "event_msg"
                        and payload.get("type") == "item_completed"
                        and isinstance(item, dict)
                        and item.get("type") == "McpToolCall"
                        and item.get("server") == "agents_server"
                        and isinstance(item.get("tool"), str)
                        and item["tool"].startswith("start")
                    ):
                        add_result(item.get("result"))
                    name = payload.get("name")
                    if (
                        payload.get("type") in {"custom_tool_call", "function_call"}
                        and isinstance(name, str)
                        and (
                            name.startswith("mcp__agents_server__start")
                            or name.startswith("mcp__plugin_agent-toolkit_agents_server__start")
                        )
                    ):
                        call_id = payload.get("call_id")
                        if isinstance(call_id, str):
                            call_ids.add(call_id)
                    if (
                        payload.get("type") in {"custom_tool_call_output", "function_call_output"}
                        and payload.get("call_id") in call_ids
                    ):
                        add_result(payload.get("output"))
                        call_ids.discard(payload.get("call_id"))
    except OSError:
        return frozenset()
    return frozenset(children)
