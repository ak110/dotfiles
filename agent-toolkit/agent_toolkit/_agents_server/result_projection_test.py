"""`_agents_server/result_projection.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pytest

from agent_toolkit._agents_server import (
    result_projection,
    state,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def test_review_wait_statement_does_not_request_review_receipt() -> None:
    """レビュー担当の待機表明を、指摘を受領した完了結果として案内しない。"""
    held = {
        "status": "completed",
        "agent_message": "待機中: child",
        "error": {"heldResultFinalized": True},
    }
    waiting = result_projection.with_result_next_action(held, "scope-review")
    assert "`show`" in waiting["next_action"]
    assert "send_message" not in waiting["next_action"]
    assert "レビューの指摘を受領した" not in waiting["next_action"]
    assert "read_requirements" not in waiting

    finished = result_projection.with_result_next_action({"status": "completed", "agent_message": "適合"}, "scope-review")
    assert "レビューの指摘を受領した" in finished["next_action"]
    assert finished["read_requirements"]


def test_unobserved_child_guidance_preserves_final_review() -> None:
    """未回収の子がいても最終本文を待機表明と取り違えず、元の所有者へ回収を案内する。"""
    result = result_projection.with_result_next_action(
        {"status": "completed", "agent_message": "適合", "error": {"unobservedSessions": ["child-1"]}},
        "scope-review",
    )
    assert result["error"]["unobservedSessions"] == ["child-1"]
    assert result["agent_message"] == "適合"
    assert "レビューの指摘を受領した" in result["next_action"]
    assert "`send_message`で元の委譲先に`atk agents wait`" in result["next_action"]
    assert result["read_requirements"]


def test_progress_excerpt_normalizes_newline_and_keeps_tail() -> None:
    assert result_projection.progress_excerpt("a\r\nb\rc\nd") == "a b c d"
    value = result_projection.progress_excerpt("x" * 100)
    assert value == "…" + "x" * 80


def test_activity_projection_decides_stall_by_activity_time() -> None:
    text_silent = result_projection.activity_projection(
        updated_at="2099-01-01T00:00:00+00:00",
        output_updated_at="2000-01-01T00:00:00+00:00",
        started_at="2000-01-01T00:00:00+00:00",
    )
    inactive = result_projection.activity_projection(
        updated_at="2000-01-01T00:00:00+00:00",
        output_updated_at="2099-01-01T00:00:00+00:00",
        started_at="2000-01-01T00:00:00+00:00",
    )
    unreadable = result_projection.activity_projection(
        updated_at=None, output_updated_at=None, started_at="2026-09-15T00:00:00"
    )

    assert {"updated_at", "output_updated_at", "seconds_since_output"}.isdisjoint(text_silent)
    assert text_silent["seconds_since_activity"] == 0
    assert text_silent["seconds_since_activity"] < state.STALL_NOTICE_SECONDS
    assert inactive["seconds_since_activity"] >= state.STALL_NOTICE_SECONDS
    assert not unreadable
