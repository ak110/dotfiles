"""背景無効のCLI待機の期限と次回の結果回収を確認する。"""

import json
import os

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, shared_layout, shared_roots
from agent_toolkit._common import state_paths, wait_schedule


def test_foreground_wait_returns_at_540_seconds_and_next_wait_collects(tmp_path, monkeypatch, capsys):
    for key in ("AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_DELEGATED_SESSION", "CODEX_THREAD_ID", "FORCE_PROMPT_CACHING_5M"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "foreground-root")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "1")
    monkeypatch.setenv("CLAUDE_CODE_PROMPT_CACHE_TTL", "1h")
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(agents_wait, "is_human_terminal", lambda: False)
    results = shared_layout.results_directory("foreground-root", tmp_path)
    identity = shared_roots.resolve_status_file_identity(dict(os.environ))
    assert identity is not None
    status = results.parent / identity.file_name
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({"version": 1, "sessions": [{"session_id": "foreground-child"}]}), encoding="utf-8")
    clock = [0.0]
    sleeps = []

    def advance(seconds):
        sleeps.append(seconds)
        clock[0] = 539.5 if len(sleeps) == 1 else clock[0] + seconds

    monkeypatch.setattr(agents_wait.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(agents_wait.time, "sleep", advance)
    with pytest.raises(SystemExit) as exited:
        atk.main(["agents", "wait"])
    assert exited.value.code == 3
    assert clock[0] == 540.0 and sleeps == [1.0, 0.5]
    running = json.loads(capsys.readouterr().out)
    assert running["status"] == "running"
    assert "atk agents wait" in running["next_action"]
    assert wait_schedule.get_wait_timeout("main") == 1740.0
    results.mkdir(parents=True, exist_ok=True)
    (results / "foreground-child.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    with pytest.raises(SystemExit) as finished:
        atk.main(["agents", "wait"])
    assert finished.value.code == 0
    assert "completed" in capsys.readouterr().out
