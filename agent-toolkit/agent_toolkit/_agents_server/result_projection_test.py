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


def test_progress_excerpt_normalizes_newline_and_keeps_tail() -> None:
    """進捗本文は改行を除き、長文では末尾80文字だけを返す。"""
    assert result_projection.progress_excerpt("a\r\nb\rc\nd") == "a b c d"
    value = result_projection.progress_excerpt("x" * 100)
    assert value == "…" + "x" * 80


def test_activity_projection_decides_stall_by_activity_time() -> None:
    """停滞の印は活動時刻からの経過だけで決め、テキスト出力の停止では付けない。"""
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
