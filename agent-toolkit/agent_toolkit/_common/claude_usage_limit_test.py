"""Claude Codeの利用上限の分類を検証する。"""

import json
from typing import Any, cast

import claude_agent_sdk
import pytest

from agent_toolkit._common import claude_usage_limit


@pytest.mark.parametrize("limit_type", ["seven_day", "seven_day_opus", "seven_day_sonnet", "five_hour"])
def test_rejected_weekly_and_five_hour_limits_are_wait_targets(limit_type: str) -> None:
    """Weekly limitの3種類と5時間の利用上限の拒否は解除まで待つ対象とする。"""
    info = claude_agent_sdk.RateLimitInfo(status="rejected", rate_limit_type=cast(Any, limit_type), resets_at=1_900_000_000)
    event = claude_agent_sdk.RateLimitEvent(rate_limit_info=info, uuid="u", session_id="s")

    state = claude_usage_limit.from_event(event)

    assert state is not None
    assert state.is_wait_target
    assert state.limit_type == limit_type
    assert state.resets_at == 1_900_000_000


@pytest.mark.parametrize(
    ("status", "limit_type"),
    [
        ("rejected", "overage"),
        ("rejected", None),
        ("allowed", "seven_day"),
        ("allowed_warning", "five_hour"),
    ],
)
def test_other_states_are_not_wait_targets(status: str, limit_type: str | None) -> None:
    """`overage`、種類の欠けた拒否、許可と警告は待機の対象外とする。"""
    state = claude_usage_limit.from_info({"status": status, "rateLimitType": limit_type})

    assert state is not None
    assert not state.is_wait_target


def test_delay_uses_reset_time_and_falls_back_to_recheck_interval() -> None:
    """解除予定時刻までの秒数を返し、時刻の欠落と経過済みでは一定の再確認間隔を返す。"""
    now = 1_000.0

    assert claude_usage_limit.UsageLimitState("rejected", "seven_day", 1_600).delay_seconds(now) == 600.0
    assert (
        claude_usage_limit.UsageLimitState("rejected", "seven_day", 900).delay_seconds(now)
        == claude_usage_limit.RECHECK_SECONDS
    )
    assert (
        claude_usage_limit.UsageLimitState("rejected", "seven_day", None).delay_seconds(now)
        == claude_usage_limit.RECHECK_SECONDS
    )


def test_stream_json_lines_yield_latest_limit_session_and_result() -> None:
    """CLIの`stream-json`出力から最後の利用枠、session識別子と`result`イベントを読み取る。"""
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "conversation"}),
        "not json",
        json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "allowed", "rateLimitType": "seven_day"},
                "session_id": "conversation",
            }
        ),
        json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "rejected", "rateLimitType": "seven_day", "resetsAt": 1_900_000_000},
                "session_id": "conversation",
            }
        ),
        json.dumps({"type": "result", "is_error": True, "result": "limit", "session_id": "conversation"}),
    ]

    state = claude_usage_limit.from_stream_lines(lines)

    assert state == claude_usage_limit.UsageLimitState("rejected", "seven_day", 1_900_000_000)
    assert state is not None
    assert state.resets_at_iso() == "2030-03-17T17:46:40+00:00"
    assert "seven_day" in state.describe()
    assert claude_usage_limit.session_id_from_stream_lines(lines) == "conversation"
    assert claude_usage_limit.result_from_stream_lines(lines) == {
        "type": "result",
        "is_error": True,
        "result": "limit",
        "session_id": "conversation",
    }
