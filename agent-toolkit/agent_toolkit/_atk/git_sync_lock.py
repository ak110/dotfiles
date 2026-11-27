from __future__ import annotations

import contextlib
import contextvars
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys
import threading
import time
import weakref
from collections.abc import Callable
from typing import Protocol

import filelock
import psutil

from agent_toolkit._atk.environment import is_human_terminal
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._git import command as _git_command

LOCK_HOLD_TIMEOUT_SECONDS = 120.0
"""`repo_lock`を保持している区間のgit操作全体の時間上限。

1つの保持区間ではfetch・merge・commit・pushと、push拒否時の再試行まで続くため、操作ごとの上限だけでは
保持が数分に及ぶ。区間全体を有限にすると、待つ側の上限を「正常な保持者が解放するまで」の値に決められる。
"""

LOCK_WAIT_TIMEOUT_SECONDS = 150.0
"""`repo_lock`の待機上限を省略した場合の値。保持区間の上限より長くし、正常に動いている保持者の解放まで待てるようにする。"""


class RepoLockTimeoutError(filelock.Timeout):
    """`repo_lock`を待機上限まで取得できなかった。`reason`に待った対象と保持者、`next_action`に次の操作を持つ。"""

    next_action = (
        "保持者のプロセスが動いているか確かめ、終了を待つか、応答しない場合は停止してから再実行する。"
        "保持者が存在しない場合はそのまま再実行する"
    )

    def __init__(self, lock_file: str, *, waited: float, holder: str) -> None:
        super().__init__(lock_file)
        self.waited = waited
        self.holder = holder

    @property
    def reason(self) -> str:
        """失敗行へ載せる理由を返す。"""
        return f"共有ロックを{self.waited:.0f}秒待ったが取得できなかった: {self.lock_file}。保持者: {self.holder}"


class _ThreadLocalHeldPaths(threading.local):
    def __init__(self) -> None:
        self.paths: weakref.WeakValueDictionary[pathlib.Path, _RepoLock] = weakref.WeakValueDictionary()


_LOCK_HELD_PATHS = _ThreadLocalHeldPaths()


def git_common_dir(repo_path: pathlib.Path) -> pathlib.Path:
    """作業コピーが共有するGit common directoryを返す。

    `git rev-parse`を実行できないテスト用パスや初期化前のパスでは、対象パス自身を
    fallbackとして用いる。通常の作業コピーでは、複数worktreeが同じロックを共有する。
    """
    repo_path = repo_path.resolve()
    if not (repo_path / ".git").exists():
        return repo_path
    try:
        result = _git_command.run(["rev-parse", "--git-common-dir"], repo_path, check=False, capture_output=True, text=True)
    except OSError, subprocess.SubprocessError:
        return repo_path
    if result.returncode != 0 or not result.stdout.strip():
        return repo_path
    common = pathlib.Path(result.stdout.strip())
    if not common.is_absolute():
        common = repo_path / common
    try:
        return common.resolve()
    except OSError:
        return common.absolute()


def _lock_key(repo_path: pathlib.Path) -> pathlib.Path:
    """ロック記録用のcommon directoryを返す。"""
    return git_common_dir(repo_path)


def repo_lock_path(repo_path: pathlib.Path) -> pathlib.Path:
    """Git common directoryへ対応するロックファイルの絶対パスを返す。"""
    common = str(_lock_key(repo_path))
    digest = hashlib.sha1(common.encode("utf-8"), usedforsecurity=False).hexdigest()
    lock_dir = _state_paths.lock_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{digest}.lock"


class LockObserver(Protocol):
    """`repo_lock`の待機と保持の秒数を受け取る観測先。"""

    def add_lock_wait(self, seconds: float) -> None: ...

    def add_lock_hold(self, seconds: float) -> None: ...


LOCK_OBSERVER: contextvars.ContextVar[LockObserver | None] = contextvars.ContextVar("atk_repo_lock_observer", default=None)
"""`repo_lock`の待機・保持を記録する観測先。`atk serve`が要求ごとに設定し、設定の無い呼出しでは計測しない。"""


def _holder_path(lock_file: str) -> pathlib.Path:
    """ロックの保持者を記録するファイルのパスを返す。

    ロックファイル自体へは書かない。保持中のファイルを他のプロセスが読めるかはOSで異なるためである。
    """
    return pathlib.Path(lock_file + ".holder")


def _write_holder(lock_file: str) -> None:
    """待つ側が原因を示せるよう、ロックを取得したプロセスのPID・起動時刻・コマンドラインを記録する。"""
    try:
        process = psutil.Process()
        record = {
            "pid": process.pid,
            "started_at": datetime.datetime.fromtimestamp(process.create_time()).astimezone().isoformat(timespec="seconds"),
            "command": " ".join(process.cmdline()),
        }
        _holder_path(lock_file).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    except OSError, psutil.Error:
        # 記録は診断の補助であり、書けないことでロックの取得を失敗させない。
        pass


def _describe_holder(lock_file: str) -> str:
    """保持者の記録から、失敗行へ載せる保持者の説明を返す。"""
    try:
        record = json.loads(_holder_path(lock_file).read_text(encoding="utf-8"))
        pid = int(record["pid"])
        description = f"PID {pid}、起動 {record['started_at']}、コマンド `{record['command']}`"
    except OSError, ValueError, KeyError, TypeError:
        return "記録が無いため特定できない"
    if not psutil.pid_exists(pid):
        return f"{description}（このPIDのプロセスは存在しない）"
    return description


class _RepoLock(filelock.FileLock):  # pylint: disable=too-many-ancestors  # filelockの継承階層によるため。
    def __init__(self, repo_path: pathlib.Path, *, timeout: float) -> None:
        self._repo_path = repo_path
        self._target = _lock_key(repo_path)
        self._observer: LockObserver | None = None
        self._acquired_at: float | None = None
        self._hold_scope: contextlib.ExitStack | None = None
        super().__init__(str(repo_lock_path(repo_path)), timeout=timeout)

    def acquire(
        self,
        timeout: float | None = None,
        poll_interval: float | None = None,
        *,
        poll_intervall: float | None = None,
        blocking: bool | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> filelock.AcquireReturnProxy:
        result: filelock.AcquireReturnProxy | None = None
        observer = LOCK_OBSERVER.get()
        wait_started = time.perf_counter()
        if blocking is not False and is_human_terminal():
            # 他の主体が保持していて待つ場合だけ、待機中であることを人の端末へ届ける。
            try:
                result = super().acquire(
                    0, poll_interval, poll_intervall=poll_intervall, blocking=False, cancel_check=cancel_check
                )
            except filelock.Timeout:
                print(f"共有ロックの取得を待機中: {self._repo_path}", file=sys.stderr, flush=True)
        if result is None:
            try:
                result = super().acquire(
                    timeout,
                    poll_interval,
                    poll_intervall=poll_intervall,
                    blocking=blocking,
                    cancel_check=cancel_check,
                )
            except filelock.Timeout:
                # 取得できずに終わった待ちも、要求の所要のうちロック待ちが占めた区間として記録する。
                waited = time.perf_counter() - wait_started
                if observer is not None and self._acquired_at is None:
                    observer.add_lock_wait(waited)
                raise RepoLockTimeoutError(self.lock_file, waited=waited, holder=_describe_holder(self.lock_file)) from None
        _LOCK_HELD_PATHS.paths[self._target] = self
        if self.lock_counter == 1:
            _write_holder(self.lock_file)

            self._hold_scope = contextlib.ExitStack()
            self._hold_scope.enter_context(_git_command.timeout_scope(LOCK_HOLD_TIMEOUT_SECONDS))
        if observer is not None and self._acquired_at is None:
            acquired_at = time.perf_counter()
            observer.add_lock_wait(acquired_at - wait_started)
            self._observer = observer
            self._acquired_at = acquired_at
        return result

    def release(self, force: bool = False) -> None:
        if self.is_locked and (force or self.lock_counter == 1):
            # 保持者の記録は、次の取得者の記録を消さないようロックの解放より前に消す。
            with contextlib.suppress(OSError):
                _holder_path(self.lock_file).unlink(missing_ok=True)
            if self._hold_scope is not None:
                # 取得と別の文脈で解放された場合、期限の復元は行えないが、その文脈の終了とともに期限も消える。
                with contextlib.suppress(ValueError):
                    self._hold_scope.close()
                self._hold_scope = None
        super().release(force)
        if not self.is_locked and _LOCK_HELD_PATHS.paths.get(self._target) is self:
            del _LOCK_HELD_PATHS.paths[self._target]
            if self._observer is not None and self._acquired_at is not None:
                self._observer.add_lock_hold(time.perf_counter() - self._acquired_at)
            self._observer = None
            self._acquired_at = None


def repo_lock(repo_path: pathlib.Path, *, timeout: float | None = None) -> filelock.FileLock:
    """指定作業コピーへ対応するGit共通ロックを返す。

    `timeout`を省略すると`LOCK_WAIT_TIMEOUT_SECONDS`まで待つ。
    待機上限に達すると、保持者を示す`RepoLockTimeoutError`（`filelock.Timeout`の派生）を送出する。
    保持している間のgit操作は、合計で`LOCK_HOLD_TIMEOUT_SECONDS`を上限とする。
    """

    return _RepoLock(repo_path, timeout=LOCK_WAIT_TIMEOUT_SECONDS if timeout is None else timeout)


def assert_repo_lock_held(repo_path: pathlib.Path) -> None:
    """現在のスレッドが対象common directoryのロックを保持していることを検証する。"""
    lock = _LOCK_HELD_PATHS.paths.get(_lock_key(repo_path))
    if lock is None or not lock.is_locked:
        raise RuntimeError(
            "不変条件違反: private_notesへのgit操作・ファイル変更は_repo_lock保持下でのみ実行できる。"
            "呼び出し元でwith _repo_lock(private_notes):を用いること。"
        )
