import json
import os
import pathlib
import subprocess
import sys

import pytest
from agent_toolkit._testing.git_repository import commit_all, init_repository

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts/prepare_verification_tools.py"
_MISE = r"""
import json, os, sys, tomllib
from pathlib import Path

mode, *args = sys.argv[1:]
env = os.environ
locked = [env.get(name) for name in ("MISE_LOCKED", "MISE_LOCKED_SCOPES", "MISE_AUTO_INSTALL")]
assert locked == ["1", "project", "false"]
versions = tomllib.loads(Path("mise.lock").read_text())["tools"]
store = Path(env["FIXTURE_STORE"])
with Path(env["FIXTURE_EVENTS"]).open("a") as log:
    print(json.dumps([mode, args, versions["needed"], str(Path.cwd())]), file=log)
if mode == "install":
    if env.get("FIXTURE_MODE") == "install-failure":
        sys.stderr.write("通信できない\n")
        sys.exit(23)
    assert args == ["needed"]
    for tool in args:
        directory = store / tool / versions[tool] / "bin"
        directory.mkdir(parents=True)
        executable = directory / tool
        executable.write_text(f"#!/bin/sh\nprintf '%s\\n' {versions[tool]}\n")
        executable.chmod(0o755)
    sys.exit(0)
tool = args[-1]
entry = store / tool / versions[tool]
executable = entry / "bin" / tool
if mode == "which":
    if executable.is_file() and env.get("FIXTURE_MODE") != "which-failure":
        print(executable)
    else:
        sys.stderr.write("見つからない\n")
        sys.exit(1)
else:
    assert args[:2] == ["--current", "--json"]
    print(json.dumps([{"active": entry.is_dir(), "installed": entry.is_dir(), "install_path": str(entry),
                       "source": {"path": str(Path.cwd() / "mise.toml")}}]))
"""
_CHECK = r"""
import json, os, subprocess, sys
from pathlib import Path

env = os.environ
assert env["MISE_LOCKED"] == "1"
assert env["MISE_AUTO_INSTALL"] == "false"
repo = Path.cwd()
assert str(repo) == env["FIXTURE_REPO"]
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
assert head == env["FIXTURE_HEAD"]
version = subprocess.check_output(["needed", "--version"], text=True).strip()
assert version == "2"
with Path(env["FIXTURE_EVENTS"]).open("a") as log:
    print(json.dumps(["verify", [version, head]]), file=log)
print(json.dumps({"kind": "header", "run_id": "fixture-run"}))
"""


def _program(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(os.name != "posix", reason="miseの代替実行ファイルにPOSIXのshebangを使う")
@pytest.mark.parametrize("case", ["missing", "ready", "install-failure", "which-failure", "launch-failure", "no-tools"])
def test_prepare_uses_only_needed_tool_after_lock_change(tmp_path: pathlib.Path, case: str) -> None:
    repo = init_repository(
        tmp_path / "candidate",
        files={"mise.lock": '[tools]\nneeded = "1"\nunrelated = "1"\n', "mise.toml": "[tools]\n"},
        commit_message="統合前",
    )
    (repo / "mise.lock").write_text('[tools]\nneeded = "2"\nunrelated = "2"\n', encoding="utf-8")
    head = commit_all(repo, "新しい統合先へ追随")
    bin_dir = tmp_path / "bin"
    store = tmp_path / "store"
    events_file = tmp_path / "events.jsonl"
    _program(bin_dir / "mise", f"#!{sys.executable}\n" + _MISE.lstrip())
    _program(bin_dir / "needed", "#!/bin/sh\nprintf '1\\n'\n")
    _program(store / "needed/1/bin/needed", "#!/bin/sh\nprintf '1\\n'\n")
    if case in {"ready", "which-failure", "launch-failure"}:
        code = "exit 24" if case == "launch-failure" else "printf '2\\n'"
        _program(store / "needed/2/bin/needed", f"#!/bin/sh\n{code}\n")
    child = tmp_path / "verify.py"
    child.write_text(_CHECK, encoding="utf-8")
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "FIXTURE_STORE": str(store),
        "FIXTURE_EVENTS": str(events_file),
        "FIXTURE_MODE": case,
        "FIXTURE_REPO": str(repo),
        "FIXTURE_HEAD": head,
    }
    options = ["--tool", "needed", "needed"] if case != "no-tools" else []
    verification = [str(child)] if options else ["-c", "print('no mise required')"]
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), *options, "--", sys.executable, *verification],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if case == "no-tools":
        assert result.returncode == 0 and result.stdout.strip() == "no mise required"
        assert not events_file.exists()
        return

    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    assert all(row[2:] == ["2", str(repo)] for row in events if row[0] != "verify")
    installs = [row[1] for row in events if row[0] == "install"]
    assert installs == ([["needed"]] if case in {"missing", "install-failure"} else [])
    assert not (store / "unrelated").exists()
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True) == ""
    if case in {"missing", "ready"}:
        assert result.returncode == 0, result.stderr
        assert events[-1] == ["verify", ["2", head]]
        assert json.loads(result.stdout)["run_id"] == "fixture-run"
        assert sum(row[0] == "which" for row in events) == (2 if installs else 1)
    else:
        assert result.returncode != 0 and not result.stdout
        assert str(repo) in result.stderr and "needed" in result.stderr
        assert all(row[0] != "verify" for row in events)
        expected = {"install-failure": "通信できない", "which-failure": "導入済み", "launch-failure": "起動を確認できない"}
        assert expected[case] in result.stderr
