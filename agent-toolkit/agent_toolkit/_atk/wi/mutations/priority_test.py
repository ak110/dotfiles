from __future__ import annotations

import pathlib

import pytest

from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._atk.wi import priority as wi_priority
from agent_toolkit._atk.wi.mutations import priority as mutation_priority
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._testing.atk_support import _setup_notes, _write_awi_file
from agent_toolkit._testing.wi_mutations_support import _disable_transition_git

_REASON = "担当を起動できない。証拠は起動ログ。修正の配布後に解除する。"
_NAME = "20260901-000000-001.md"


def test_urgent_is_recorded_with_reason_and_cleared(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, _NAME)
    _disable_transition_git(monkeypatch)
    assert mutation_priority.set_entry_priority(notes, filename=_NAME, urgent=True, reason=_REASON)
    metadata, body = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8")) or ({}, "")
    assert wi_priority.priority_of(metadata) == "urgent" and wi_priority.reason_of(metadata, body) == _REASON
    assert metadata["state"] == "inbox"
    assert mutation_priority.set_entry_priority(notes, filename=_NAME, urgent=False)
    metadata, body = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8")) or ({}, "")
    assert wi_priority.priority_of(metadata) == "normal"
    assert _REASON in body
    assert not set(metadata) & {"priority", *wi_priority.LEGACY_REASON_KEYS}


@pytest.mark.parametrize("reason", [None, "  ", "## 別の節"])
def test_urgent_without_valid_reason_is_rejected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, reason: str | None
) -> None:
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, _NAME)
    before = path.read_text(encoding="utf-8")
    _disable_transition_git(monkeypatch)
    with pytest.raises(WebInputError):
        mutation_priority.set_entry_priority(notes, filename=_NAME, urgent=True, reason=reason)
    assert path.read_text(encoding="utf-8") == before
