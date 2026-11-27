from __future__ import annotations

import datetime
import pathlib
from typing import Any

import pytest

from agent_toolkit._atk.wi import add, frontmatter, layout, submit_recovery
from agent_toolkit._testing import git_repository


@pytest.fixture(name="notes")
def _notes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    notes = tmp_path / "notes"
    git_repository.init_repository(notes)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    return notes


@pytest.mark.parametrize("status", ["queued", "running", "completed"])
def test_no_recovery_for_nonfailed_submission(notes: pathlib.Path, status: str) -> None:
    submit_recovery.save(notes, {"status": status}, notes / "run.json")
    assert not list(layout.iter_entry_paths(notes))


def test_failed_recovery_waits_for_existing_questions_and_survives_lost_save_response(
    notes: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = add.add_entries(
        notes,
        messages=["# 本人固有の判断\n\n対象の利用範囲を確定する。"],
        target_repo="github.com/example/product",
        source="agent",
        now=datetime.datetime.now(datetime.UTC),
        entry_type="uwi",
        question_type="yes-no",
    )
    run: dict[str, Any] = {
        "status": "failed",
        "run_id": "a" * 32,
        "request_ids": ["request-1", "request-2"],
        "target_repo": "github.com/example/product",
        "result": {"results": [{"status": "incomplete", "files": questions}]},
    }
    original = add.add_entries

    def save_then_fail(*args: Any, **kwargs: Any) -> list[str]:
        original(*args, **kwargs)
        raise OSError("保存応答を失った")

    with monkeypatch.context() as scoped:
        scoped.setattr(add, "add_entries", save_then_fail)
        with pytest.raises(OSError, match="保存応答を失った"):
            submit_recovery.save(notes, run, notes / "run.json")
    submit_recovery.save(notes, run, notes / "run.json")
    recoveries = []
    for entry in layout.iter_entry_paths(notes):
        parsed = frontmatter.parse_frontmatter(entry.read_text(encoding="utf-8"))
        assert parsed is not None
        if parsed[0]["type"] == "awi":
            recoveries.append((entry, parsed))
    assert len(recoveries) == 1
    entry, (metadata, body) = recoveries[0]
    assert layout.entry_state_of(entry) == "inbox"
    assert metadata["depends_on"] == questions
    assert "request-1, request-2" in body
    assert "atk wi submit --show request-1" in body
    assert "atk wi submit --resume request-1" in body
