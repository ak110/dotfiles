from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.atk_support import _setup_notes, _write_awi_file
from agent_toolkit._testing.wi_mutations_support import _disable_transition_git
from agent_toolkit.atk_test import _make_subprocess_fake


def _listed(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> dict[str, dict[str, object]]:
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "list", "--jsonl", "--skip-pull"], home=tmp_path)
    assert result.value.code == 0
    return {row["filename"]: row for row in map(json.loads, capsys.readouterr().out.splitlines())}


def test_cli_priority_changes_are_visible_in_list_json(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = _setup_notes(tmp_path)
    for name in ("20260901-000000-001.md", "20260902-000000-001.md", "20260909-000000-001.md"):
        _write_awi_file(notes, name)
    _disable_transition_git(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    urgent = [
        "wi", "set-priority", "20260909-000000-001.md", "urgent", "--target-repo", "github.com/example/foo",
        "--reason", "担当を起動できない。証拠は起動ログ。修正の配布後に解除する。",
    ]  # fmt: skip
    with pytest.raises(SystemExit) as result:
        atk.main(urgent, home=tmp_path)
    assert result.value.code in (0, None)
    capsys.readouterr()
    facts = _listed(tmp_path, capsys)
    assert facts["20260909-000000-001.md"]["priority"] == "urgent"
    reason = facts["20260909-000000-001.md"]["priority_reason"]
    assert reason == "担当を起動できない。証拠は起動ログ。修正の配布後に解除する。"
    with pytest.raises(SystemExit):
        atk.main(
            ["wi", "set-priority", "20260909-000000-001.md", "normal", "--target-repo", "github.com/example/foo"], home=tmp_path
        )
    capsys.readouterr()
    facts = _listed(tmp_path, capsys)
    assert facts["20260909-000000-001.md"]["priority"] == "normal"
