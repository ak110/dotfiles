"""hook_process_loopの契約テスト。"""

import contextlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any, cast

import pytest

from agent_toolkit import atk
from agent_toolkit import hook as _hook
from agent_toolkit._atk import agents_exit_session as _agents_exit_session
from agent_toolkit._atk import config as _config
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._common import session_activity
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit.atk_test import _setup_notes


def test_native_subagent_stop_keeps_next_process_loop_session_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開hookで子のStopを処理した後も、公開CLIが次のセッションを起動する。"""
    monkeypatch.setattr(session_activity, "scan_activity", lambda **_kwargs: session_activity.ActivitySnapshot())
    notes = _setup_notes(tmp_path)
    myrepo = git_repository.init_repository(tmp_path / "myrepo", origin="https://github.com/example/myrepo.git")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(myrepo)]))
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    entries: list[pathlib.Path] = []

    def add_entry(number: int) -> None:
        path = notes / "2026/10" / f"native-stop-{number}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nstate: inbox\ntype: awi\ntarget_repo: github.com/example/myrepo\n---\n\n# 作業\n",
            encoding="utf-8",
        )
        entries.append(path)

    add_entry(1)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(_config.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setattr(shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _path: True)
    monkeypatch.setattr(_pl_watch, "wait_for_start_permission", lambda *_args: True)
    monkeypatch.setattr(_pl_session, "resolve_orchestrator_specs", lambda: [("claude", "model", "medium")])
    monkeypatch.setattr(
        _pl_session, "select_available_orchestrator", lambda *_args, **_kwargs: ("claude", "model", "medium", None, None)
    )
    monkeypatch.setattr(_wait_schedule, "get_prompt_cache_ttl", lambda _bucket: "1h")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    abort_path = tmp_path / "state" / "agent-toolkit" / "process-wi-abort"
    session_calls: list[list[str]] = []
    child_responses: list[dict[str, Any]] = []
    termination_calls: list[str] = []

    def invoke_stop(agent_id: str | None, count: int) -> dict[str, Any]:
        transcript = tmp_path / f"stop-{agent_id or 'main'}-{count}.jsonl"
        entry = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "進行中"}]}}
        transcript.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for _ in range(count)) + "\n", encoding="utf-8")
        payload: dict[str, object] = {"session_id": "loop-stop-integration", "transcript_path": str(transcript)}
        if agent_id is not None:
            payload["agent_id"] = agent_id
        output = io.StringIO()
        with monkeypatch.context() as context:
            stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8")), encoding="utf-8")
            context.setattr(sys, "stdin", stdin)
            with contextlib.redirect_stdout(output):
                assert _hook.main(["stop"]) == 0
        return cast(dict[str, Any], json.loads(output.getvalue()))

    def fake_termination(*, session_id: str) -> tuple[str, None]:
        assert session_id == "loop-stop-integration"
        termination_calls.append("requested")
        return "unsupported", None

    monkeypatch.setattr(_agents_exit_session, "request_termination", fake_termination)

    def fake_cli_session(
        cmd: list[str], _env: dict[str, str], cwd: pathlib.Path, **_kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        assert cmd[0] == "claude" and cwd == pathlib.Path.cwd()
        assert any(argument.startswith("/goal ") for argument in cmd)
        manifest = pathlib.Path(json.loads(_env["AGENT_TOOLKIT_SESSION_START_INPUT"])["manifest"])
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert len(data["repositories"]) == 1
        target = data["repositories"][0]
        assert pathlib.Path(target["worktree"]) == myrepo
        candidates = json.loads(pathlib.Path(target["candidate_file"]).read_text(encoding="utf-8"))
        assert [candidate["filename"] for candidate in candidates] == [entries[-1].name]
        if not session_calls:
            for count in range(1, 4):
                response = invoke_stop("agent-review", count)
                assert response.get("decision") != "block"
                child_responses.append(response)
            assert not abort_path.exists()
        session_calls.append(cmd)
        entry = entries[-1]
        entry.write_text(entry.read_text(encoding="utf-8").replace("state: inbox", "state: adopted"), encoding="utf-8")
        records = {target["target_repo"]: {"status": "completed", "writer_sessions": []}}
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": records}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(_pl_session, "_run_cli_session", fake_cli_session)

    def stop_wait(*_args: object, **_kwargs: object) -> bool:
        if len(session_calls) == 1:
            add_entry(2)
            return True
        raise KeyboardInterrupt

    monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)

    with pytest.raises(SystemExit) as loop_exit:
        atk.main(["wi", "process-loop", "--no-update", "--no-alerts"], home=tmp_path)

    assert loop_exit.value.code == 0
    assert len(session_calls) == 2
    identifiers = [command[command.index("--session-id") + 1] for command in session_calls]
    assert identifiers[0] != identifiers[1]
    assert len(child_responses) == 3
    assert all("systemMessage" not in response for response in child_responses)
    assert not abort_path.exists()
    assert not termination_calls

    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    parent_responses = [invoke_stop(None, count) for count in range(1, 4)]
    assert "systemMessage" in parent_responses[-1]
    assert not abort_path.exists()
    assert termination_calls == ["requested"]
