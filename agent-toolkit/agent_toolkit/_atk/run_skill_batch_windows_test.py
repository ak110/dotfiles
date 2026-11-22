import os
import sys
import time

import pytest

from agent_toolkit._atk import run_skill_batch
from agent_toolkit._common import session_activity, session_review, windows_job

pytestmark = [pytest.mark.windows, pytest.mark.skipif(sys.platform != "win32", reason="Windowsの実プロセスで検証する")]

_HOOK = """
import os
from pathlib import Path

from agent_toolkit._atk import run_skill_batch
from agent_toolkit._common import session_activity, session_review

session_activity.host_process = object
run_skill_batch.meta_auto_start_due = lambda **kwargs: False
root = Path(os.environ["REVIEW_TEST_ROOT"])
if os.environ["REVIEW_TEST_OUTCOME"] == "accepted":
    transcript = root / "target.jsonl"
    transcript.write_text('{"type":"user","message":{"role":"user","content":"test"}}\\n', encoding="utf-8")
    session_review.record(
        {"hook_event_name": "SessionStart", "session_id": "target", "transcript_path": str(transcript), "cwd": str(root)},
        host="claude",
        target_repo="https://example.com/team/repo",
    )
else:
    session_review.pending = lambda: [{"host": "claude"}]
run_skill_batch.hook_request({"hook_event_name": "SessionStart"}, host="claude")
"""

_CHILD_SETUP = """
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time


def record_console(root, stage):
    kernel = ctypes.windll.kernel32
    kernel.GetConsoleWindow.restype = ctypes.c_void_p
    window = kernel.GetConsoleWindow()
    visible = bool(window and ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(window)))
    (root / f"console-{stage}").write_text(str(visible), encoding="ascii")


if os.environ.get("AGENT_TOOLKIT_SESSION_REVIEW_LAUNCH"):
    root = Path(os.environ["REVIEW_TEST_ROOT"])
    if sys.orig_argv[1] == "-c":
        record_console(root, "relay")
        deadline = time.monotonic() + 30
        while not (root / "parent-exited").exists():
            if time.monotonic() >= deadline:
                os._exit(90)
            time.sleep(0.02)
    elif sys.orig_argv[1:3] == ["-m", "agent_toolkit._atk.session_review_launch"]:
        record_console(root, "launcher")
        if os.environ["REVIEW_TEST_VISIBLE_CONSOLE"] == "1":
            original_run = subprocess.run

            def run_with_visible_console(*args, **kwargs):
                kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
                return original_run(*args, **kwargs)

            subprocess.run = run_with_visible_console
    elif Path(sys.orig_argv[1]).name == "atk.py":
        record_console(root, "atk")
        if os.environ["REVIEW_TEST_OUTCOME"] == "failure":
            os._exit(17)
        from agent_toolkit._atk import run_skill_batch
        from agent_toolkit._common import session_activity

        session_activity.host_processes = lambda: ()
        run_skill_batch._prepare = lambda target: None
"""


@pytest.mark.parametrize(
    ("outcome", "visible_console"),
    [
        ("settled", False),
        ("accepted", False),
        ("failure", False),
        pytest.param("accepted", True, id="visible-console-control"),
    ],
)
def test_session_start_survives_hook_process_exit(tmp_path, monkeypatch, outcome, visible_console):
    (tmp_path / "sitecustomize.py").write_text(_CHILD_SETUP, encoding="utf-8")
    monkeypatch.setenv("REVIEW_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("REVIEW_TEST_OUTCOME", outcome)
    monkeypatch.setenv("REVIEW_TEST_VISIBLE_CONSOLE", "1" if visible_console else "0")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(run_skill_batch.PLUGIN_ROOT), str(tmp_path))))
    monkeypatch.delenv(session_review.LAUNCH_ENV, raising=False)
    with windows_job.WindowsJob() as job:
        parent = windows_job.start_owned_process([sys.executable, "-c", _HOOK], job=job, cwd=tmp_path)
        try:
            assert parent.wait(timeout=30) == 0
        finally:
            if parent.poll() is None:
                job.terminate()
                parent.wait(timeout=5)
    [launch] = (session_review.directory() / "launches").glob("*.json")
    initial = session_review.read(launch)
    (tmp_path / "parent-exited").touch()

    process = session_review.LaunchAttempt.model_validate(initial).process
    assert process is not None, launch.with_suffix(".log").read_text(encoding="utf-8")
    deadline = time.monotonic() + 30
    while process.exists():
        assert time.monotonic() < deadline, f"背景起動が終了しない: {launch}"
        time.sleep(0.02)

    assert initial["status"] == "starting"
    result = session_review.read(launch)
    assert (tmp_path / "console-relay").read_text(encoding="ascii") == "False"
    assert (tmp_path / "console-launcher").read_text(encoding="ascii") == "False"
    assert (tmp_path / "console-atk").read_text(encoding="ascii") == str(visible_console)
    if outcome != "failure":
        assert result["status"] == outcome, launch.with_suffix(".log").read_text(encoding="utf-8")
        if outcome == "accepted":
            assert result["manifest_path"]
        return

    assert result["status"] == "starting"
    assert result["exit_code"] == 17
    assert result["exited"] is True
    assert launch.with_suffix(".log").read_bytes() == b""
    monkeypatch.setattr(session_activity, "host_process", lambda: None)
    payload = {"hook_event_name": "SessionStart"}
    notice = run_skill_batch.hook_request(payload, host="claude")
    assert notice is not None and "終了コード17" in notice
    assert result["log_path"].replace("'", "''") in notice
    assert session_review.read(launch)["status"] == "notified"
    assert run_skill_batch.hook_request(payload, host="claude") is None
