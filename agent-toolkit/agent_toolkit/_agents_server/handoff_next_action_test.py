"""結果だけからの引き継ぎ案内が、各backendの可用性判定と一致することを確かめる。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent_toolkit._agents_server import handoff_next_action, session_handoff


@pytest.mark.parametrize("engine", ["claude", "codex", "agy", "opencode", "unknown"])
@pytest.mark.parametrize("status", ["failed", "completed"])
@pytest.mark.parametrize(
    "error",
    [
        None,
        {"message": "一般的な失敗"},
        {"codexErrorInfo": "usageLimitExceeded"},
        {"apiErrorStatus": 429},
        {"apiErrorStatus": 429, "usageLimit": {"limit_type": "five_hour"}},
        {"apiErrorStatus": 503},
        {"message": json.dumps({"error": {"param": "model"}})},
    ],
)
def test_result_guidance_preserves_backend_availability_contract(engine: str, status: str, error: Any) -> None:
    """軽い結果投影とbackend本体が、可用性・利用枠・モデル拒否を同じ対象として扱う。"""
    expected = session_handoff.availability_reason(engine, status, error) is not None
    action = handoff_next_action.result_next_action(
        {"session_id": "predecessor", "engine": engine, "status": status, "error": error}
    )
    assert (action is not None) == expected
    if action is not None:
        assert '"handoff_from": "predecessor"' in action


def test_existing_import_keeps_the_same_result_guidance() -> None:
    """従来のsession_handoffからのimportも、分離した結果判定へ到達する。"""
    assert session_handoff.result_next_action is handoff_next_action.result_next_action
