import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).with_name("sync_release_checkout.py")


@pytest.mark.parametrize("prepare_exit", [0, 41])
def test_sync_waits_for_target_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prepare_exit: int) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(checkout), *args], text=True).strip()

    git("init", "--initial-branch=develop")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("config", "core.hooksPath", str(tmp_path / "no-hooks"))
    launcher = checkout / "agent-toolkit/scripts/launch.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("old\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "old")
    old = git("rev-parse", "HEAD")
    launcher.write_text("new\n", encoding="utf-8")
    git("commit", "-am", "new")
    new = git("rev-parse", "HEAD")
    git("reset", "--hard", old)

    run = subprocess.run
    calls = []

    def run_with_prepare(command: list[str], **kwargs: Any):
        if command[0] != "uv":
            return run(command, **kwargs)
        calls.append(command)
        assert git("rev-parse", "HEAD") == old
        assert launcher.read_text(encoding="utf-8") == "old\n"
        assert Path(command[-3]).read_text(encoding="utf-8") == "new\n"
        return run(
            [sys.executable, "-c", f"raise SystemExit({prepare_exit})"],
            check=True,
        )

    monkeypatch.setattr(subprocess, "run", run_with_prepare)
    sync = runpy.run_path(str(_SCRIPT))["sync"]
    if prepare_exit:
        with pytest.raises(subprocess.CalledProcessError, match="41"):
            sync(checkout, new, tmp_path)
        assert git("rev-parse", "HEAD") == old
    else:
        sync(checkout, new, tmp_path)
        assert git("rev-parse", "HEAD") == new
    assert len(calls) == 1
