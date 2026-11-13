# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesの標準indexロックの待機・残存ロック回収の試験。"""

# pylint: disable=protected-access

import json
import os
import pathlib
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable

import psutil
import pytest
from agent_toolkit._git import stale_lock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_git  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_process  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _REAL_UPDATE_GIT_WITH_RECOVERY,
    _REPOSITORY_ATK_BIN,
    _create_git_pair,
    _dirty_snapshot,
    _prepare_dirty,
    _wait_process_ready,
)
from update_dotfiles_test_helpers import _isolate_sync_report as _isolate_sync_report  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _logs_environment as _logs_environment  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _no_codex_processes as _no_codex_processes  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _own_git_process_scan as _own_git_process_scan  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _real_stash_runtime as _real_stash_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _separate_git_recovery as _separate_git_recovery  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _skip_agent_toolkit_prepare as _skip_agent_toolkit_prepare  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stale_lock, "POLL_INITIAL_SECONDS", 0.02)
    monkeypatch.setattr(stale_lock, "POLL_MAX_SECONDS", 0.05)


def _repository(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles_git, "_update_owned_git", lambda *_args, **_kwargs: 0)
    return local, local / ".git" / "index.lock"


def _write_record(lock: pathlib.Path, *, pid: int, create_time: float) -> None:
    status = lock.stat()
    (lock.parent / stale_lock.RECORD_NAME).write_text(
        json.dumps(
            {
                "format": stale_lock.RECORD_FORMAT,
                "pid": pid,
                "create_time": create_time,
                "host": stale_lock.socket.gethostname(),
                "index": str(lock.with_suffix("")),
                "lock_dev": status.st_dev,
                "lock_ino": status.st_ino,
            }
        ),
        encoding="utf-8",
    )


def _live_record(lock: pathlib.Path) -> None:
    _write_record(lock, pid=os.getpid(), create_time=psutil.Process().create_time())


def test_update_waits_for_lock_released_by_live_owner(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """生存中の所有者が数秒後に解放したロックを、同じ実行で取得して更新を進める。"""
    local, lock = _repository(monkeypatch, tmp_path)
    before = _dirty_snapshot(local)
    lock.write_bytes(b"")
    _live_record(lock)

    def release() -> None:
        time.sleep(0.3)
        (lock.parent / stale_lock.RECORD_NAME).unlink()
        lock.unlink()

    thread = threading.Thread(target=release)
    thread.start()
    try:
        assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=10) == 0
    finally:
        thread.join()
    assert _dirty_snapshot(local) == before
    assert not lock.exists()
    assert not (lock.parent / stale_lock.RECORD_NAME).exists()


def test_update_reclaims_lock_without_owner_record(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """旧版の残存ロック（所有記録なし）をGit・更新主体の不在を確認して回収し、更新を進める。"""
    local, lock = _repository(monkeypatch, tmp_path)
    before = _dirty_snapshot(local)
    lock.write_bytes(b"")

    assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=10) == 0
    assert _dirty_snapshot(local) == before
    assert not lock.exists()


@pytest.mark.parametrize("reuse", [False, True])
def test_update_reclaims_lock_of_finished_owner(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, reuse: bool) -> None:
    """所有者が終了した記録つきロックを、再観測を待たずに回収する。PIDの再利用も終了とみなす。"""
    _local, lock = _repository(monkeypatch, tmp_path)
    lock.write_bytes(b"")
    if reuse:
        _write_record(lock, pid=os.getpid(), create_time=psutil.Process().create_time() - 1000)
    else:
        _write_record(lock, pid=2**22 + 12345, create_time=1.0)

    assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=10) == 0
    assert not lock.exists()


@pytest.mark.parametrize(
    "cause",
    [
        "live-owner",
        "access-denied",
        "git-running",
        "other-updater",
        "worker",
        "scan-denied",
        "unnamed",
        "corrupt-record",
        "incomplete-record",
        "other-index-record",
    ],
)
def test_update_keeps_lock_and_explains_when_owner_is_not_gone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    cause: str,
) -> None:
    """生存中・照会不能・Git操作中・他の更新主体ありのロックは回収せず、期限内に非0で終え、診断を残す。"""
    local, lock = _repository(monkeypatch, tmp_path)
    before = _dirty_snapshot(local)
    lock.write_bytes(b"")
    if cause in {"live-owner", "access-denied"}:
        _live_record(lock)
    if cause == "corrupt-record":
        (lock.parent / stale_lock.RECORD_NAME).write_text("{broken", encoding="utf-8")
    if cause == "incomplete-record":
        (lock.parent / stale_lock.RECORD_NAME).write_text(
            json.dumps({"format": stale_lock.RECORD_FORMAT, "pid": 1}), encoding="utf-8"
        )
    if cause == "other-index-record":
        _live_record(lock)
        record = json.loads((lock.parent / stale_lock.RECORD_NAME).read_text(encoding="utf-8"))
        record["index"] = str(lock.parent / "other-index")
        record["create_time"] = 1.0
        (lock.parent / stale_lock.RECORD_NAME).write_text(json.dumps(record), encoding="utf-8")
    if cause == "access-denied":
        monkeypatch.setattr(stale_lock, "_process_state", lambda *_args: "unknown")
    if cause == "git-running":
        # 取得前の確認では操作中でなく、ロック判定の時点で操作中になる。
        checks = iter([False, True, True, True, True])
        monkeypatch.setattr(update_dotfiles_git, "_git_operation_in_progress", lambda: next(checks, True))
        monkeypatch.setattr(
            update_dotfiles_git, "_scan_git_processes", lambda: stale_lock.ProcessScan((stale_lock.ProcessInfo(4242, "git"),))
        )
    if cause == "other-updater":
        monkeypatch.setattr(
            stale_lock.psutil,
            "process_iter",
            lambda *_args, **_kwargs: iter([_FakeUpdater("python", ["python", "update_dotfiles.py"])]),
        )
    if cause == "worker":
        # 更新jobの実worker（`update_dotfiles_job.py --worker <job ID>`）のコマンドライン。
        worker = _FakeUpdater("python", [sys.executable, "libexec/update_dotfiles_job.py", "--worker", "job-1"])
        monkeypatch.setattr(stale_lock.psutil, "process_iter", lambda *_args, **_kwargs: iter([worker]))
    if cause == "scan-denied":
        monkeypatch.setattr(stale_lock.psutil, "process_iter", lambda *_args, **_kwargs: iter([_FakeUpdater("python", None)]))
    if cause == "unnamed":
        monkeypatch.setattr(stale_lock.psutil, "process_iter", lambda *_args, **_kwargs: iter([_FakeUpdater(None, None)]))
    started = time.monotonic()
    with update_dotfiles_runtime.configure_persistent_log("test"):
        assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=1) == 1
        log = update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")
    assert time.monotonic() - started < 5
    err = capsys.readouterr().err
    assert str(lock) in err
    assert "回収しませんでした" in err
    assert "update-dotfiles logs" in err
    assert str(lock) in log
    if cause == "git-running":
        assert "PID 4242" in err
    assert lock.exists()
    assert _dirty_snapshot(local) == before


class _FakeUpdater:
    """他のupdate-dotfilesの更新主体として走査される偽のプロセス。"""

    pid = 999_999

    def __init__(self, name: str | None, cmdline: list[str] | None) -> None:
        self.info = {"name": name, "cmdline": cmdline, "create_time": 1.0}


def test_reclaim_refuses_lock_replaced_just_before_removal(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """判定後、削除の直前に別主体が作り直したロックは削除せず、元の名前へ戻す。"""
    lock = tmp_path / "index.lock"
    lock.write_bytes(b"")
    key = stale_lock._stat_key(lock.stat())
    original_rename = os.rename

    def replace_then_rename(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        # 判定時の実体を別主体が解放し、新しいロックを作った状態を、移動の直前へ注入する。
        os.unlink(source)
        pathlib.Path(source).write_bytes(b"other")
        os.utime(source, ns=(key[2] + 10**9, key[2] + 10**9))
        original_rename(source, destination)

    monkeypatch.setattr(stale_lock.os, "rename", replace_then_rename)

    assert not stale_lock._reclaim(lock, key)
    assert lock.read_bytes() == b"other"
    assert not list(tmp_path.glob("index.lock.reclaim-*"))


def test_reclaim_exclusion_follows_owner_lifetime(tmp_path: pathlib.Path) -> None:
    """回収の排他を保持するプロセスが居る間は後発が回収せず、強制終了した後は回収できる。"""
    lock = tmp_path / "index.lock"
    lock.write_bytes(b"")
    key = stale_lock._stat_key(lock.stat())
    ready = tmp_path / "ready"
    holder = tmp_path / "holder.py"
    holder.write_text(
        "import pathlib, sys, time\n"
        f"sys.path.insert(0, {str(_REPOSITORY_ATK_BIN.parents[0])!r})\n"
        "from agent_toolkit._common import file_lock\n"
        "handle = (pathlib.Path(sys.argv[1]) / 'update-dotfiles-index-reclaim.lock').open('a+b')\n"
        "file_lock.acquire_lock(handle, blocking=False)\n"
        "pathlib.Path(sys.argv[2]).touch()\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    with subprocess.Popen(
        [sys.executable, str(holder), str(tmp_path), str(ready)], stderr=subprocess.PIPE, text=True
    ) as process:
        try:
            _wait_process_ready(process, ready)
            assert not stale_lock._reclaim(lock, key)
            assert lock.exists()
        finally:
            process.kill()
            process.communicate(timeout=15)
    assert stale_lock._reclaim(lock, key)
    assert not lock.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="実SIGKILLによる強制停止はPOSIX上で検証する")
@pytest.mark.usefixtures("real_stash_runtime")
@pytest.mark.parametrize("stop", ["before-record", "after-alternate"])
def test_update_reruns_after_real_forced_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
    stop: str,
) -> None:
    """公開入口の実行を指定地点でSIGKILLし、再実行で回復して、stage済み・未stage・未追跡の変更と代替indexを保持する。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    before = _dirty_snapshot(local)
    runner = tmp_path / "killed_update.py"
    runner.write_text(
        "import importlib.util, os, pathlib, signal, sys\n"
        f"sys.path.insert(0, {str(_REPOSITORY_ATK_BIN.parents[1])!r})\n"
        f"spec = importlib.util.spec_from_file_location('update_dotfiles', {update_dotfiles.__file__!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        f"module.update_dotfiles_runtime.DOTFILES_ROOT = pathlib.Path({str(local)!r})\n"
        "def die(*args, **kwargs):\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n"
        "if sys.argv[1] == 'before-record':\n"
        "    module.update_dotfiles_git.stale_lock.write_record = die\n"
        "else:\n"
        "    module.update_dotfiles_git.update_dotfiles_stashing.protect_old_worktree_stashes = die\n"
        "module.update_dotfiles_git.update_git_with_recovery(1, 4, timeout=30)\n",
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(runner), stop],
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == -signal.SIGKILL, (result.stdout, result.stderr)
    lock = local / ".git" / "index.lock"
    assert lock.exists()
    assert bool(list((local / ".git").glob("update-dotfiles-index-[!lr]*"))) == (stop == "after-alternate")
    alternates = {path.name for path in (local / ".git").glob("update-dotfiles-index-[!lr]*")}

    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
    assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=30) == 0

    assert _dirty_snapshot(local) == before
    assert not lock.exists()
    assert {path.name for path in (local / ".git").glob("update-dotfiles-index-[!lr]*")} == alternates


def test_lock_creation_only_file_exists_error_is_classified_as_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ロック作成以外の`FileExistsError`は標準ロックの競合として扱わない。"""
    local, lock = _repository(monkeypatch, tmp_path)
    before = _dirty_snapshot(local)

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        raise FileExistsError("copy")

    monkeypatch.setattr(update_dotfiles_git.shutil, "copyfile", fail_copy)
    assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5) == 1
    err = capsys.readouterr().err
    assert "対象indexの管理に失敗" in err
    assert "update-dotfiles logs" in err
    assert "進行中のGit操作が終了してから" not in err
    assert "回収しませんでした" not in err
    assert _dirty_snapshot(local) == before
    assert not lock.exists()


def test_plugin_pull_recovers_from_stale_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """プラグインのpullが残存ロックで失敗したら、回収して1回だけ再実行する。"""
    plugin, lock = _repository(monkeypatch, tmp_path)
    lock.write_bytes(b"")
    codes = iter([1, 0])
    calls: list[int] = []

    def once(*_args: object, **_kwargs: object) -> int:
        calls.append(1)
        update_dotfiles_runtime.last_stderr_tail = f"fatal: Unable to create '{lock}': File exists."
        return next(codes)

    monkeypatch.setattr(update_dotfiles_process, "_run_git_pull_once", once)
    assert update_dotfiles_process.run_git_pull(1, 3, timeout=10, plugin=plugin) == 0
    assert len(calls) == 2
    assert not lock.exists()


def test_plugin_pull_does_not_retry_when_lock_is_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """生存中の所有者のロックでは再実行せず、診断を標準エラーへ出す。"""
    plugin, lock = _repository(monkeypatch, tmp_path)
    lock.write_bytes(b"")
    _live_record(lock)
    calls: list[int] = []

    def once(*_args: object, **_kwargs: object) -> int:
        calls.append(1)
        update_dotfiles_runtime.last_stderr_tail = f"fatal: Unable to create '{lock}': File exists."
        return 128

    monkeypatch.setattr(update_dotfiles_process, "_run_git_pull_once", once)
    assert update_dotfiles_process.run_git_pull(1, 3, timeout=1, plugin=plugin) == 128
    assert len(calls) == 1
    assert "回収しませんでした" in capsys.readouterr().err
    assert lock.exists()


@pytest.mark.usefixtures("real_stash_runtime")
def test_update_waits_for_real_git_holding_index_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """実gitが`index.lock`を保持している間は待ち、解放後に同じ呼び出しで更新を進める。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    before = _dirty_snapshot(local)
    ready, release = tmp_path / "ready", tmp_path / "release"
    gate = tmp_path / "gate.py"
    gate.write_text(
        "import pathlib, sys, time\n"
        "ready, release = map(pathlib.Path, sys.argv[1:3])\n"
        "ready.touch()\n"
        "deadline = time.monotonic() + 30\n"
        "while not release.exists() and time.monotonic() < deadline: time.sleep(0.02)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    hook = local / ".git" / "hooks" / "pre-commit"
    command = " ".join(shlex.quote(str(value).replace("\\", "/")) for value in (sys.executable, gate, ready, release))
    hook.write_text("#!/bin/sh\n" + command + "\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles_git, "_PROCESS_EXIT_GRACE_SECONDS", 3.0)
    with subprocess.Popen(
        ["git", "-C", str(local), "commit", "-a", "-m", "同期点"],
        env=host_environ(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as process:
        try:
            _wait_process_ready(process, ready)
            # `commit -a`はhookの実行中も`index.lock`を保持する。
            assert (local / ".git" / "index.lock").exists()
            timer = threading.Timer(0.5, release.touch)
            timer.start()
            assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=30) == 0
            timer.join()
        finally:
            release.touch()
            process.communicate(timeout=15)
    assert _dirty_snapshot(local) == before
    assert not (local / ".git" / "index.lock").exists()


def test_update_deducts_lock_wait_from_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """ロックの待機に使った時間を、更新へ渡す上限から差し引く。"""
    _local, lock = _repository(monkeypatch, tmp_path)
    lock.write_bytes(b"")
    _live_record(lock)
    received: list[float | None] = []

    def owned(_step: int, _total: int, *, timeout: float | None) -> int:
        received.append(timeout)
        return 0

    monkeypatch.setattr(update_dotfiles_git, "_update_owned_git", owned)

    def release() -> None:
        time.sleep(0.6)
        (lock.parent / stale_lock.RECORD_NAME).unlink()
        lock.unlink()

    thread = threading.Thread(target=release)
    thread.start()
    try:
        assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5) == 0
    finally:
        thread.join()
    assert received
    assert received[0] is not None
    assert 1.0 <= received[0] <= 4.5


def test_plugin_pull_shares_deadline_with_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """プラグインpullの再実行へ、初回と待機で使った時間を差し引いた上限を渡す。"""
    plugin, lock = _repository(monkeypatch, tmp_path)
    lock.write_bytes(b"")
    timeouts: list[float | None] = []
    codes = iter([1, 0])

    def once(*_args: object, timeout: float | None, **_kwargs: object) -> int:
        timeouts.append(timeout)
        time.sleep(0.3)
        update_dotfiles_runtime.last_stderr_tail = f"fatal: Unable to create '{lock}': File exists."
        return next(codes)

    monkeypatch.setattr(update_dotfiles_process, "_run_git_pull_once", once)
    assert update_dotfiles_process.run_git_pull(1, 3, timeout=10, plugin=plugin) == 0
    assert timeouts[0] == 10
    assert timeouts[1] is not None
    assert timeouts[1] < 9.7
