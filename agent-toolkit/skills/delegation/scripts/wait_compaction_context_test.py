"""公開された待機圧縮の事前照会を、所有・起動区分・保存時刻の異なる入力で確認する。"""

import argparse
import json
import os
import pathlib
import types

import pytest

from agent_toolkit._agents_server import shared_roots, status_reader, wait_targets
from agent_toolkit._atk import managed_temp, run_script


@pytest.mark.parametrize(
    "case",
    [
        "delegate",
        "write",
        "explore",
        "shell",
        "foreign-owner",
        "completed",
        "not-waited",
        "missing-handoff",
        "stale-handoff",
        "empty-handoff",
        "missing-state",
        "quoted-command",
    ],
)
def test_wait_compaction_context_uses_recorded_ownership_and_current_handoff(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
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
    monkeypatch.setattr(managed_temp, "find_session_temp", lambda _session_id: tmp_path)
    handoff = tmp_path / "parent-handoff.md"
    if case != "missing-handoff":
        handoff.write_text("" if case == "empty-handoff" else "対象WI・再開先・待機対象を保存済み", encoding="utf-8")
        modified_at = 10.0 if case == "stale-handoff" else 30.0
        os.utime(handoff, (modified_at, modified_at))
    command = "echo 'atk agents wait'" if case == "quoted-command" else "/usr/local/bin/atk agents wait"
    assert (
        run_script.dispatch(
            argparse.Namespace(
                script_name="wait-compaction-context",
                script_args=["--session-id", "main", "--command", command, "--not-before", "20"],
            )
        )
        == 0
    )
    result = capsys.readouterr()
    payload = json.loads(result.out)
    assert payload["eligible"] is (case in {"delegate", "write"})
    if payload["eligible"]:
        assert payload["handoff"] == str(handoff)
        assert payload["sessions"] == [{"session_id": "child", "mode": case}]
    else:
        assert payload["reason"]
    assert not result.err
