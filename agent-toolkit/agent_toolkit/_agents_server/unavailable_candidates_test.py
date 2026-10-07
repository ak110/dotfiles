"""`_agents_server/unavailable_candidates.py`の振る舞いを検証する。"""

from __future__ import annotations

import datetime
import pathlib

from agent_toolkit._agents_server import (
    shared_layout,
    unavailable_candidates,
)


def test_unavailable_candidates_are_kept_until_the_retention_period_elapses(tmp_path: pathlib.Path) -> None:
    """除外した候補を保持期間内は返し、経過後は返さない。"""
    recorded_at = datetime.datetime(2026, 9, 16, 0, 0, tzinfo=datetime.UTC)
    unavailable_candidates.record_unavailable_candidate(
        "plan",
        "delegate",
        ("claude", "opus", "high"),
        "401",
        now=recorded_at,
        state_root=tmp_path,
    )
    within = recorded_at + datetime.timedelta(seconds=unavailable_candidates.UNAVAILABLE_CANDIDATES_RETENTION_SECONDS - 1)
    after = recorded_at + datetime.timedelta(seconds=unavailable_candidates.UNAVAILABLE_CANDIDATES_RETENTION_SECONDS)

    assert unavailable_candidates.load_unavailable_candidates("plan", "delegate", now=within, state_root=tmp_path) == {
        ("claude", "opus", "high"): "401"
    }
    assert not unavailable_candidates.load_unavailable_candidates("plan", "delegate", now=after, state_root=tmp_path)
    assert not unavailable_candidates.load_unavailable_candidates("plan", "explore", now=within, state_root=tmp_path)


def test_unavailable_candidates_hold_two_or_more_engines_and_clear_individually(tmp_path: pathlib.Path) -> None:
    """同じ起動条件で2件以上の候補を保持し、成立した候補だけを取り除く。"""
    now = datetime.datetime(2026, 9, 16, 0, 0, tzinfo=datetime.UTC)
    unavailable_candidates.record_unavailable_candidate(
        "plan", "delegate", ("claude", "opus", "high"), "401", now=now, state_root=tmp_path
    )
    unavailable_candidates.record_unavailable_candidate(
        "plan", "delegate", ("codex", "terra", "medium"), "usageLimitExceeded", now=now, state_root=tmp_path
    )

    assert unavailable_candidates.load_unavailable_candidates("plan", "delegate", now=now, state_root=tmp_path) == {
        ("claude", "opus", "high"): "401",
        ("codex", "terra", "medium"): "usageLimitExceeded",
    }

    unavailable_candidates.clear_unavailable_candidate(
        "plan", "delegate", ("codex", "terra", "medium"), now=now, state_root=tmp_path
    )

    assert unavailable_candidates.load_unavailable_candidates("plan", "delegate", now=now, state_root=tmp_path) == {
        ("claude", "opus", "high"): "401"
    }


def test_clearing_an_unrecorded_candidate_creates_no_file(tmp_path: pathlib.Path) -> None:
    """記録の無い候補の解除は記録ファイルを作成しない。"""
    now = datetime.datetime(2026, 9, 16, 0, 0, tzinfo=datetime.UTC)

    unavailable_candidates.clear_unavailable_candidate(
        "plan", "delegate", ("claude", "opus", "high"), now=now, state_root=tmp_path
    )

    assert not unavailable_candidates.unavailable_candidates_path(tmp_path).exists()


def test_unavailable_candidates_record_is_a_file_and_not_a_root_session(tmp_path: pathlib.Path) -> None:
    """記録はルートsession識別子の列挙へ現れない単一ファイルとする。"""
    now = datetime.datetime(2026, 9, 16, 0, 0, tzinfo=datetime.UTC)
    unavailable_candidates.record_unavailable_candidate(
        "plan", "delegate", ("claude", "opus", "high"), "401", now=now, state_root=tmp_path
    )

    assert unavailable_candidates.unavailable_candidates_path(tmp_path).is_file()
    assert shared_layout.list_root_session_ids(tmp_path) == []
