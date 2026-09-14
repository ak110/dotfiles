"""`atk agents list/show`の共有状態診断を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit import _atk_agents, atk
from agent_toolkit._atk import config

status_file = _atk_agents.status_file


@pytest.fixture
def session_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """1件の実行中sessionを持つ共有状態を準備する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
    directory = status_file.status_directory("root-session", tmp_path)
    directory.mkdir(parents=True)
    (directory / "root.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": [
                    {
                        "session_id": "session-1",
                        "status": "running",
                        "cwd": "/worktree",
                        "prompt": "調査せよ",
                        "model_type": "execute",
                        "launch_kind": "delegate",
                        "started_at": "2026-09-13T00:00:00+00:00",
                        "updated_at": "2026-09-13T00:01:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return directory


@pytest.mark.usefixtures("session_environment")
def test_agents_list_returns_diagnostic_fields(capsys: pytest.CaptureFixture[str]) -> None:
    """listはMCP listで省いた起動条件も診断用に返す。"""
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    payload = json.loads(capsys.readouterr().out)
    session = payload["sessions"][0]
    assert session == {
        "session_id": "session-1",
        "status": "running",
        "cwd": "/worktree",
        "prompt": "調査せよ",
        "model_type": "execute",
        "launch_kind": "delegate",
        "started_at": "2026-09-13T00:00:00+00:00",
        "updated_at": "2026-09-13T00:01:00+00:00",
        "owner_status_file": "root.json",
        "result_available": False,
        "output_updated_at": None,
        "seconds_since_output": session["seconds_since_output"],
        "stalled": True,
    }
    assert isinstance(session["seconds_since_output"], int)


@pytest.mark.usefixtures("session_environment")
def test_agents_show_selects_one_session(capsys: pytest.CaptureFixture[str]) -> None:
    """showは完全識別子で指定した1件だけを返す。"""
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", "session-1"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == "session-1"
    assert payload["prompt"] == "調査せよ"


@pytest.mark.usefixtures("session_environment")
def test_agents_show_rejects_unknown_session(capsys: pytest.CaptureFixture[str]) -> None:
    """未知の識別子は非0と理由で拒否する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "show", "missing"])

    assert capsys.readouterr().err == "unknown session: missing\n"


def test_agents_list_from_terminal_merges_all_root_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会話識別子のない直接端末では有効な全rootのsessionを統合する。"""
    for key in ("CLAUDE_CODE_SESSION_ID", "AGENT_TOOLKIT_OWNER_SESSION", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
    for root_session_id, remote_session_id in (("root-a", "session-a"), ("root-b", "session-b")):
        directory = status_file.status_directory(root_session_id, tmp_path)
        directory.mkdir(parents=True)
        (directory / "root.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "sessions": [
                        {
                            "session_id": remote_session_id,
                            "status": "running",
                            "started_at": "2026-09-14T00:00:00+00:00",
                            "updated_at": "2026-09-14T00:00:00+00:00",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    payload = json.loads(capsys.readouterr().out)
    assert {session["session_id"] for session in payload["sessions"]} == {"session-a", "session-b"}
