"""Claude CodeとCodexの起動結果から子セッションIDを取得する。"""

import json
import typing


class DelegationCollector:
    """記録の行を順に受け取り、起動ツールの結果に記録された子セッションIDを集める。

    記録を開いて読む処理は持たない。一覧の走査は記録1件を1回だけ開き、要約と子セッションIDを
    同じ走査で求めるため、行の読み込みは`session_watch.scan_record`が担う。
    """

    def __init__(self, engine: str) -> None:
        self.engine = engine
        self._call_ids: set[str] = set()
        self._children: set[str] = set()

    def wants(self, line: str) -> bool:
        """行をJSONとして解析する必要があるかを返す。

        `agents_server`を含む行と、起動の呼び出しを見つけて結果を待つ間の行だけが結果を変え得る。
        """
        return bool(self._call_ids) or "agents_server" in line

    @property
    def children(self) -> frozenset[str]:
        """これまでに受け取った行から求めた子セッションIDの集合。"""
        return frozenset(self._children)

    def _add_result(self, value: typing.Any) -> None:
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
                self._children.add(child)
                break
        excluded = value.get("excluded_candidates")
        if isinstance(excluded, list):
            for candidate in excluded:
                child = candidate.get("session_id") if isinstance(candidate, dict) else None
                if isinstance(child, str) and child:
                    self._children.add(child)

    def feed(self, record: typing.Any) -> None:
        """`wants`が真を返した行を解析した値を受け取る。"""
        if not isinstance(record, dict):
            return
        if self.engine == "claude":
            self._feed_claude(record)
        else:
            self._feed_codex(record)

    def _feed_claude(self, record: dict[str, typing.Any]) -> None:
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            name = block.get("name")
            if block.get("type") == "tool_use" and isinstance(name, str) and _is_start_tool(name):
                call_id = block.get("id")
                if isinstance(call_id, str):
                    self._call_ids.add(call_id)
            if block.get("type") == "tool_result" and block.get("tool_use_id") in self._call_ids:
                self._add_result(record.get("toolUseResult"))
                mcp_meta = record.get("mcpMeta")
                if isinstance(mcp_meta, dict):
                    self._add_result(mcp_meta.get("structuredContent"))
                self._call_ids.discard(block.get("tool_use_id"))

    def _feed_codex(self, record: dict[str, typing.Any]) -> None:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return
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
            self._add_result(item.get("result"))
        name = payload.get("name")
        if payload.get("type") in {"custom_tool_call", "function_call"} and isinstance(name, str) and _is_start_tool(name):
            call_id = payload.get("call_id")
            if isinstance(call_id, str):
                self._call_ids.add(call_id)
        if (
            payload.get("type") in {"custom_tool_call_output", "function_call_output"}
            and payload.get("call_id") in self._call_ids
        ):
            self._add_result(payload.get("output"))
            self._call_ids.discard(payload.get("call_id"))


def _is_start_tool(name: str) -> bool:
    return name.startswith("mcp__agents_server__start") or name.startswith("mcp__plugin_agent-toolkit_agents_server__start")
