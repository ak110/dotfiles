"""公開コミット起動から、実hookの中断と所有する資源の回収を確認する。"""

import argparse
import os
import pathlib
import shlex
import shutil
import signal
import sys
import time
from collections.abc import Callable

import psutil
import pytest
import yaml

from agent_toolkit._atk import run_script
from agent_toolkit._common import process_tree
from agent_toolkit._testing import git_repository


@pytest.mark.skipif(os.name != "posix", reason="commit入口はPOSIX専用")
def test_public_commit_forwards_git_exit_code(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """実hookで拒否されたGitの終了コードを返し、HEADとstage済み変更を保持する。"""
    repo = git_repository.init_repository(tmp_path / "repo", files={"tracked": "base"}, commit_message="基点")
    base = git_repository.git_output(repo, "rev-parse", "HEAD")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-ran"
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\nprintf rejected > {shlex.quote(str(marker))}\nexit 7\n", encoding="utf-8")
    hook.chmod(0o755)
    git_repository.run_git(repo, "config", "core.hooksPath", str(hooks))
    (repo / "tracked").write_text("changed", encoding="utf-8")
    git_repository.run_git(repo, "add", "tracked")
    monkeypatch.chdir(repo)
    result = run_script.dispatch(
        argparse.Namespace(script_name="commit-with-timeout", script_args=["--timeout", "10", "--", "-m", "終了コードの確認"])
    )
    # Gitはpre-commitの非0終了をcommitの終了コード1へ変換する。
    assert result == 1
    assert marker.read_text(encoding="utf-8") == "rejected"
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == base
    assert git_repository.git_output(repo, "show", ":tracked") == "changed"


def _holds_resources(process: psutil.Process) -> bool:
    try:
        return process.is_running() and process.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
    except psutil.NoSuchProcess:
        return False


@pytest.mark.skipif(os.name != "posix", reason="Git hookとSIGINTの公開操作はPOSIX専用")
def test_public_commit_finishes_hook_with_a_waiting_verification(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """競合待機者がいてもhookを自己待機させず、commit終了後に次の検証へ枠を返す。"""
    environment = dict(os.environ)
    environment["PATH"] = host_environ().get("PATH", "")
    # 競合状態の故障注入を他の検証から分離し、このテストの子だけで同じ枠を共有する。
    environment.update(PYTHONPATH=str(run_script.PLUGIN_ROOT), PYFLTR_CACHE_DIR=str(tmp_path / "cache"))
    repo = git_repository.init_repository(tmp_path / "repo", files={"tracked": "base"}, commit_message="基点")
    ready, waiting, release, complete, acquired = [
        tmp_path / name for name in ("ready", "waiting", "release", "complete", "acquired")
    ]
    helper = tmp_path / "hook.py"
    helper.write_text(
        "import pathlib,sys,time\nfrom pyfltr.command import execution_slot as slot\n"
        f"ready=pathlib.Path({str(ready)!r}); waiting=pathlib.Path({str(waiting)!r})\n"
        f"release=pathlib.Path({str(release)!r}); complete=pathlib.Path({str(complete)!r})\n"
        f"acquired=pathlib.Path({str(acquired)!r})\n"
        "if sys.argv[1]=='hook':\n"
        " with slot.hold(label='hook'):\n"
        "  ready.touch(); deadline=time.monotonic()+10\n"
        "  while not release.exists() and time.monotonic()<deadline: time.sleep(0.01)\n"
        "  assert release.exists()\n"
        "  complete.touch()\n"
        "else:\n"
        " def on_wait(_owner): waiting.touch()\n"
        " with slot.hold(label='waiter',on_wait=on_wait): acquired.touch()\n",
        encoding="utf-8",
    )
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, str(helper), "hook"]) + "\n", encoding="utf-8")
    hook.chmod(0o755)
    git_repository.run_git(repo, "config", "core.hooksPath", str(hooks))
    (repo / "tracked").write_text("changed", encoding="utf-8")
    git_repository.run_git(repo, "add", "tracked")
    command = [
        sys.executable,
        "-m",
        "agent_toolkit.atk",
        "run-script",
        "commit-with-timeout",
        "--",
        "--timeout",
        "10",
        "--",
        "-m",
        "競合待機の確認",
    ]
    stdout, stderr = tmp_path / "stdout", tmp_path / "stderr"
    with (
        stdout.open("wb") as out,
        stderr.open("wb") as err,
        process_tree.process_scope(command, cwd=repo, env=environment, stdout=out, stderr=err) as commit,
    ):
        deadline = time.monotonic() + 10
        while not ready.exists() and commit.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), stderr.read_text(encoding="utf-8")
        with process_tree.process_scope(
            [sys.executable, str(helper), "waiter"], env=environment, stdout=out, stderr=err
        ) as waiter:
            deadline = time.monotonic() + 10
            while not waiting.exists() and waiter.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert waiting.exists(), stderr.read_text(encoding="utf-8")
            release.touch()
            assert commit.wait(timeout=15) == 0, stderr.read_text(encoding="utf-8")
            assert waiter.wait(timeout=15) == 0, stderr.read_text(encoding="utf-8")
    assert complete.exists() and acquired.exists()


@pytest.mark.skipif(os.name != "posix", reason="commit入口はPOSIX専用")
def test_public_commit_counts_slot_wait_in_timeout_and_does_not_start_commit(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """実行枠を待ったまま上限に達すると、commitを起動せず終了コード124で終わる。

    枠待ちを上限から除くと、枠が空くまで終わらず、呼び出し側がホストへ指定した時間上限に先に達する。
    """
    environment = dict(os.environ)
    environment["PATH"] = host_environ().get("PATH", "")
    environment.update(PYTHONPATH=str(run_script.PLUGIN_ROOT), PYFLTR_CACHE_DIR=str(tmp_path / "cache"))
    repo = git_repository.init_repository(tmp_path / "repo", files={"tracked": "base"}, commit_message="基点")
    base = git_repository.git_output(repo, "rev-parse", "HEAD")
    (repo / "tracked").write_text("changed", encoding="utf-8")
    git_repository.run_git(repo, "add", "tracked")
    held = tmp_path / "held"
    holder_code = (
        "import pathlib,sys,time\nfrom pyfltr.command import execution_slot as slot\n"
        "with slot.hold(label='holder'):\n pathlib.Path(sys.argv[1]).touch(); time.sleep(60)\n"
    )
    command = [sys.executable, "-m", "agent_toolkit.atk", "run-script", "commit-with-timeout", "--"]
    command += ["--timeout", "2", "--", "-m", "枠待ちの上限の確認"]
    stderr = tmp_path / "stderr"
    with process_tree.process_scope([sys.executable, "-c", holder_code, str(held)], env=environment) as holder:
        deadline = time.monotonic() + 10
        while not held.exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert held.exists()
        started = time.monotonic()
        with stderr.open("wb") as err, process_tree.process_scope(command, cwd=repo, env=environment, stderr=err) as commit:
            code = commit.wait(timeout=30)
        elapsed = time.monotonic() - started
        holder.kill()

    assert code == 124, stderr.read_text(encoding="utf-8")
    assert elapsed < 20
    assert "commitは開始していない" in stderr.read_text(encoding="utf-8")
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == base
    assert git_repository.git_output(repo, "diff", "--cached", "--name-only") == "tracked"


@pytest.mark.skipif(os.name != "posix", reason="SIGINTとprekの退避復元の入口はPOSIX専用")
@pytest.mark.parametrize("termination", ["timeout", "interrupt"])
def test_public_commit_reclaims_detached_verifier_and_restores_unstaged_changes(
    tmp_path: pathlib.Path, termination: str, host_environ: Callable[[], dict[str, str]]
) -> None:
    """Git親だけの終了を成功とせず、所有子孫・退避・無関係な実行を公開操作から確かめる。"""
    environment = dict(os.environ)
    environment["PATH"] = host_environ().get("PATH", "")
    prek = shutil.which("prek", path=environment["PATH"])
    if prek is None:
        pytest.skip("実prekが必要")
    environment.update(PYTHONPATH=str(run_script.PLUGIN_ROOT), PREK_HOME=str(tmp_path / "prek"))
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    initial = git_repository.commit_all(repo, "基点")
    marker = tmp_path / "child.pid"
    hook_program = tmp_path / "verifier.py"
    hook_program.write_text(
        "import pathlib,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)\n"
        f"marker=pathlib.Path({str(marker)!r}); pending=marker.with_suffix('.pending')\n"
        "pending.write_text(str(child.pid)); pending.replace(marker)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    config = repo / ".pre-commit-config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {
                        "repo": "local",
                        "hooks": [
                            {
                                "id": "verifier",
                                "name": "verifier",
                                "language": "system",
                                "entry": shlex.join([sys.executable, str(hook_program)]),
                                "pass_filenames": False,
                                "always_run": True,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    pre_commit = hooks / "pre-commit"
    pre_commit.write_text("#!/bin/sh\nexec " + shlex.join([prek, "run", "--config", str(config)]) + "\n", encoding="utf-8")
    pre_commit.chmod(0o755)
    git_repository.run_git(repo, "config", "core.hooksPath", str(hooks))
    git_repository.run_git(repo, "add", ".pre-commit-config.yaml")
    tracked.write_text("unstaged\n", encoding="utf-8")
    stdout, stderr = tmp_path / "stdout", tmp_path / "stderr"
    command = [
        sys.executable,
        "-m",
        "agent_toolkit.atk",
        "run-script",
        "commit-with-timeout",
        "--",
        "--timeout",
        "3" if termination == "timeout" else "20",
        "--",
        "-m",
        "中断の確認",
    ]
    with (
        process_tree.process_scope([sys.executable, "-c", "import time; time.sleep(60)"], env=environment) as unrelated,
        stdout.open("wb") as out,
        stderr.open("wb") as err,
        process_tree.process_scope(command, cwd=repo, env=environment, start_new_session=True, stdout=out, stderr=err) as owned,
    ):
        known: list[psutil.Process] = []
        try:
            deadline = time.monotonic() + 15
            while not marker.exists() and owned.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists(), stderr.read_text(encoding="utf-8")
            child = psutil.Process(int(marker.read_text(encoding="utf-8")))
            known = process_tree.collect_descendants(owned.pid)
            assert os.getsid(child.pid) != os.getsid(owned.pid)
            if termination == "interrupt":
                os.killpg(owned.pid, signal.SIGINT)
            assert owned.wait(timeout=30) == (124 if termination == "timeout" else 130), stderr.read_text(encoding="utf-8")
            assert not _holds_resources(child)
            assert not any(_holds_resources(process) for process in known)
            assert tracked.read_text(encoding="utf-8") == "unstaged\n"
            assert unrelated.poll() is None
            assert git_repository.git_output(repo, "rev-parse", "HEAD") == initial
        finally:
            process_tree.stop_tree(None, known=known)
