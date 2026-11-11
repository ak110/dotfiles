# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesのprocessの試験。"""

# pylint: disable=protected-access

import contextlib
import ctypes
import io
import os
import pathlib
import subprocess
import sys
import types
from typing import Self

import psutil
import pytest
from agent_toolkit._testing import late_descendant

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_git  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_process  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_stashing  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _fake_popen,
    _fake_run,
    _FakePopen,
    _TimeoutPopen,
)
from update_dotfiles_test_helpers import _isolate_sync_report as _isolate_sync_report  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _logs_environment as _logs_environment  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _no_codex_processes as _no_codex_processes  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _own_git_process_scan as _own_git_process_scan  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _real_stash_runtime as _real_stash_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _separate_git_recovery as _separate_git_recovery  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _skip_agent_toolkit_prepare as _skip_agent_toolkit_prepare  # noqa: E402  # pylint: disable=wrong-import-position

from pytools._internal import common  # noqa: E402  # pylint: disable=wrong-import-position


def test_run_git_pull_disables_mise_auto_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """工程1のサブプロセス起動が`MISE_AUTO_INSTALL=0`を含む環境で実行される。"""
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls, environments=environments))

    assert update_dotfiles_process.run_git_pull(1, 4) == 0  # pylint: disable=protected-access
    assert environments[0]["MISE_AUTO_INSTALL"] == "0"


def test_run_git_pull_disables_submodule_recursion(monkeypatch: pytest.MonkeyPatch) -> None:
    """工程1がgitのサブコマンドより前に`-c submodule.recurse=false`を渡す。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))

    assert update_dotfiles_process.run_git_pull(1, 4) == 0  # pylint: disable=protected-access
    argv = calls[0]
    assert argv[argv.index("-c") + 1] == "submodule.recurse=false"
    assert argv.index("-c") < argv.index("pull")


def test_run_git_pull_decodes_output_as_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """工程1の取得出力をUTF-8でデコードする。"""
    calls: list[list[str]] = []
    encodings: list[str | None] = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls, encodings=encodings))

    assert update_dotfiles_process.run_git_pull(1, 4) == 0  # pylint: disable=protected-access
    assert encodings == ["utf-8"]


@pytest.mark.parametrize(
    ("raw_value", "expected_timeout"),
    [(None, 600), ("17", 17), ("0", None)],
)
def test_git_timeout_environment_is_applied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    raw_value: str | None,
    expected_timeout: int | None,
) -> None:
    """未設定・正数・0をgit pullの待機上限へ反映する。"""
    if raw_value is None:
        monkeypatch.delenv("UPDATE_DOTFILES_GIT_TIMEOUT_SEC", raising=False)
    else:
        monkeypatch.setenv("UPDATE_DOTFILES_GIT_TIMEOUT_SEC", raw_value)
    calls: list[list[str]] = []
    communicate_timeouts: list[int | None] = []
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _fake_popen({}, calls, communicate_timeouts=communicate_timeouts),
    )
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
    monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

    assert update_dotfiles.main() == 0
    assert communicate_timeouts == [expected_timeout]


@pytest.mark.parametrize("raw_value", ["-1", "invalid"])
def test_invalid_git_timeout_returns_2_without_starting_process(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """負数と整数でない上限値を値と受理条件付きで拒否する。"""
    monkeypatch.setenv("UPDATE_DOTFILES_GIT_TIMEOUT_SEC", raw_value)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail(f"Popen: {args}, {kwargs}"))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail(f"run: {args}, {kwargs}"))

    assert update_dotfiles.main() == 2
    error = capsys.readouterr().err
    assert raw_value in error
    assert "0以上の整数" in error


def test_git_timeout_kills_descendants_and_limits_output_recovery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """上限超過時に子孫と親を終了し、回収上限を付けて未完了を報告する。"""
    calls: list[list[str]] = []
    communicate_timeouts: list[int | None] = []
    process = _TimeoutPopen(
        ["chezmoi", "git"],
        calls,
        output_text="partial output\n",
        error_text="partial error\n",
        communicate_timeouts=communicate_timeouts,
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    killed: list[int] = []

    class Process:
        def __init__(self, pid: int, children: list[Self] | None = None) -> None:
            self.pid = pid
            self._children = children or []

        def children(self, *, recursive: bool) -> list[Self]:
            assert recursive
            return self._children

        def kill(self) -> None:
            killed.append(self.pid)

    descendants = [Process(101), Process(102)]
    parent = Process(process.pid, descendants)
    monkeypatch.setattr(psutil, "Process", lambda pid: parent if pid == process.pid else pytest.fail(str(pid)))
    waited: list[tuple[list[int], int]] = []

    def wait_procs(processes: list[Process], timeout: int) -> tuple[list[Process], list[Process]]:
        waited.append(([item.pid for item in processes], timeout))
        return processes, []

    monkeypatch.setattr(psutil, "wait_procs", wait_procs)

    assert update_dotfiles_process.run_git_pull(1, 4, timeout=7) == 1  # pylint: disable=protected-access

    assert communicate_timeouts == [7, 30]
    assert killed == [101, 102, process.pid]
    assert waited == [([101, 102, process.pid], 5)]
    assert process.kill_calls == 1
    captured = capsys.readouterr()
    assert "partial output" in captured.out
    assert "git pull" in captured.err
    assert "未完了" in captured.err
    assert "7秒" in captured.err
    assert "UPDATE_DOTFILES_GIT_TIMEOUT_SEC" in captured.err
    assert "子孫プロセスを終了" in captured.err


@pytest.mark.parametrize("target", ["dotfiles", "plugin"])
def test_unreclaimed_git_child_stops_update_before_chezmoi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    """時間上限のGit子プロセスを回収できなければ、任意プラグイン更新でも続行せずchezmoiを起動しない。"""
    calls: list[list[str]] = []
    process = _TimeoutPopen(["chezmoi", "git"], calls, output_text="", error_text="", communicate_timeouts=[])
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", tmp_path)
    monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")
    if target == "plugin":
        (tmp_path / "plugins" / "extra" / ".git").mkdir(parents=True)
        monkeypatch.setattr(update_dotfiles_git, "update_git_with_recovery", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
    survivor = types.SimpleNamespace(pid=4242, kill=lambda: None, children=lambda *, recursive: [])
    monkeypatch.setattr(psutil, "Process", lambda pid: survivor)
    monkeypatch.setattr(psutil, "wait_procs", lambda processes, timeout: ([], [survivor]))

    assert update_dotfiles.main() == 1

    # 偽のPopenは起動したGit段を`chezmoi git`として記録する。chezmoiの再展開・適用の段は起動しない。
    assert not [call for call in calls if call[:2] != ["chezmoi", "git"]]
    assert "回収できないため更新を中止" in capsys.readouterr().err


@pytest.mark.parametrize("platform_name", ["posix", "nt"])
def test_git_timeout_does_not_wait_again_when_output_recovery_times_out(
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
) -> None:
    """二重timeout後は復帰し、POSIXだけが呼出側でpipeを閉じる。"""
    calls: list[list[str]] = []
    communicate_timeouts: list[int | None] = []
    process = _TimeoutPopen(
        ["chezmoi", "git"],
        calls,
        always_timeout=True,
        communicate_timeouts=communicate_timeouts,
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(update_dotfiles_process, "kill_process_tree", lambda _process: None)
    # os.name全体を変えるとpathlibの実装選択へ波及するため、対象モジュールだけを差し替える。
    monkeypatch.setattr(update_dotfiles_process, "os", type("Platform", (), {"name": platform_name}))
    monkeypatch.setattr(update_dotfiles_runtime, "child_env", lambda: {})

    assert update_dotfiles_process.run_git_pull(1, 4, timeout=3) == 1  # pylint: disable=protected-access
    assert communicate_timeouts == [3, 30]
    assert process.stdout.closed is (platform_name == "posix")
    assert process.stderr.closed is (platform_name == "posix")
    process.stdout.close()
    process.stderr.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linuxのsubreaperで列挙外の孫もテスト自身が回収する")
def test_git_pull_closes_real_pipes_after_detached_descendant_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """直接子が終了し別sessionの孫だけがpipeを保持しても、二重timeout後に両pipeを閉じる。"""
    # Linux prctlの公開定数。孤児の孫を本テストがwaitpidし、PID 1へ残さない。
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int(0)
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0  # PR_GET_CHILD_SUBREAPER
    pid_path = tmp_path / "holder.pid"
    holder_source = late_descendant.held_source(tmp_path / "pipe.release")
    child_code = (
        "import pathlib, subprocess, sys; "
        f"child = subprocess.Popen([sys.executable, '-c', {holder_source!r}], start_new_session=True); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))"
    )
    holder_pid: int | None = None

    def restore_subreaper() -> None:
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0

    def reclaim_holder() -> None:
        nonlocal holder_pid
        if holder_pid is None and pid_path.exists():
            holder_pid = int(pid_path.read_text(encoding="utf-8"))
        if holder_pid is not None:
            os.kill(holder_pid, 9)
            os.waitpid(holder_pid, 0)

    with contextlib.ExitStack() as cleanup:
        assert libc.prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
        cleanup.callback(restore_subreaper)
        process = cleanup.enter_context(
            common.process_scope(
                [sys.executable, "-c", child_code, str(pid_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
            )
        )
        cleanup.callback(reclaim_holder)
        process.wait(timeout=10)
        holder_pid = int(pid_path.read_text(encoding="utf-8"))
        monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
        monkeypatch.setattr(update_dotfiles_runtime, "GIT_OUTPUT_RECOVERY_TIMEOUT_SEC", 0.05)
        assert update_dotfiles_process.run_git_pull(1, 4, timeout=1) == 1
        assert process.stdout is not None and process.stdout.closed
        assert process.stderr is not None and process.stderr.closed
    assert holder_pid is not None and not psutil.pid_exists(holder_pid)


@pytest.mark.parametrize("operation", ["pull", "stash"])
def test_git_subprocess_closes_both_pipes_when_one_close_fails(monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    """POSIXのpipe解放の片側が失敗しても、残る側を閉じて元の例外を返す。"""

    class FailingClose(io.StringIO):
        def close(self) -> None:
            if not self.closed:
                super().close()
                raise OSError("pipe close failed")

    process = _FakePopen(["test"], [])
    process.stdout.close()
    process.stdout = FailingClose()
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(update_dotfiles_runtime, "git_env", lambda: {})
    monkeypatch.setattr(update_dotfiles_process, "os", type("Platform", (), {"name": "posix"}))
    monkeypatch.setattr(update_dotfiles_stashing, "os", type("Platform", (), {"name": "posix"}))
    with pytest.raises(OSError, match="pipe close failed"):
        if operation == "pull":
            update_dotfiles_process.run_git_pull(1, 4, timeout=1)
        else:
            update_dotfiles_stashing._run_worktree_stash("save")
    assert process.stdout.closed and process.stderr.closed
