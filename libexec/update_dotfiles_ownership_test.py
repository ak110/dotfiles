# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesのownershipの試験。"""

# pylint: disable=protected-access

import os
import pathlib
import shlex
import signal
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import psutil
import pytest
from agent_toolkit._git import stale_lock
from agent_toolkit._testing import late_descendant

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_git  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_process  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_stashing  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _REAL_UPDATE_GIT_WITH_RECOVERY,
    _REPOSITORY_ATK_BIN,
    _create_git_pair,
    _dirty_snapshot,
    _git,
    _prepare_dirty,
    _UnreadableGitProcess,
    _wait_process_ready,
)
from update_dotfiles_test_helpers import _isolate_sync_report as _isolate_sync_report  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _logs_environment as _logs_environment  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _no_codex_processes as _no_codex_processes  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _own_git_process_scan as _own_git_process_scan  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _real_stash_runtime as _real_stash_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _separate_git_recovery as _separate_git_recovery  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _skip_agent_toolkit_prepare as _skip_agent_toolkit_prepare  # noqa: E402  # pylint: disable=wrong-import-position


def test_update_skips_when_git_process_scan_is_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """対象の走査で照会を拒否されたgitプロセスがあれば、内容を変えずに更新を見送る。"""
    local, seed, _remote = _create_git_pair(tmp_path)
    (local / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    (seed / "tracked.txt").write_text("upstream\n", encoding="utf-8")
    _git(seed, "add", "tracked.txt")
    _git(seed, "commit", "-m", "upstream")
    _git(seed, "push")
    head = _git(local, "rev-parse", "HEAD")
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles_process, "run_git_pull", lambda *_args, **_kwargs: pytest.fail("pull開始"))
    monkeypatch.setattr(
        stale_lock.psutil, "process_iter", lambda *_args, **_kwargs: iter([_UnreadableGitProcess(pid=os.getpid())])
    )

    assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 5, timeout=30) == 1
    assert _git(local, "rev-parse", "HEAD") == head
    assert (local / "tracked.txt").read_text(encoding="utf-8") == "dirty\n"
    assert not _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree")


@pytest.mark.skipif(sys.platform == "win32", reason="使用中cwdの削除はPOSIX上で検証する")
def test_update_ignores_unrelated_git_with_removed_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """別Gitのcwdが走査後に消えても、対象rootの所有取得を妨げない。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    directory = tmp_path / "unrelated-git-cwd"
    directory.mkdir()
    ready, release = tmp_path / "ready", tmp_path / "release"
    gate = tmp_path / "gate.py"
    gate.write_text(
        "import pathlib, sys, time\n"
        "ready, release = map(pathlib.Path, sys.argv[1:])\n"
        "ready.touch()\n"
        "deadline = time.monotonic() + 30\n"
        "while not release.exists() and time.monotonic() < deadline: time.sleep(0.02)\n",
        encoding="utf-8",
    )
    alias = "!" + " ".join(shlex.quote(str(value)) for value in (sys.executable, gate, ready, release))
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles_git, "_update_owned_git", lambda *_args, **_kwargs: 0)
    with subprocess.Popen(
        ["git", "-C", str(directory), "-c", f"alias.wait={alias}", "wait"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as process:
        try:
            _wait_process_ready(process, ready)
            snapshot = next(candidate for candidate in psutil.process_iter(["name", "cmdline"]) if candidate.pid == process.pid)
            directory.rmdir()
            assert snapshot.cwd() == str(directory)
            # 最初の走査後、index取得までの間に消えたcwdを2回目の検査へ渡す。
            scans = iter([[], [snapshot]])
            monkeypatch.setattr(stale_lock.psutil, "process_iter", lambda *_args, **_kwargs: iter(next(scans)))

            assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5) == 0
        finally:
            release.touch()
            stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, (stdout, stderr)
    assert not list((local / ".git").glob("update-dotfiles-index-*"))


@pytest.mark.parametrize("phase", ["pre-commit", "editor", "commit-a"])
@pytest.mark.parametrize("scope", ["same", "other-worktree", "unrelated"])
def test_update_skips_while_git_commit_in_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    phase: str,
    host_environ: Callable[[], dict[str, str]],
    scope: str,
) -> None:
    """実commitのhook・編集区間を保持し、最初の変更前の見送りを検査する。"""
    local, seed, _remote = _create_git_pair(tmp_path)
    target = local
    if scope == "other-worktree":
        target = tmp_path / "other"
        _git(local, "worktree", "add", "-b", "other", str(target))
    elif scope == "unrelated":
        target = seed
    _prepare_dirty(local)
    original = _dirty_snapshot(local)
    ready, release = tmp_path / "ready", tmp_path / "release"
    gate = tmp_path / "gate.py"
    gate.write_text(
        "import pathlib, sys, time\n"
        "ready, release, tracked = map(pathlib.Path, sys.argv[1:4])\n"
        "original = tracked.read_bytes()\n"
        "if sys.argv[4] == 'pre-commit': tracked.write_bytes(b'base\\n')\n"
        "ready.touch()\n"
        "deadline = time.monotonic() + 30\n"
        "while not release.exists() and time.monotonic() < deadline: time.sleep(0.02)\n"
        "tracked.write_bytes(original)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    command = " ".join(
        shlex.quote(str(value).replace("\\", "/"))
        for value in (
            sys.executable,
            gate,
            ready,
            release,
            local / "tracked.txt",
            phase,
        )
    )
    env = host_environ()
    if phase == "editor":
        env["GIT_EDITOR"] = command
        arguments = ["commit"]
    else:
        hook = local / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\n" + command + "\n", encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        arguments = ["commit", *(["-a"] if phase == "commit-a" else []), "-m", "同期点"]
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", target)
    monkeypatch.setattr(update_dotfiles_stashing, "protect_old_worktree_stashes", lambda: pytest.fail("変更開始"))
    if scope != "same":
        monkeypatch.setattr(update_dotfiles_git, "_update_owned_git", lambda *_args, **_kwargs: 0)
    with subprocess.Popen(
        ["git", "-C", str(local), *arguments],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as process:
        try:
            _wait_process_ready(process, ready)
            before = _dirty_snapshot(local)
            assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5) == int(scope == "same")
            assert _dirty_snapshot(local) == before
        finally:
            release.touch()
            stdout, stderr = process.communicate(timeout=15)
    assert process.returncode != 0, (stdout, stderr)
    assert _dirty_snapshot(local) == original
    assert not list((local / ".git").glob("update-dotfiles-index-*"))


@pytest.mark.usefixtures("real_stash_runtime")
@pytest.mark.parametrize("commit_all", [False, True])
def test_update_holds_index_lock_against_later_git_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    commit_all: bool,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """更新所有中の後発commitを実Gitが拒否し、別worktreeは独立して動く。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    before = _dirty_snapshot(local)
    other = tmp_path / "other"
    _git(local, "worktree", "add", "-b", "other", str(other))
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)

    def later_commit(*_args: object, **_kwargs: object) -> int:
        result = subprocess.run(
            ["git", "-C", str(local), "commit", *(["-a"] if commit_all else []), "-m", "後発"],
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode != 0
        assert "index.lock" in result.stderr
        (other / "other.txt").write_text("other\n", encoding="utf-8")
        _git(other, "add", "other.txt")
        _git(other, "commit", "-m", "別worktree")
        return 0

    monkeypatch.setattr(update_dotfiles_process, "run_git_pull", later_commit)
    assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5) == 0
    assert _dirty_snapshot(local) == before
    assert (other / "other.txt").read_text(encoding="utf-8") == "other\n"
    assert not (local / ".git" / "index.lock").exists()
    assert "GIT_INDEX_FILE" not in update_dotfiles_runtime.child_env()


@pytest.mark.parametrize("outcome", ["success", "failure", "interrupt", "publish-failure", "stat-failure"])
def test_update_recovers_index_after_interrupted_git_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    outcome: str,
) -> None:
    """成功・失敗・中断のindexを公開し、自身の一時物とロックだけ回収する。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    other = tmp_path / "other"
    _git(local, "worktree", "add", "-b", "other", str(other))
    other_index = pathlib.Path(_git(other, "rev-parse", "--git-path", "index"))
    other_lock = other_index.with_name("index.lock")
    other_lock.write_bytes(b"other owner")
    before_other = _dirty_snapshot(other)
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)

    def change(*_args: object, **_kwargs: object) -> int:
        assert update_dotfiles_process.run_git_change("add", "tracked.txt")
        if outcome == "interrupt":
            raise KeyboardInterrupt
        if outcome == "publish-failure":

            def fail_publish(*_args: object, **_kwargs: object) -> None:
                raise OSError("index公開失敗")

            monkeypatch.setattr(update_dotfiles.os, "replace", fail_publish)
        if outcome == "stat-failure":
            original_stat = pathlib.Path.stat
            failed = False

            def fail_stat(path: pathlib.Path, **kwargs: Any) -> os.stat_result:
                nonlocal failed
                if path == local / ".git" / "index.lock" and not failed:
                    failed = True
                    raise OSError("ロック確認失敗")
                return original_stat(path, **kwargs)

            monkeypatch.setattr(pathlib.Path, "stat", fail_stat)
        if outcome == "failure":
            assert not update_dotfiles_process.run_git_change("restore", "--", "missing-file")
            return 1
        return 0

    monkeypatch.setattr(update_dotfiles_git, "_update_owned_git", change)
    with update_dotfiles_runtime.configure_persistent_log("test"):
        if outcome == "interrupt":
            with pytest.raises(KeyboardInterrupt):
                _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5)
        else:
            assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5) == int(outcome != "success")
        assert ("tracked.txt" in _git(local, "diff", "--cached", "--name-only")) == (
            outcome not in {"publish-failure", "stat-failure"}
        )
        assert (local / "tracked.txt").read_text(encoding="utf-8") == "unstaged\n"
        assert other_lock.read_bytes() == b"other owner"
        assert _dirty_snapshot(other) == before_other
        assert not (local / ".git" / "index.lock").exists()
        assert not list((local / ".git").glob("update-dotfiles-index-*"))
        if outcome == "failure":
            log = update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")
            assert "git restore -- missing-file: exit=1" in log
            assert "pathspec" in log


@pytest.mark.parametrize("race", ["started-before-lock", "foreign-lock", "copy-interrupt"])
def test_update_preserves_index_when_ownership_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    race: str,
) -> None:
    """ロック後の再確認と準備中断でも、既存indexと別主体のロックを保持する。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    before = _dirty_snapshot(local)
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles_git, "_update_owned_git", lambda *_args, **_kwargs: pytest.fail("変更開始"))
    lock = local / ".git" / "index.lock"
    if race == "started-before-lock":
        calls: list[int] = []

        def started_after_first_check() -> bool:
            calls.append(1)
            return len(calls) > 1

        monkeypatch.setattr(update_dotfiles_git, "_git_operation_in_progress", started_after_first_check)
    elif race == "foreign-lock":
        # 生存中の別主体のロックは、待機の上限まで待っても回収しない。
        monkeypatch.setattr(stale_lock, "POLL_INITIAL_SECONDS", 0.01)
        lock.write_bytes(b"foreign")
        stale_lock.write_record(lock.parent, lock.with_name("index"), lock.stat())
    else:

        def interrupt_copy(source: pathlib.Path, destination: pathlib.Path, **_kwargs: object) -> None:
            del source
            destination.write_bytes(b"partial index")
            raise KeyboardInterrupt

        monkeypatch.setattr(update_dotfiles.shutil, "copyfile", interrupt_copy)
    if race == "copy-interrupt":
        with pytest.raises(KeyboardInterrupt):
            _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5)
    else:
        assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=1) == 1
    assert _dirty_snapshot(local) == before
    assert not [path for path in (local / ".git").glob("update-dotfiles-index-*") if path.name != stale_lock.RECORD_NAME]
    if race == "foreign-lock":
        assert lock.read_bytes() == b"foreign"
    else:
        assert not lock.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIXの実SIGTERMによるプロセス中断を検証する")
def test_update_recovers_index_after_sigterm(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    """実プロセスへの停止通知後もGit作業を再開でき、完了済みstageを保持する。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    ready = tmp_path / "ready"
    runner = tmp_path / "interrupt_update.py"
    runner.write_text(
        "import importlib.util, pathlib, sys, time\n"
        f"sys.path.insert(0, {str(_REPOSITORY_ATK_BIN.parents[1])!r})\n"
        f"spec = importlib.util.spec_from_file_location('update_dotfiles', {update_dotfiles.__file__!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        f"module.update_dotfiles_runtime.DOTFILES_ROOT = pathlib.Path({str(local)!r})\n"
        "def change(*args, **kwargs):\n"
        "    assert module.update_dotfiles_process.run_git_change('add', 'tracked.txt')\n"
        f"    pathlib.Path({str(ready)!r}).touch()\n"
        f"    exec({late_descendant.held_source(tmp_path / 'interrupt.release')!r})\n"
        "module.update_dotfiles_git._update_owned_git = change\n"
        "module.update_dotfiles_git.update_git_with_recovery(1, 4, timeout=5)\n",
        encoding="utf-8",
    )
    with subprocess.Popen(
        [sys.executable, str(runner)],
        env=host_environ(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as process:
        try:
            _wait_process_ready(process, ready)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    assert process.returncode != 0, (stdout, stderr)
    assert not (local / ".git" / "index.lock").exists()
    assert not list((local / ".git").glob("update-dotfiles-index-*"))
    assert "tracked.txt" in _git(local, "diff", "--cached", "--name-only")
    _git(local, "add", "untracked.txt")
