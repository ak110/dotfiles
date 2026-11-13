# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesの標準indexロックの待機・残存ロック回収の試験。"""

# pylint: disable=protected-access

import json
import os
import pathlib
import sys
import threading
import time

import psutil
import pytest
from agent_toolkit._git import stale_lock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles_git  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_process  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _REAL_UPDATE_GIT_WITH_RECOVERY,
    _create_git_pair,
    _dirty_snapshot,
    _prepare_dirty,
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
    assert not (lock.parent / stale_lock.RECLAIM_NAME).exists()


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


@pytest.mark.parametrize("cause", ["live-owner", "access-denied", "git-running", "other-updater", "scan-denied"])
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
    if cause == "scan-denied":
        monkeypatch.setattr(stale_lock.psutil, "process_iter", lambda *_args, **_kwargs: iter([_FakeUpdater("python", None)]))
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

    def __init__(self, name: str, cmdline: list[str] | None) -> None:
        self.info = {"name": name, "cmdline": cmdline, "create_time": 1.0}


def test_reclaim_refuses_replaced_lock(tmp_path: pathlib.Path) -> None:
    """判定後に差し替えられたロックは回収しない。"""
    lock = tmp_path / "index.lock"
    lock.write_bytes(b"")
    key = stale_lock._stat_key(lock.stat())
    lock.unlink()
    lock.write_bytes(b"other")
    # inode番号の再利用で同一性が偶然一致しないよう、更新時刻も変える。
    os.utime(lock, ns=(key[2] + 10**9, key[2] + 10**9))

    assert not stale_lock._reclaim(lock, key)
    assert lock.read_bytes() == b"other"
    assert not (tmp_path / stale_lock.RECLAIM_NAME).exists()


def test_reclaim_exclusion_and_abandoned_exclusion(tmp_path: pathlib.Path) -> None:
    """回収の排他を保持する間は後発が回収せず、作成者が終了した排他は回収して再試行できる。"""
    lock = tmp_path / "index.lock"
    lock.write_bytes(b"")
    key = stale_lock._stat_key(lock.stat())
    reclaim = tmp_path / stale_lock.RECLAIM_NAME
    reclaim.write_text(json.dumps({"pid": os.getpid(), "create_time": psutil.Process().create_time()}), encoding="utf-8")

    assert not stale_lock._reclaim(lock, key)
    assert lock.exists()
    assert reclaim.exists()

    reclaim.write_text(json.dumps({"pid": os.getpid(), "create_time": 1.0}), encoding="utf-8")
    assert not stale_lock._reclaim(lock, key)
    assert not reclaim.exists()
    assert stale_lock._reclaim(lock, key)
    assert not lock.exists()


@pytest.mark.parametrize("stop", ["before-record", "after-alternate"])
def test_update_reruns_after_forced_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, stop: str) -> None:
    """強制停止の各地点が残したロックから再実行で回復し、stage済み・未stageの変更と代替indexを保持する。"""
    local, lock = _repository(monkeypatch, tmp_path)
    before = _dirty_snapshot(local)
    lock.write_bytes(b"")
    alternate = lock.parent / "update-dotfiles-index-leftover"
    if stop == "after-alternate":
        alternate.write_bytes((lock.parent / "index").read_bytes())
        _live_record(lock)
        # 記録の所有者だけが終了した状態にする。
        _write_record(lock, pid=os.getpid(), create_time=1.0)
    # 回収処理自体の中断も再試行できる。
    (lock.parent / stale_lock.RECLAIM_NAME).write_text("", encoding="utf-8")
    os.utime(lock.parent / stale_lock.RECLAIM_NAME, (1, 1))

    assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=10) == 0
    assert _dirty_snapshot(local) == before
    assert not lock.exists()
    assert alternate.exists() == (stop == "after-alternate")


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
