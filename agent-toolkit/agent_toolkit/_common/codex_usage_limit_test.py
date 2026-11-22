"""Codexの上限停止をロールアウトの構造と後続turnで判定する。"""

import datetime
import json
import os
import pathlib
from typing import Any

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


def test_rollout_skips_line_cut_inside_multibyte_character(tmp_path: pathlib.Path) -> None:
    # 2026年10月22日・23日のCodexの実記録に、マルチバイト文字の途中で終わる行があった。
    cut = json.dumps({"type": "event_msg", "payload": {"message": "時間上限"}}, ensure_ascii=False).encode("utf-8")
    events = [
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {"primary": {"used_percent": 100, "window_minutes": 300, "resets_at": 1200}},
            },
        },
        {"type": "event_msg", "payload": {"type": "task_complete", "error": {"codex_error_info": "usage_limit_exceeded"}}},
    ]
    path = tmp_path / "rollout.jsonl"
    path.write_bytes(cut[: cut.index("限".encode()) + 1] + b"\n" + "\n".join(json.dumps(event) for event in events).encode())
    assert codex_usage_limit.from_rollout(path) == codex_usage_limit.UsageLimitState(1200, "five_hour")


def _write_rollout(path: pathlib.Path, events: list[dict[str, Any]], *, mtime: float | None = None) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.mark.parametrize("stale_count", [1, 8])
def test_session_rollout_opens_only_records_written_since_start(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, stale_count: int
) -> None:
    started_at = datetime.datetime(2026, 11, 19, 12, tzinfo=datetime.UTC).timestamp()
    cwd = tmp_path / "work"
    sessions = tmp_path / "sessions"
    meta = {
        "type": "session_meta",
        "payload": {"cwd": str(cwd), "originator": "codex-tui", "timestamp": "2026-11-01T00:00:00+00:00"},
    }
    for index in range(stale_count):
        _write_rollout(sessions / f"2026/11/01/rollout-stale-{index}.jsonl", [meta], mtime=started_at - 3600)
    resumed = _write_rollout(
        sessions / "2026/11/01/rollout-resumed.jsonl",
        [meta, {"type": "event_msg", "timestamp": "2026-11-19T12:00:05+00:00", "payload": {"type": "task_started"}}],
        mtime=started_at + 5,
    )
    opened: list[pathlib.Path] = []
    path_open = pathlib.Path.open

    def tracking_open(self: pathlib.Path, *args: Any, **kwargs: Any) -> Any:
        if self.is_relative_to(sessions):
            opened.append(self)
        return path_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", tracking_open)
    assert codex_usage_limit.find_session_rollout(cwd, started_at, codex_home=tmp_path) == resumed
    assert opened == [resumed]


@pytest.mark.parametrize(
    ("thread_id", "day"),
    [
        ("01a1b5e1-3e00-7000-8000-000000000000", "2026/11/07"),
        ("01a1b5e1-3e00-7000-8000-000000000000", "2026/09/30"),
        ("55555555-5555-4555-8555-555555555555", "2026/09/30"),
    ],
)
def test_usage_limit_is_read_from_thread_record(tmp_path: pathlib.Path, thread_id: str, day: str) -> None:
    _write_rollout(tmp_path / "sessions/2026/11/07/rollout-2026-11-07T12-00-00-other.jsonl", [])
    _write_rollout(
        tmp_path / f"sessions/{day}/rollout-2026-11-07T12-00-00-{thread_id}.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {"primary": {"used_percent": 100, "window_minutes": 300, "resets_at": 1200}},
                },
            },
            {"type": "event_msg", "payload": {"type": "task_complete", "error": {"codex_error_info": "usage_limit_exceeded"}}},
        ],
    )
    assert codex_usage_limit.from_thread(thread_id, codex_home=tmp_path) == codex_usage_limit.UsageLimitState(1200, "five_hour")
