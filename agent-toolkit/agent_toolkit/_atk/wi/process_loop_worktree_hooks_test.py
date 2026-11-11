# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""プロジェクトのWorktreeCreate hookによる作成と既存資源の保護を検証する。"""

import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._common import state_paths
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_worktree_support import _make_remote_repository
from agent_toolkit._testing.process_loop_worktree_support import (
    _prepare_process_loop_environment as _prepare_process_loop_environment,
)
from agent_toolkit._testing.process_loop_worktree_support import (
    _process_loop_commands_isolated as _process_loop_commands_isolated,
)


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("name", ["fixed", None])
@pytest.mark.parametrize("detached", [False, True])
def test_project_hook(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], fails: bool, name: str | None, detached: bool
) -> None:
    """公開されたworktree準備処理がWorktreeCreate hookの返したパスを再利用し、hookが失敗してもGitによる作成へ切り替えない。"""
    local = _make_remote_repository(tmp_path, "hook-target")
    destination = tmp_path / "project-worktree"
    count = tmp_path / "calls"
    script = tmp_path / "create-hook.py"
    script.write_text(
        "import json,pathlib,subprocess,sys\n"
        "p=json.load(sys.stdin)\n"
        "assert p['name']==sys.argv[5] and p['cwd']==sys.argv[1]\n"
        "pathlib.Path(sys.argv[3]).write_text('called')\n"
        "if sys.argv[4]=='fail': raise SystemExit(3)\n"
        "mode=['--detach'] if sys.argv[6]=='detached' else ['-b','project-created']\n"
        "subprocess.run(['git','worktree','add',*mode,sys.argv[2]],check=True,stdout=sys.stderr)\n"
        "print(sys.argv[2])\n",
        encoding="utf-8",
    )
    argv = [
        sys.executable,
        str(script),
        str(local),
        str(destination),
        str(count),
        "fail" if fails else "success",
        name or "process-loop",
        "detached" if detached else "branch",
    ]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    settings = local / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"hooks": {"WorktreeCreate": [{"hooks": [{"type": "command", "command": command}]}]}}), encoding="utf-8"
    )
    first = _pl_worktree.prepare_session_target(local, "example/repo", "input", worktree_name=name, resume_pending=False)
    assert count.is_file()
    if fails:
        assert first is None and not destination.exists()
        assert not (local / ".claude/worktrees/fixed").exists()
        match = re.search(r"途中資源を保持した: (.+?\.json):", capsys.readouterr().err)
        assert match is not None
        evidence = pathlib.Path(match[1])
        assert evidence.parent.parent == managed_temp.temp_root_path()
        observed = json.loads(evidence.read_text(encoding="utf-8"))
        assert observed["exit_code"] == 3 and observed["input"]["name"] == (name or "process-loop")
    else:
        assert first is not None and first[0] == destination
        stored_paths = list((state_paths.state_dir() / "process-loop-worktrees").glob("*.json"))
        assert len(stored_paths) == 1
        stored = json.loads(stored_paths[0].read_text(encoding="utf-8"))
        diagnostics = pathlib.Path(stored["hook_diagnostics"])
        assert diagnostics.parent == managed_temp.temp_root_path()
        observed = json.loads((diagnostics / "WorktreeCreate-0.json").read_text(encoding="utf-8"))
        assert observed["exit_code"] == 0 and observed["input"]["cwd"] == str(local)
        count.unlink()
        second = _pl_worktree.prepare_session_target(local, "example/repo", "input", worktree_name=name, resume_pending=False)
        assert second is not None and second[0] == destination
        assert not count.exists()


@pytest.mark.parametrize("destination_kind", ["main", "branch", "detached"])
@pytest.mark.parametrize("dirty", [False, True])
def test_project_hook_rejects_preexisting_worktree(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], destination_kind: str, dirty: bool
) -> None:
    """新規hookの返却だけで既存資源を転用せず、branch・HEAD・ユーザー差分を保つ。"""
    local = _make_remote_repository(tmp_path, "hook-existing")
    destination = local if destination_kind == "main" else tmp_path / "other worktree"
    if destination_kind != "main":
        mode = ["--detach"] if destination_kind == "detached" else ["-b", "user-branch"]
        git_repository.run_git(local, "worktree", "add", *mode, str(destination))
    if dirty:
        (destination / "state.txt").write_text("user tracked change\n", encoding="utf-8")
        (destination / "user.txt").write_text("user untracked change\n", encoding="utf-8")
    script = tmp_path / "return-existing.py"
    script.write_text("import sys\nprint(sys.argv[1])\n", encoding="utf-8")
    argv = [sys.executable, str(script), str(destination)]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    settings = local / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"hooks": {"WorktreeCreate": [{"hooks": [{"type": "command", "command": command}]}]}}),
        encoding="utf-8",
    )

    def snapshot() -> tuple[str, str, str, str, str, str | None]:
        return (
            git_repository.run_git(destination, "symbolic-ref", "--quiet", "HEAD", check=False).stdout,
            git_repository.run_git(destination, "rev-parse", "HEAD").stdout,
            git_repository.run_git(destination, "status", "--porcelain=v1", "--untracked-files=all").stdout,
            git_repository.run_git(local, "for-each-ref", "refs/heads", "--format=%(refname) %(objectname)").stdout,
            (destination / "state.txt").read_text(encoding="utf-8"),
            (destination / "user.txt").read_text(encoding="utf-8") if dirty else None,
        )

    before = snapshot()
    result = _pl_worktree.prepare_session_target(local, "example/repo", "input", worktree_name="fixed", resume_pending=False)
    assert snapshot() == before
    assert result is None
    assert not list((state_paths.state_dir() / "process-loop-worktrees").glob("*.json"))
    assert "既存の作業ツリー" in capsys.readouterr().err
