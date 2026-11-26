import json
import os
import pathlib
import shlex
import subprocess
import sys
from collections.abc import Callable

import pytest
from agent_toolkit._atk import managed_temp
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

_SCRIPT = pathlib.Path(__file__).with_name("atk-sandbox.sh")

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="隔離ハーネスはLinuxのprocfsを使う")


@pytest.mark.parametrize("escape", ("none", "home-env", "claude-config", "xdg-data", "update-link"))
def test_claude_writes_stay_inside_sandbox(
    tmp_path: pathlib.Path, escape: str, host_environ: Callable[[], dict[str, str]]
) -> None:
    fixture = tmp_path / "sandbox"
    home = fixture / "home"
    executable = home / ".local/bin/claude"
    executable.parent.mkdir(parents=True)
    (fixture / "work").mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    foreign = tmp_path / "other-home"
    foreign.mkdir()
    (fixture / "evidence-dir").write_text(str(evidence), encoding="utf-8")
    settings = {"home-env": "HOME", "claude-config": "CLAUDE_CONFIG_DIR", "xdg-data": "XDG_DATA_HOME"}
    config = "PATH=/usr/bin:/bin\n"
    if escape in settings:
        config += f"{settings[escape]}={foreign}\n"
    if escape == "update-link":
        executable.symlink_to(foreign / "claude")
    (fixture / "sandbox.env").write_text(config, encoding="utf-8")
    command = (
        'mkdir -p -- "$HOME/.local/bin" "$CLAUDE_CONFIG_DIR" "$XDG_DATA_HOME" && '
        'touch -- "$HOME/.local/bin/claude" "$CLAUDE_CONFIG_DIR/history.jsonl" "$XDG_DATA_HOME/updated"'
    )

    result = subprocess.run(
        ["bash", str(_SCRIPT), "run", str(fixture), "updater", "/bin/sh", "-c", command],
        env=host_environ(),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert not list(foreign.iterdir())
    if escape == "none":
        assert result.returncode == 0
        assert executable.is_file()
        assert (home / ".claude/history.jsonl").is_file()
        assert (home / ".local/share/updated").is_file()
    else:
        assert result.returncode == 2
        assert "失敗: 検証領域" in (evidence / "01-updater.err").read_text(encoding="utf-8")
        assert (evidence / "01-updater.rc").read_text(encoding="utf-8").strip() == "2"
        assert not (home / ".claude/history.jsonl").exists()
        assert not (home / ".local/share/updated").exists()


@pytest.mark.parametrize(("matching_home", "exit_code"), [(True, 0), (True, 7), (False, 0)])
def test_run_reports_only_matching_home(
    tmp_path: pathlib.Path, matching_home: bool, exit_code: int, host_environ: Callable[[], dict[str, str]]
) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "work").mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (fixture / "evidence-dir").write_text(str(evidence), encoding="utf-8")
    (fixture / "sandbox.env").write_text("PATH=/usr/bin:/bin\n", encoding="utf-8")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    grep = stubs / "grep"
    grep.write_text(
        "#!/bin/bash\n"
        '[[ "$1" == -lzxsF && "$2" == -- && "$3" == "HOME=$MOCK_HOME" ]] || exit 1\n'
        "printf '/proc/9000001/environ\\n/proc/9000002/environ\\n'\n",
        encoding="utf-8",
    )
    grep.chmod(0o755)
    env = host_environ()
    env["PATH"] = str(stubs) + os.pathsep + env["PATH"]
    env["MOCK_HOME"] = str(fixture / ("home" if matching_home else "home-other"))

    result = subprocess.run(
        [
            "bash",
            str(_SCRIPT),
            "run",
            str(fixture),
            "model",
            "/bin/sh",
            "-c",
            f"printf 'model output\\n'; printf 'model error\\n' >&2; exit {exit_code}",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == exit_code
    assert (evidence / "01-model.rc").read_text(encoding="utf-8").strip() == str(exit_code)
    assert (evidence / "01-model.out").read_text(encoding="utf-8") == "model output\n"
    assert (evidence / "01-model.err").read_text(encoding="utf-8") == "model error\n"
    expected_pids = ["残存PID: 9000001", "残存PID: 9000002"] if matching_home else []
    lines = result.stderr.splitlines()
    if matching_home:
        assert lines[0].startswith("注意: 前景コマンドの終了後も隔離HOMEで動くプロセスが残っている。")
    assert lines[1 if matching_home else 0 :] == expected_pids


def test_setup_queue_commit_passes_repository_protection(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _SCRIPT.read_text(encoding="utf-8")
    assert 'git init --quiet --bare --initial-branch=main "${scratch}/notes-remote.git"' in script
    assert 'remote add origin "${scratch}/notes-remote.git"' in script
    scratch = managed_temp.temp_root_path() / f"sandbox-{tmp_path.name}"
    git.init_bare_repository(scratch / "notes-remote.git")
    notes = git.init_repository(scratch / "private-notes", initial_branch="main", origin=str(scratch / "notes-remote.git"))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", "{}")
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_path),
        "tool_input": {"command": f"git -C {shlex.quote(str(notes))} commit --quiet --allow-empty -m init"},
    }
    result = _run_hook_in_process("pretooluse", payload)
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout or "{}").get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
