import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_toolkit._testing import git_repository

from pytools._internal import verify_worktree_venv


@pytest.fixture(name="nested_worktree")
def _nested_worktree(tmp_path: Path) -> tuple[Path, Path]:
    primary = git_repository.init_repository(tmp_path / "親 checkout", commit_message="基点")
    linked = primary / "検証 worktree"
    git_repository.run_git(primary, "worktree", "add", "--detach", str(linked))
    return primary, linked


def test_rejects_synchronizing_the_parent_worktrees_environment(nested_worktree: tuple[Path, Path]) -> None:
    primary, linked = nested_worktree
    environment = primary / ".venv"
    site_packages = environment / "site-packages"
    site_packages.mkdir(parents=True)
    editable = site_packages / "editable.pth"
    saved = os.fsencode(str(primary)) + b"\n"
    editable.write_bytes(saved)

    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("verify_worktree_venv.py")), str(linked), str(environment)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=15,
    )

    assert result.returncode == 1
    assert "次の操作:" in result.stderr
    assert editable.read_bytes() == saved


def test_rejects_link_at_the_default_environment(nested_worktree: tuple[Path, Path]) -> None:
    primary, linked = nested_worktree
    environment = primary / ".venv"
    environment.mkdir()
    default = linked / ".venv"
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(default), str(environment)],
            capture_output=True,
            check=True,
            timeout=10,
        )
    else:
        default.symlink_to(environment, target_is_directory=True)

    with pytest.raises(ValueError, match="既定の仮想環境"):
        verify_worktree_venv.verify(linked, Path(".venv"))


def test_accepts_dedicated_environment_in_the_deepest_worktree(nested_worktree: tuple[Path, Path], tmp_path: Path) -> None:
    _, linked = nested_worktree
    verify_worktree_venv.verify(linked, Path(".venv"))
    verify_worktree_venv.verify(linked, tmp_path / "専用環境")


def test_accepts_standalone_directory_without_git(tmp_path: Path) -> None:
    verify_worktree_venv.verify(tmp_path, Path(".venv"))


@pytest.mark.skipif(os.name == "nt" or shutil.which("make") is None, reason="Makeのシェル起動をPOSIXで検証する")
@pytest.mark.parametrize("target", ["sync", "test", "setup-browser"])
@pytest.mark.parametrize("empty_environment", [False, True])
def test_make_stops_shared_environment_before_mutation(nested_worktree, tmp_path, target, empty_environment):
    primary, linked = nested_worktree
    (primary / ".venv").mkdir()
    marker = primary / ".venv" / "editable.pth"
    marker.write_text(str(primary), encoding="utf-8")
    (linked / ".venv").symlink_to(primary / ".venv", target_is_directory=True)
    (linked / ".python-version").write_text("3.15\n", encoding="utf-8")
    guard = Path(verify_worktree_venv.__file__)
    copied_guard = linked / "pytools" / "_internal" / guard.name
    copied_guard.parent.mkdir(parents=True)
    shutil.copyfile(guard, copied_guard)
    executable = tmp_path / "uv"
    log = tmp_path / "uv.log"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$2" = "--no-project" ]; then shift 4; exec ' + shlex.quote(sys.executable) + ' "$@"; fi\n'
        'printf "%s:%s\\n" "$1" "${UV_NO_SYNC:-}" >> "$LOG"\n'
        'if [ "$1" = "sync" ]; then printf "%s" "$PWD" > "${UV_PROJECT_ENVIRONMENT:-.venv}/editable.pth"; fi\n',
        encoding="utf-8",
    )
    executable.chmod(0o700)
    make = Path(verify_worktree_venv.__file__).parents[2] / "Makefile"
    args = ["make", "-f", str(make), target]
    if empty_environment:
        args.append("UV_PROJECT_ENVIRONMENT=")
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "LOG": str(log)}
    rejected = subprocess.run(args, cwd=linked, env=env, capture_output=True, text=True, check=False, timeout=30)
    assert rejected.returncode != 0 and "別の場所" in rejected.stderr
    assert marker.read_text(encoding="utf-8") == str(primary) and not log.exists()
    (linked / ".venv").unlink()
    (linked / ".venv").mkdir()
    accepted = subprocess.run(args, cwd=linked, env=env, capture_output=True, text=True, check=False, timeout=30)
    assert accepted.returncode == 0, accepted.stderr
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0].startswith("run:" if target == "setup-browser" else "sync:")
    if target == "test":
        assert "run:true" in lines
    assert marker.read_text(encoding="utf-8") == str(primary)


@pytest.mark.skipif(os.name == "nt", reason="Windowsでは改行をファイル名にできない")
def test_rejects_environment_from_a_worktree_with_a_newline_in_its_name(nested_worktree):
    primary, linked = nested_worktree
    other = primary / "別\nworktree"
    git_repository.run_git(primary, "worktree", "add", "--detach", str(other))
    environment = other / ".venv"
    environment.mkdir()
    with pytest.raises(ValueError, match="別の作業ツリー"):
        verify_worktree_venv.verify(linked, environment)
