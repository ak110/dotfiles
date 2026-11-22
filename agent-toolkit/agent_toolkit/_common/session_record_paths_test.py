"""Codexの記録を作成日の日付ディレクトリから探すことと、多数の識別子を続けて探す`RecordLocator`が`find_session_record`と同じ記録を返すことを検証する。"""

import datetime
import os
import pathlib

import pytest

from agent_toolkit._common import session_record_paths


def _uuid7(moment: datetime.datetime) -> str:
    stamp = f"{int(moment.timestamp() * 1000):012x}"
    return f"{stamp[:8]}-{stamp[8:]}-7000-8000-000000000000"


_CLAUDE_ID = "11111111-2222-3333-4444-555555555555"
_CODEX_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_CODEX_V7_ID = _uuid7(datetime.datetime(2026, 11, 7, 12, tzinfo=datetime.UTC))
_CODEX_V7_DAYS = ("2026/11/06", "2026/11/07", "2026/11/08")


def _touch(path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "session_id",
    [
        _CLAUDE_ID,
        "22222222-2222-3333-4444-555555555555",
        _CODEX_ID,
        _CODEX_V7_ID,
        _uuid7(datetime.datetime(2026, 11, 9, tzinfo=datetime.UTC)),
        "agent-abc123",
        "missing-id",
        "bad/id",
        "late-created",
    ],
)
def test_locator_matches_find_session_record(tmp_path: pathlib.Path, session_id: str) -> None:
    """Claude Codeの複数project、Codex、サブエージェント、未作成と一覧の後に作られた記録で同じ結果を返す。"""
    claude_home, codex_home = tmp_path / "claude", tmp_path / "codex"
    _touch(claude_home / f"projects/b-project/{_CLAUDE_ID}.jsonl")
    _touch(claude_home / f"projects/a-project/{_CLAUDE_ID}.jsonl")
    _touch(claude_home / "projects/a-project/22222222-2222-3333-4444-555555555555.jsonl")
    _touch(claude_home / f"projects/a-project/{_CLAUDE_ID}/subagents/agent-abc123.jsonl")
    _touch(codex_home / f"sessions/2026/11/07/rollout-2026-11-07T00-00-00-{_CODEX_ID}.jsonl")
    _touch(codex_home / f"sessions/2026/11/06/rollout-2026-11-06T00-00-00-{_CODEX_ID}.jsonl")
    _touch(codex_home / f"sessions/2026/11/07/rollout-2026-11-07T12-00-00-{_CODEX_V7_ID}.jsonl")
    (claude_home / "projects/not-a-directory").write_text("", encoding="utf-8")
    homes = {"claude_home": claude_home, "codex_home": codex_home, "state_root": tmp_path / "state"}
    locator = session_record_paths.RecordLocator(**homes)
    assert locator.find(_CLAUDE_ID) is not None
    _touch(claude_home / "projects/a-project/late-created.jsonl")
    assert locator.find(session_id) == session_record_paths.find_session_record(session_id, **homes)


def test_locator_finds_antigravity_record_without_rescanning_claude_and_codex(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一覧に無いAntigravityの委譲先は、Claude CodeとCodexの保存先を走査し直さずに同じ結果を返す。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    agy_id = "33333333-2222-3333-4444-555555555555"
    _touch(tmp_path / f"home/.gemini/antigravity-cli/brain/{agy_id}/.system_generated/logs/transcript_full.jsonl")
    homes = {"claude_home": tmp_path / "claude", "codex_home": tmp_path / "codex", "state_root": tmp_path / "state"}
    _touch(homes["claude_home"] / f"projects/a-project/{_CLAUDE_ID}.jsonl")
    expected = session_record_paths.find_session_record(agy_id, **homes)
    assert expected is not None and expected.engine == "agy"
    locator = session_record_paths.RecordLocator(**homes)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("一覧に無い識別子でClaude CodeとCodexの保存先を走査し直した")

    monkeypatch.setattr(session_record_paths, "find_session_record", unexpected)
    assert locator.find(agy_id) == expected


def _listed_directories(monkeypatch: pytest.MonkeyPatch, root: pathlib.Path) -> list[pathlib.Path]:
    listed: list[pathlib.Path] = []
    scandir = os.scandir

    def tracking_scandir(path: str | int = ".") -> object:
        if not isinstance(path, int) and pathlib.Path(path).is_relative_to(root):
            listed.append(pathlib.Path(path))
        return scandir(path)

    monkeypatch.setattr(os, "scandir", tracking_scandir)
    return listed


def _touch_other_days(sessions: pathlib.Path) -> None:
    for day in range(1, 31):
        _touch(sessions / f"2026/10/{day:02d}/rollout-2026-10-{day:02d}T00-00-00-{_CODEX_ID}.jsonl")


@pytest.mark.parametrize("day", _CODEX_V7_DAYS)
def test_codex_records_list_only_thread_day_directories(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, day: str
) -> None:
    sessions = tmp_path / "sessions"
    _touch_other_days(sessions)
    expected = _touch(sessions / day / f"rollout-2026-11-07T12-00-00-{_CODEX_V7_ID}.jsonl")
    listed = _listed_directories(monkeypatch, sessions)
    assert session_record_paths.find_codex_records(_CODEX_V7_ID, codex_home=tmp_path) == (expected,)
    assert expected.parent in listed
    assert set(listed) <= {sessions / window for window in _CODEX_V7_DAYS}


def test_codex_records_outside_thread_days_are_found_from_all_days(tmp_path: pathlib.Path) -> None:
    sessions = tmp_path / "sessions"
    _touch_other_days(sessions)
    expected = _touch(sessions / f"2026/09/30/rollout-2026-09-30T00-00-00-{_CODEX_V7_ID}.jsonl")
    assert session_record_paths.find_codex_records(_CODEX_V7_ID, codex_home=tmp_path) == (expected,)


def test_locator_resolves_uuid7_codex_thread_from_thread_day_directories(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UUIDv7のCodexのthread IDは、Codexの全期間の一覧を読まずに`find_session_record`と同じ記録を返す。"""
    homes = {"claude_home": tmp_path / "claude", "codex_home": tmp_path / "codex", "state_root": tmp_path / "state"}
    sessions = homes["codex_home"] / "sessions"
    _touch_other_days(sessions)
    _touch(sessions / f"2026/11/07/rollout-2026-11-07T12-00-00-{_CODEX_V7_ID}.jsonl")
    expected = session_record_paths.find_session_record(_CODEX_V7_ID, **homes)
    assert expected is not None and expected.engine == "codex"
    listed = _listed_directories(monkeypatch, sessions)
    assert session_record_paths.RecordLocator(**homes).find(_CODEX_V7_ID) == expected
    assert sessions / "2026/11/07" in listed
    assert set(listed) <= {sessions / window for window in _CODEX_V7_DAYS}
