"""公開された待機圧縮の事前照会を、所有・起動区分の入力で確認する。"""

import argparse
import json
import types

import pytest

from agent_toolkit._agents_server import shared_roots, status_reader, wait_targets
from agent_toolkit._atk import run_script


@pytest.mark.parametrize(
    "case", ["delegate", "write", "explore", "shell", "foreign-owner", "completed", "not-waited", "missing-state"]
)
@pytest.mark.parametrize(
    ("command", "is_single_wait"),
    [("echo 'atk agents wait'", False), ("/usr/local/bin/atk agents wait", True)],
)
def test_wait_compaction_context_uses_recorded_ownership(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str, command: str, is_single_wait: bool
) -> None:
    identity = types.SimpleNamespace(root_session_id="root", file_name="owner.json")
    monkeypatch.setattr(shared_roots, "resolve_wait_identity", lambda *_args: identity)
    monkeypatch.setattr(wait_targets, "read_wait_targets", lambda *_args: ({"child"} if case != "not-waited" else set(), None))
    session = {
        "owner_status_file": "other.json" if case == "foreign-owner" else "owner.json",
        "status": "completed" if case == "completed" else "running",
        "launch_kind": case if case in {"write", "explore", "shell"} else "delegate",
    }
    monkeypatch.setattr(
        status_reader,
        "load_root",
        lambda *_args: status_reader.SessionSnapshot(
            sessions={"child": session}, failures=["read error"] if case == "missing-state" else []
        ),
    )
    assert (
        run_script.dispatch(
            argparse.Namespace(
                script_name="wait-compaction-context",
                script_args=["--session-id", "main", "--command", command],
            )
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["eligible"] is (is_single_wait and case in {"delegate", "write"})
    if payload["eligible"]:
        assert payload["sessions"] == [{"session_id": "child", "mode": case}]
    else:
        assert payload["reason"]
