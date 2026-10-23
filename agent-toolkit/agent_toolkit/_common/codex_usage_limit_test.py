"""Codexの上限停止をロールアウトの構造と後続turnで判定する。"""

import json
import pathlib

import pytest

from agent_toolkit._common import codex_usage_limit


@pytest.mark.parametrize("following", [None, "task_started", "task_complete"])
@pytest.mark.parametrize("reset", [None, 1200])
def test_rollout_stop_uses_preceding_limits_and_following_turn(
    tmp_path: pathlib.Path,
    following: str | None,
    reset: int | None,
) -> None:
    """上限失敗だけが解除待ちとなり、記録欠損時は再確認間隔を使う。"""
    # Codex 0.162.1の記録形は原WI 20261014-210511-002「実現性」の観測による。
    events = [
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {"used_percent": 100, "window_minutes": 300, "resets_at": reset},
                    "secondary": {"used_percent": 99, "window_minutes": 10080, "resets_at": 9999},
                },
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "error": {
                    "codex_error_info": "usage_limit_exceeded",
                },
            },
        },
    ]
    if following:
        events.append({"type": "event_msg", "payload": {"type": following}})
    path = tmp_path / "rollout.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events) + '\n{"partial":', encoding="utf-8")
    state = codex_usage_limit.from_rollout(path)
    if following:
        assert state is None
    else:
        assert state is not None
        assert state.resets_at == reset
        assert state.limit_type == "five_hour"
        assert state.delay_seconds(1000) == (200 if reset else 300)
        assert state.delay_seconds(1300) == 300


def test_rollout_waits_until_all_exhausted_windows_reset(tmp_path: pathlib.Path) -> None:
    """同時枯渇した窓のうち、解除が遅い窓まで待つ。"""
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "rate_limits": {
                            "primary": {"used_percent": 100, "window_minutes": 300, "resets_at": 1200},
                            "secondary": {"used_percent": 100, "window_minutes": 10080, "resets_at": 1500},
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "error": {
                            "codex_error_info": "usage_limit_exceeded",
                        },
                    },
                },
            ]
        ),
        encoding="utf-8",
    )
    state = codex_usage_limit.from_rollout(path)
    assert state is not None and state.delay_seconds(1000) == 500
    assert state.limit_type == "seven_day"
