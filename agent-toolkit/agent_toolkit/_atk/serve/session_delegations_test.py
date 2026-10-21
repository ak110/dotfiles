"""一覧の子セッション抽出で、要求の写しと実際の起動結果を分ける。"""

import json
from typing import Any

from agent_toolkit._atk.serve.session_delegations import DelegationCollector


def _call(identity: str, kind: str = "assistant") -> dict[str, Any]:
    return {
        "type": kind,
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": identity, "name": "mcp__agents_server__start", "input": {}}],
        },
    }


def _result(identity: str, child: str, kind: str = "user") -> dict[str, Any]:
    return {
        "type": kind,
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": identity, "content": "結果"}]},
        "toolUseResult": {"session_id": child},
    }


def test_request_copy_neither_starts_nor_consumes_delegation() -> None:
    """写しの結果で実呼出しを消費せず、写しだけの起動も子として数えない。"""
    collector = DelegationCollector("claude")
    for record in [
        _call("real"),
        _result("real", "copied-result", "api-request-blob"),
        _result("real", "actual-child"),
        _call("copy", "api-request-blob"),
        _result("copy", "copied-child", "api-request-blob"),
    ]:
        if collector.wants(json.dumps(record)):
            collector.feed(record)
    assert collector.children == {"actual-child"}
