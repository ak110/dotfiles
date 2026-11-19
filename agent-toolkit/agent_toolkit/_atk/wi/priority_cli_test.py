"""公開CLIで付けた優先区分が一覧JSONへ現れ、その出力のままで選定順の検査に使えることを確かめる。"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.atk_support import _setup_notes, _write_awi_file
from agent_toolkit._testing.wi_mutations_support import _disable_transition_git
from agent_toolkit.atk_test import _make_subprocess_fake

_SCRIPTS = pathlib.Path(__file__).resolve().parents[3] / "skills" / "process-wi" / "scripts"


def _listed(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> dict[str, dict[str, object]]:
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "list", "--jsonl", "--skip-pull"], home=tmp_path)
    assert result.value.code == 0
    return {row["filename"]: row for row in map(json.loads, capsys.readouterr().out.splitlines())}


def test_cli_priority_flows_to_list_json_and_selection_order(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """古い通常2件と新しいurgent1件の混在で、一覧JSONのままの候補から交互の順を検査し、解除後は昇順へ戻る。"""
    notes = _setup_notes(tmp_path)
    for name in ("20260901-000000-001.md", "20260902-000000-001.md", "20260909-000000-001.md"):
        _write_awi_file(notes, name)
    _disable_transition_git(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    urgent = [
        "wi", "set-priority", "20260909-000000-001.md", "urgent", "--target-repo", "github.com/example/foo",
        "--impact", "担当を起動できない", "--evidence", "起動ログ", "--release", "修正の配布",
    ]  # fmt: skip
    with pytest.raises(SystemExit) as result:
        atk.main(urgent, home=tmp_path)
    assert result.value.code in (0, None)
    capsys.readouterr()
    facts = _listed(tmp_path, capsys)
    assert facts["20260909-000000-001.md"]["priority"] == "urgent"
    reason = facts["20260909-000000-001.md"]["priority_reason"]
    assert isinstance(reason, dict) and reason["priority_release"] == "修正の配布"
    sys.path.insert(0, str(_SCRIPTS))
    try:
        import selection_contract  # pylint: disable=import-outside-toplevel,import-error
    finally:
        sys.path.remove(str(_SCRIPTS))
    check = selection_contract._check_selection_order  # pylint: disable=protected-access
    check(facts, {"20260901-000000-001.md", "20260909-000000-001.md"}, set())
    with pytest.raises(ValueError):
        check(facts, {"20260901-000000-001.md", "20260902-000000-001.md"}, set())
    with pytest.raises(SystemExit):
        atk.main(
            ["wi", "set-priority", "20260909-000000-001.md", "normal", "--target-repo", "github.com/example/foo"], home=tmp_path
        )
    capsys.readouterr()
    facts = _listed(tmp_path, capsys)
    assert facts["20260909-000000-001.md"]["priority"] == "normal"
    check(facts, {"20260901-000000-001.md", "20260902-000000-001.md"}, set())
