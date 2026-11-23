import datetime
import json
import pathlib
import time
import zoneinfo
from typing import Any

import pytest

from agent_toolkit._atk import research_sources, run_skill, run_skill_batch, weekly_command, weekly_schedule
from agent_toolkit._common import private_notes, session_activity, session_review

_JST = zoneinfo.ZoneInfo("Asia/Tokyo")
_REPO = "github.com/test/package"


@pytest.fixture(name="tokyo")
def _tokyo(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _at(year, month, day, hour, minute=0):
    return datetime.datetime(year, month, day, hour, minute, tzinfo=_JST).timestamp()


@pytest.fixture(name="weekly")
def _weekly(monkeypatch, tmp_path, tokyo):
    del tokyo
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_AUTO_START", "true")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_REPOS", json.dumps([_REPO]))
    monkeypatch.setattr(session_activity, "living_root_session_ids", frozenset)
    monkeypatch.setattr(run_skill_batch, "_plugin_repository", lambda: _REPO)
    monkeypatch.setattr(
        run_skill_batch.config_settings, "repository_settings", lambda **_args: {_REPO: {"path": str(tmp_path)}}
    )
    monkeypatch.setattr(run_skill_batch, "_save_failure_report", lambda *_args: True)
    state: dict[str, Any] = {"calls": [], "save": True, "code": 0}

    def execute(args):
        state["calls"].append(args.weekly_slot)
        if state["save"]:
            _save_result(args.weekly_slot)
        return state["code"]

    monkeypatch.setattr(run_skill, "_dispatch_once", execute)
    return state


def _save_result(slot):
    notes = private_notes.default_private_notes()
    path = notes / "2026" / "11" / f"20261122-060000-{len(list(notes.rglob('*.md'))) + 1:03d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: uwi\nsource: run-skill\nweekly_slot: {slot}\n---\n\n## 質問\n\n結果\n", encoding="utf-8")
    return path


def _clock(monkeypatch, value):
    monkeypatch.setattr("time.time", lambda: value)


def _due(now):
    return [work for work in weekly_schedule.WORKS if run_skill_batch.weekly_auto_start_due(work, now=now)]


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        ((2026, 11, 22, 13, 59), datetime.date(2026, 11, 15)),
        ((2026, 11, 22, 14, 0), datetime.date(2026, 11, 22)),
        ((2026, 11, 28, 23, 59), datetime.date(2026, 11, 22)),
        ((2026, 11, 29, 9, 0), datetime.date(2026, 11, 22)),
    ],
)
def test_latest_slot_starts_at_sunday_14_local_time(tokyo, moment, expected):
    del tokyo
    now = datetime.datetime.fromtimestamp(_at(*moment), datetime.UTC)
    assert weekly_schedule.latest_slot(now) == expected


def test_before_sunday_14_does_not_start_new_slot_when_previous_completed(weekly, monkeypatch):
    _save_result("meta@2026-11-15")
    _save_result("research@2026-11-15")
    now = _at(2026, 11, 22, 13, 59)
    _clock(monkeypatch, now)
    assert not _due(now)
    assert weekly_command.run_due() == 0
    assert weekly["calls"] == []


def test_before_sunday_14_catches_up_only_previous_incomplete_slot(weekly, monkeypatch):
    _save_result("meta@2026-11-15")
    now = _at(2026, 11, 22, 13, 0)
    _clock(monkeypatch, now)
    assert _due(now) == ["research"]
    assert weekly_command.run_due() == 0
    assert weekly["calls"] == ["research@2026-11-15"]
    assert not _due(now)


def test_after_sunday_14_starts_each_work_once(weekly, monkeypatch):
    now = _at(2026, 11, 22, 14, 5)
    _clock(monkeypatch, now)
    assert _due(now) == ["meta", "research"]
    assert weekly_command.run_due() == 0
    assert weekly_command.run_due() == 0
    assert weekly["calls"] == ["meta@2026-11-22", "research@2026-11-22"]


def test_failed_save_is_retried_by_next_judgment(weekly, monkeypatch):
    now = _at(2026, 11, 22, 14, 5)
    _clock(monkeypatch, now)
    weekly["save"] = False
    weekly["code"] = 1
    weekly_command.run_due()
    assert weekly["calls"] == ["meta@2026-11-22", "research@2026-11-22"]
    weekly["save"] = True
    weekly["code"] = 0
    record = weekly_schedule.read_record("meta")
    record.pop("failure_key")
    weekly_schedule.write_record("meta", record)
    later = now + weekly_schedule.RESEARCH_RETRY_SECONDS
    _clock(monkeypatch, later)
    assert _due(later) == ["meta", "research"]
    weekly_command.run_due()
    assert weekly["calls"][2:] == ["meta@2026-11-22", "research@2026-11-22"]
    assert not _due(later)


def test_interrupted_before_save_is_retried(weekly, monkeypatch):
    del weekly
    now = _at(2026, 11, 22, 15)
    _clock(monkeypatch, now)
    for work in weekly_schedule.WORKS:
        weekly_schedule.write_record(work, {"slot": f"{work}@2026-11-22", "started_at": "2026-11-22T06:00:00+00:00"})
    assert _due(now) == ["meta", "research"]


def test_stopped_right_after_save_does_not_rerun_slot(weekly, monkeypatch):
    now = _at(2026, 11, 22, 15)
    _clock(monkeypatch, now)
    for work in weekly_schedule.WORKS:
        weekly_schedule.write_record(work, {"slot": f"{work}@2026-11-22", "started_at": "2026-11-22T06:00:00+00:00"})
        _save_result(f"{work}@2026-11-22")
    research_sources.pending_path().parent.mkdir(parents=True, exist_ok=True)
    session_review.write(
        research_sources.pending_path(),
        {"slot": "research@2026-11-22", "positions": {"changelog:codex": "2026-11-21T00:00:00+00:00"}, "user_ids": {}},
    )
    assert not _due(now)
    assert weekly_command.run_due() == 0
    assert weekly["calls"] == []
    assert research_sources.load_state()["positions"] == {"changelog:codex": "2026-11-21T00:00:00+00:00"}
    assert not research_sources.pending_path().exists()


def test_missed_slots_run_only_latest_once(weekly, monkeypatch):
    _save_result("meta@2026-11-01")
    _save_result("research@2026-11-01")
    now = _at(2026, 11, 24, 9)
    _clock(monkeypatch, now)
    weekly_command.run_due()
    weekly_command.run_due()
    assert weekly["calls"] == ["meta@2026-11-22", "research@2026-11-22"]


@pytest.mark.parametrize(
    ("setting", "value", "expected"),
    [
        ("WEEKLY_META_AUTO_START", "false", ["research"]),
        ("SESSION_REVIEW_AUTO_START", "false", ["research"]),
        ("WEEKLY_RESEARCH_AUTO_START", "false", ["meta"]),
    ],
)
def test_disabled_work_does_not_start(weekly, monkeypatch, setting, value, expected):
    del weekly
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{setting}", value)
    now = _at(2026, 11, 22, 15)
    assert _due(now) == expected


def test_research_is_disabled_by_default(tokyo, monkeypatch):
    del tokyo
    monkeypatch.setattr(session_activity, "living_root_session_ids", frozenset)
    now = _at(2026, 11, 22, 15)
    assert _due(now) == ["meta"]


def test_research_failure_is_reported_once_after_consecutive_failures(weekly, monkeypatch):
    reports = []

    def save(*_args, **kwargs):
        reports.append(kwargs)
        return ["r.md"]

    monkeypatch.setattr(run_skill_batch.add, "add_entries", save)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_META_AUTO_START", "false")
    weekly["save"] = False
    weekly["code"] = 1
    now = _at(2026, 11, 22, 15)
    for attempt in range(3):
        _clock(monkeypatch, now + attempt * weekly_schedule.RESEARCH_RETRY_SECONDS)
        weekly_command.run_due()
    assert weekly["calls"] == ["research@2026-11-22"] * 3
    assert len(reports) == 1
    assert reports[0]["scope"] == session_review.RESEARCH_SCOPE
    assert "run_skill_report: true" in reports[0]["messages"][0]
    assert weekly_schedule.read_record("research")["failures"] == 3


def test_legacy_meta_record_is_migrated_without_interval_judgment(weekly, monkeypatch):
    del weekly
    legacy = session_review.directory() / weekly_schedule.LEGACY_META_RECORD
    legacy.parent.mkdir(parents=True, exist_ok=True)
    session_review.write(legacy, {"started_at": "2026-11-22T05:30:00+00:00"})
    now = _at(2026, 11, 22, 15)
    _clock(monkeypatch, now)
    assert "meta" in _due(now)
    assert not legacy.exists()
    assert weekly_schedule.read_record("meta") == {"started_at": "2026-11-22T05:30:00+00:00"}


def test_interval_judgment_is_removed_from_launch_paths():
    root = pathlib.Path(run_skill_batch.__file__).resolve().parents[1]
    for path in (root / "_atk" / "run_skill_batch.py", root / "_common" / "session_review.py"):
        text = path.read_text(encoding="utf-8")
        assert "meta_start_due" not in text
        assert "meta_auto_start_due" not in text
        assert "meta-start.json" not in text
    assert not hasattr(session_review, "meta_start_due")
    assert not hasattr(run_skill_batch, "meta_auto_start_due")
