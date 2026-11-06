"""起動した外部コマンドを子孫プロセスごと回収する。

ラッパー、シェル、エージェントCLIは、自身の実行中に補助プロセスを起動する。
終了処理が直接の子だけを終了させると、これらの子孫は親を失ったまま生存し続ける。
外部コマンドの終了処理は`stop_tree`へ集約し、生成の停止、終了対象の確定、終了要求、回収の確認を1回の呼び出しで行う。

終了要求の前に1回だけ列挙した一覧で回収を完了と判断してはならない。
列挙の後に起動した子孫は一覧に含まれず、親の終了後は起点から辿れなくなるためである。
WindowsのCodex App Serverでは、この漏れた孫が一時領域を保持して削除がWinError 32で失敗した。
`stop_tree`は起点と子孫を一時停止しながら列挙を繰り返して生成を止め、増えなくなった集合を終了させる。
一時停止できない対象、列挙の失敗、反復上限への到達は`TreeStopResult.unresolved`で全対象の回収成功と区別する。

完全な回収を保証しない範囲は次のとおりである。
一時停止できない対象が起動した子孫と、POSIXの終了猶予中に子を起動した親がポーリング間隔より早く終了した場合の子
（親を失った子はinitへ付け替わり、起点から辿れない）である。
起点自身を一時停止しない呼び出し（`include_root=False`）では、凍結後に起点が新たに起動した子がさらに起動した子孫も含む。

対象の特定は起点PIDからの子孫関係だけを根拠とする。
プロセス名の部分一致とパターン一致で対象集合を広げない。

回収できない子孫は、任意機能の失敗・通常の時間上限・警告だけへ変換しない。
所有する資源の停止を確認できない間に後続の更新や再接続へ進むと、残った子孫と新しい処理が同じ資源を奪い合う。
`run_with_timeout`と`process_scope`は回収不能を`ReclaimError`で送出し、所有対象を保持して同じ対象の回収を再試行できるようにする。
"""

import contextlib
import ctypes
import ctypes.wintypes
import dataclasses
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Generator, Iterable
from typing import Any, Self

# 回収しない呼出し元では、プロセス管理の依存を読み込まない。
lazy import psutil

_LOG = logging.getLogger(__name__)

RECLAIM_TIMEOUT_SECONDS = 5.0
"""終了要求と強制終了のそれぞれで子孫の終了を待つ時間（秒）。

委譲先が起動する子孫はMCPサーバーなどの短命な補助プロセスであり、
終了要求の到達から資源の解放までに要する時間は秒未満である。
既存のCodex App Serverの終了待機と同じ値を用い、委譲先セッションの終了が延びる上限をそろえる。
"""

_POLL_INTERVAL_SECONDS = 0.05
"""子孫の終了を確認する間隔（秒）。"""

_FREEZE_MAX_ROUNDS = 10
"""`stop_tree`が一時停止と列挙を繰り返す上限回数。

一時停止した対象は子を起動できないため、各回で増えるのは前回の列挙と一時停止の間に起動した子孫だけである。
一時停止できない対象が子を起動し続ける場合も、この回数で列挙を打ち切り、未確定として報告する。
"""


@dataclasses.dataclass
class TreeStopResult:
    """`stop_tree`の結果。

    `residual`が空でも`unresolved`が空でなければ、生成を止められなかった子孫が残っている可能性がある。
    """

    targets: list[psutil.Process]
    """終了させた所有対象（起点を含む場合は起点を含む）。"""
    residual: list[psutil.Process]
    """資源を保持したまま残った対象。`reclaim_processes`へそのまま渡して再試行できる。"""
    unresolved: list[str]
    """生成の停止を確認できなかった理由。"""
    _descriptions: dict[int, str] = dataclasses.field(default_factory=dict)

    def describe_residual(self, process: psutil.Process) -> str:
        """終了要求の前に取得した記述で対象を示す（終了後は起動コマンドを取得できないため）。"""
        return self._descriptions.get(process.pid) or describe(process)

    def descriptions(self, *, exclude_pid: int | None = None) -> list[str]:
        """残存の記述と未確定の理由を、呼び出し元の残存診断へ渡す形式で返す。"""
        residual = [self.describe_residual(process) for process in self.residual if process.pid != exclude_pid]
        return residual + self.unresolved_descriptions()

    def unresolved_descriptions(self) -> list[str]:
        """未確定の理由を、残存診断へ渡す形式で返す。"""
        return [f"生成の停止を確認できない: {reason}" for reason in self.unresolved]


class ReclaimError(RuntimeError):
    """起動した子プロセスの停止を確認できない。

    任意機能の失敗を吸収する既存の`except`節（`OSError`、`subprocess.SubprocessError`）に捕まらないよう、
    `RuntimeError`の派生にする。受け取った呼出元は後続の更新・再接続を始めず、`retry`で同じ対象を回収し直す。
    """

    def __init__(self, residual: list[str], processes: list[psutil.Process], *, context: str) -> None:
        super().__init__(residual_message(residual, context=context))
        self.residual = residual
        self.processes = processes
        self.context = context

    def retry(self, *, timeout: float = RECLAIM_TIMEOUT_SECONDS) -> list[str]:
        """保持した所有対象とその子孫を回収し直し、なお残る対象の記述を返す（空なら回収済み）。"""
        result = stop_tree(None, known=self.processes, timeout=timeout)
        self.processes = list(result.residual)
        self.residual = result.descriptions()
        return self.residual


def stop_tree_by_pid(pid: int, **kwargs: Any) -> TreeStopResult | None:
    """`pid`のプロセスを起点に`stop_tree`を呼ぶ。起点が既に消滅していればNoneを返す。

    起点が消滅していると、POSIXでは子孫の親がinitへ付け替わり起点から辿れないため、回収の対象を特定できない。
    """
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return None
    return stop_tree(root, **kwargs)


def stop_tree(
    root: psutil.Process | None,
    *,
    include_root: bool = True,
    known: Iterable[psutil.Process] = (),
    pins: ProcessIdentityPins | None = None,
    timeout: float = RECLAIM_TIMEOUT_SECONDS,
) -> TreeStopResult:
    """`root`の子孫（`include_root`なら`root`自身も）の生成を止め、終了させて回収する。

    `root`を省略（None）すると`known`だけを起点に回収する。
    前回の停止で未確定が残った対象を、起点の終了後に再試行する場合に使う。

    Windowsでは一時停止した全対象を強制終了する（一時停止中でも終了できる）。
    POSIXでは全対象へSIGTERMを送ってから再開し、正常終了の機会を保つ。
    猶予中に増えた子孫にもSIGTERMを送り、期限で再び一時停止してからSIGKILLを送る。
    `known`は呼び出し元が既に所有する対象であり、同じ扱いで終了させる。
    `pins`を渡すと、呼び出し元が事前観測から保持している親世代を使う。
    渡した`pins`は呼び出し元が回収終了後に閉じる。
    途中で例外が起きても、一時停止した対象を残さないよう所有対象を強制終了してから例外を伝播させる。
    同期的に待機するため、イベントループ上から呼ぶ場合は別スレッドへ退避させる。
    """
    with _OwnedTree(root, include_root=include_root, known=known, pins=pins) as tree:
        return _stop_owned_tree(tree, timeout)


def _stop_owned_tree(tree: _OwnedTree, timeout: float) -> TreeStopResult:
    finished = False
    try:
        tree.freeze()
        descriptions = {process.pid: describe(process) for process in tree.targets()}
        if sys.platform == "win32":
            tree.kill_all()
        else:
            tree.terminate_gracefully(timeout)
            tree.suspended.clear()
            tree.freeze()
            tree.kill_all()
        residual = _wait_until_released(tree.targets(), timeout=timeout)
        # 一時停止できなかった対象が終了までに子を起動していないかを確かめ、見つかれば同じく終了させる。
        if late := tree.scan():
            tree.kill_all()
            residual = _wait_until_released(tree.targets(), timeout=timeout)
            tree.unresolve_late(late)
        tree.resolve_suspend_failures()
        finished = True
    finally:
        if not finished:
            for process in tree.targets():
                with contextlib.suppress(psutil.Error, OSError):
                    process.kill()
    return TreeStopResult(tree.targets(), residual, tree.unresolved, descriptions)


class ProcessIdentityPins:
    """最初の所有観測から回収まで、Windowsのプロセス世代を保持する。"""

    def __init__(self) -> None:
        self._handles = contextlib.ExitStack()
        self._retained: dict[tuple[int, float], bool] = {}
        self._lock = threading.RLock()
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()

    def close(self) -> None:
        """全観測・回収が終わった所有者が、保持したkernel handleを解放する。"""
        with self._lock:
            self._closed = True
            self._handles.close()

    def retain(self, process: psutil.Process) -> bool:
        """観測した同じ世代を保持する。終了後も既に保持済みならTrueを返す。"""
        with self._lock:
            if self._closed or (key := _key(process)) is None:
                return False
            if sys.platform != "win32":
                return process.is_running()
            if key not in self._retained:
                self._retained[key] = self._handles.enter_context(_retain_windows_process(process))
            return self._retained[key]


class _OwnedTree:
    """`stop_tree`が所有する終了対象と、子孫を辿る起点の集合。"""

    def __init__(
        self,
        root: psutil.Process | None,
        *,
        include_root: bool,
        known: Iterable[psutil.Process],
        pins: ProcessIdentityPins | None,
    ) -> None:
        self._anchors: dict[tuple[int, float], psutil.Process] = {}
        self._owned: dict[tuple[int, float], psutil.Process] = {}
        self.suspended: set[tuple[int, float]] = set()
        self.unresolved: list[str] = []
        # 一時停止できなかった対象と理由。終了処理の後も資源を保持していれば未確定として報告する。
        self._suspend_failures: dict[tuple[int, float], tuple[psutil.Process, str]] = {}
        self._pins = ProcessIdentityPins() if pins is None else pins
        self._owns_pins = pins is None
        root_key = None if root is None else _key(root)
        if root is not None and root_key is not None:
            if include_root and root.pid != os.getpid():
                self._owned[root_key] = root
            else:
                self._anchors[root_key] = root
        self.add(known)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exception: object) -> None:
        if self._owns_pins:
            self._pins.close()

    def _retain(self, process: psutil.Process) -> bool:
        try:
            retained = self._pins.retain(process)
            if not retained and sys.platform == "win32":
                self._unresolve(f"pid {process.pid} の親世代を終了前に保持できなかった")
            return retained
        except (psutil.Error, OSError) as error:
            self._unresolve(f"pid {process.pid} の親世代を保持できない: {type(error).__name__}: {error}")
            return False

    def targets(self) -> list[psutil.Process]:
        return list(self._owned.values())

    def add(self, processes: Iterable[psutil.Process]) -> list[psutil.Process]:
        """自プロセス以外の未所有の対象を所有に加え、加えた対象を返す。"""
        added = []
        for process in processes:
            key = _key(process)
            if key is None or key in self._owned or key in self._anchors or process.pid == os.getpid():
                continue
            self._owned[key] = process
            added.append(process)
        return added

    def freeze(self) -> None:
        """所有対象を一時停止しながら子孫を列挙し、増えなくなるまで所有に加える。"""
        for _ in range(_FREEZE_MAX_ROUNDS):
            for key, process in list(self._owned.items()):
                if key in self.suspended:
                    continue
                self.suspended.add(key)
                try:
                    process.suspend()
                except psutil.NoSuchProcess:
                    pass  # 既に終了した対象は子を起動しない。
                except (psutil.Error, OSError) as error:
                    # 未確定とするかは、終了処理の後に`resolve_suspend_failures`で判定する。
                    reason = f"pid {process.pid} を一時停止できない: {type(error).__name__}: {error}"
                    self._suspend_failures[key] = (process, reason)
            if not self.scan():
                return
        self._unresolve(f"{_FREEZE_MAX_ROUNDS}回の一時停止と列挙の後も子孫が増え続けた")

    def resolve_suspend_failures(self) -> None:
        """一時停止できなかった対象を、子孫を追跡できなかった可能性に応じて未確定として報告する。

        Windowsでは親の終了後も子の親PIDが残り、終了処理の後の列挙で終了した対象の子まで所有へ加えて終了させている。
        このため、終了処理の後も資源を保持している対象だけを未確定とする（終了中の対象への一時停止はAccessDeniedになる）。
        POSIXでは親を失った子がinitへ付け替わり辿れないため、一時停止できなかった対象は終了した場合も未確定とする。
        """
        for process, reason in self._suspend_failures.values():
            if sys.platform != "win32" or _holds_resources(process):
                self._unresolve(reason)

    def unresolve_late(self, late: list[psutil.Process]) -> None:
        """終了処理の後の列挙で新しい子孫が見つかったことを、生成を止められなかった証拠として報告する。"""
        self._unresolve(f"終了処理の後に子孫が見つかった: {[process.pid for process in late]}")

    def _unresolve(self, reason: str) -> None:
        # POSIXでは終了猶予の前後で2回凍結するため、同じ理由を重複して報告しない。
        if reason not in self.unresolved:
            self.unresolved.append(reason)

    def scan(self) -> list[psutil.Process]:
        """所有対象と起点の子孫のうち未所有の対象を所有に加えて返す。列挙の失敗は未確定として記録する。"""
        try:
            found = _scan_descendants([*self._anchors.values(), *self._owned.values()], retain=self._retain)
        except (psutil.Error, OSError) as error:
            self._unresolve(f"子孫を列挙できない: {type(error).__name__}: {error}")
            return []
        return self.add(found)

    def terminate_gracefully(self, timeout: float) -> None:
        """SIGTERMを送ってから再開し、猶予中に増えた子孫にもSIGTERMを送りながら終了を待つ。"""
        targets = self.targets()
        for process in targets:
            with contextlib.suppress(psutil.Error, OSError):
                process.terminate()
        for process in targets:
            with contextlib.suppress(psutil.Error, OSError):
                process.resume()
        deadline = time.monotonic() + timeout
        while any(_holds_resources(process) for process in self.targets()) and time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL_SECONDS)
            for process in self.scan():
                with contextlib.suppress(psutil.Error, OSError):
                    process.terminate()

    def kill_all(self) -> None:
        """所有対象を強制終了し、強制終了の間に増えた子孫も強制終了する。"""
        killed: set[int] = set()
        for _ in range(_FREEZE_MAX_ROUNDS):
            for process in self.targets():
                if id(process) in killed:
                    continue
                killed.add(id(process))
                with contextlib.suppress(psutil.Error, OSError):
                    process.kill()
            if not self.scan():
                return


def _key(process: psutil.Process) -> tuple[int, float] | None:
    """PIDの再利用と区別するため、PIDと起動時刻の組で対象を識別する。消滅済みならNoneを返す。"""
    try:
        return process.pid, process.create_time()
    except psutil.Error, OSError:
        return None


@contextlib.contextmanager
def _retain_windows_process(process: psutil.Process) -> Generator[bool]:
    """同じ世代と確認した親のkernel handleを、子孫列挙が終わるまで保持する。

    Windowsは終了後もPPIDを保持し、最後のhandleを閉じるとPIDを再利用する。
    handle取得後の同一性検証と保持を組み合わせ、終了した別世代の子を所有へ加えない。
    """
    if sys.platform != "win32":
        yield False
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
    kernel.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel.GetProcessTimes.restype = ctypes.wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [ctypes.wintypes.HANDLE, *([ctypes.POINTER(ctypes.wintypes.FILETIME)] * 4)]
    handle = kernel.OpenProcess(0x1000, False, process.pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        error = ctypes.get_last_error()
        _LOG.warning(
            "Windows親世代保持診断: reason=open-process error=%s observed=%s current=%s",
            error,
            _key(process),
            identity(process.pid),
        )
        if error == 87:  # ERROR_INVALID_PARAMETER: 既に消滅している。
            yield False
            return
        raise ctypes.WinError(error)
    try:
        created, exited, kernel_time, user_time = (ctypes.wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)
        ):
            error = ctypes.get_last_error()
            _LOG.warning("Windows親世代保持診断: reason=process-times error=%s observed=%s", error, _key(process))
            raise ctypes.WinError(error)
        # is_running()は終了済みの同世代もFalseにする。handle自身の作成時刻を観測済みの値と比べる。
        # psutilのcreate_timeと丸めを揃え、1601→1970の差を整数で引いてから秒へ変換する。
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        handle_created_at = float(ticks - 116444736000000000) / 10000000
        observed_created_at = process.create_time()
        same_generation = handle_created_at == observed_created_at
        if not same_generation:
            _LOG.warning(
                "Windows親世代保持診断: reason=creation-time observed=%s handle_created_at=%r current=%s",
                (process.pid, observed_created_at),
                handle_created_at,
                identity(process.pid),
            )
        yield same_generation
    finally:
        kernel.CloseHandle(handle)


def _scan_descendants(parents: list[psutil.Process], *, retain: Callable[[psutil.Process], bool]) -> list[psutil.Process]:
    """`parents`の子孫を、1回のプロセス一覧から親PIDを辿って返す。

    終了済みの親は、同じ世代のhandleを保持している場合だけ辿る。
    Windowsでは親の終了後も子の親PIDが残るため、保持した親から親を失った子孫も見つけられる。
    子の起動時刻が親より前の場合と、親PIDを別のプロセスが再利用している場合のその新しいプロセスの子は含めない。
    """
    entries: list[tuple[psutil.Process, int]] = []
    for process in psutil.process_iter(["ppid"]):
        ppid = process.info.get("ppid")
        if isinstance(ppid, int):
            entries.append((process, ppid))
    holders = {process.pid: process for process, _ in entries}

    def retain_parent(process: psutil.Process, stage: str) -> bool:
        retained = retain(process)
        if not retained and sys.platform == "win32":
            # 保持失敗時の親子関係を同じsnapshotから記録し、消滅と世代不一致を判別する。
            _LOG.warning(
                "Windows親世代保持診断: stage=%s observed=%s current=%s snapshot_present=%s snapshot_children=%s",
                stage,
                _key(process),
                identity(process.pid),
                process.pid in holders,
                [
                    {"pid": child.pid, "snapshot_ppid": ppid, "observed": _key(child), "current": identity(child.pid)}
                    for child, ppid in entries
                    if ppid == process.pid
                ],
            )
        return retained

    created: dict[int, float] = {}
    for parent in parents:
        with contextlib.suppress(psutil.Error, OSError):
            if retain_parent(parent, "known"):
                created[parent.pid] = parent.create_time()
    found: list[psutil.Process] = []
    pending = set(created)
    while pending:
        next_pending: set[int] = set()
        for process, ppid in entries:
            if ppid not in pending or process.pid in created:
                continue
            try:
                child_created = process.create_time()
                if child_created < created[ppid]:
                    continue
                holder = holders.get(ppid)
                # 親PIDを別の新しいプロセスが再利用していれば、その後に起動した子は再利用側の子である。
                if holder is not None and created[ppid] != holder.create_time() <= child_created:
                    continue
            except psutil.Error, OSError:
                continue
            created[process.pid] = child_created
            found.append(process)
            # 今はleafでも、次の列挙までに子を起動して終了し得るため、発見時から世代を保持する。
            if retain_parent(process, "discovered"):
                next_pending.add(process.pid)
        pending = next_pending
    return found


def stop_process_tree(process: subprocess.Popen[Any]) -> list[str]:
    """所有する子を子孫ごと停止し、回収できなかったPIDと起動コマンドを返す。"""
    return _stop_owned_process(process)[0]


def _stop_owned_process(process: subprocess.Popen[Any]) -> tuple[list[str], list[psutil.Process]]:
    """所有する子を子孫ごと停止し、回収できなかった記述と、再試行に使う所有対象を返す。"""
    result = stop_tree_by_pid(process.pid)
    # 所有する子はzombie表示でも回収済みとは限らない。スレッドグループのリーダーだけが先に終了し、
    # 他のスレッドが生存する多スレッドの子はzombieと表示され、`reclaim_processes`の強制終了の対象から外れる。
    # そのためzombie判定に頼らず回収を上限付きで待ち、終わらなければ強制終了を重ねる。
    try:
        process.wait(timeout=RECLAIM_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=RECLAIM_TIMEOUT_SECONDS)
    residual = [] if result is None else result.descriptions(exclude_pid=process.pid)
    owned = [] if result is None else [target for target in result.residual if target.pid != process.pid]
    if result is not None and result.unresolved:
        # 生成停止を確認できない場合は、残存一覧に無い子孫も再試行で辿れるよう所有対象全体を保持する。
        owned = [target for target in result.targets if target.pid != process.pid]
    if process.poll() is None:
        residual.append(f"{process.pid}: {process.args!r}")
        with contextlib.suppress(psutil.Error):
            owned.append(psutil.Process(process.pid))
    return residual, owned


@contextlib.contextmanager
def process_scope(
    *popenargs: Any,
    close_streams: bool = True,
    **kwargs: Any,
) -> Generator[subprocess.Popen[Any]]:
    """獲得直後から子プロセスの停止とパイプの解放を所有する。"""
    # Popen.__exit__の無期限waitを避け、停止できない子も上限付きで診断する。
    process = subprocess.Popen(*popenargs, **kwargs)  # pylint: disable=consider-using-with
    try:
        yield process
    finally:
        in_flight = sys.exception()
        try:
            if process.poll() is None:
                try:
                    residual, owned = _stop_owned_process(process)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=RECLAIM_TIMEOUT_SECONDS)
                if residual and isinstance(in_flight, ReclaimError):
                    # 本体が既に回収不能を送出している場合は、同じ例外へ対象を合流させて元の原因を保つ。
                    in_flight.residual.extend(entry for entry in residual if entry not in in_flight.residual)
                    in_flight.processes.extend(item for item in owned if item not in in_flight.processes)
                elif residual:
                    raise ReclaimError(residual, owned, context="process_scope")
        finally:
            if close_streams:
                with contextlib.ExitStack() as cleanup:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            cleanup.callback(stream.close)


def run_with_timeout(
    *popenargs: Any,
    input: Any = None,  # pylint: disable=redefined-builtin  # subprocess.runの公開引数とそろえる
    capture_output: bool = False,
    timeout: float | None = None,
    check: bool = False,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """subprocess.runと同じ入力で実行し、時間上限では子孫の停止後に例外を送出する。"""
    if input is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdinとinputは同時に指定できない")
        kwargs["stdin"] = subprocess.PIPE
    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("capture_outputとstdout・stderrは同時に指定できない")
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    with process_scope(*popenargs, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            residual, owned = _stop_owned_process(process)
            if residual:
                # 残存がパイプを保持していても待たずに戻り、通常の時間上限と区別して回収不能を伝える。
                raise ReclaimError(residual, owned, context="run_with_timeout") from error
            stdout, stderr = process.communicate()
            encoding = kwargs.get("encoding") or "utf-8"
            error.output = stdout.encode(encoding) if isinstance(stdout, str) else stdout
            error.stderr = stderr.encode(encoding) if isinstance(stderr, str) else stderr
            raise
    returncode = process.returncode
    assert returncode is not None
    if check and returncode:
        raise subprocess.CalledProcessError(returncode, process.args, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(process.args, returncode, stdout, stderr)


def collect_descendants(pid: int | None, *, pins: ProcessIdentityPins | None = None) -> list[psutil.Process]:
    """`pid`を起点とする子孫プロセスを列挙する。

    起点プロセスが存在しない場合と、権限などで列挙できない場合は空リストを返す。
    回収は委譲の付随処理であり、列挙できないことを委譲の失敗として扱わない。
    `pins`を指定した場合は、親世代を保持できた子孫だけを返し、最終回収まで保持を続ける。
    """
    if pid is None:
        return []
    try:
        root = psutil.Process(pid)
        if pins is not None and not pins.retain(root):
            return []
        children = root.children(recursive=True)
        return children if pins is None else [process for process in children if pins.retain(process)]
    except psutil.Error, OSError:
        return []


def reclaim_descendants(descendants: list[psutil.Process], *, timeout: float = RECLAIM_TIMEOUT_SECONDS) -> list[str]:
    """列挙済みの子孫プロセスを終了させ、回収できなかった対象の記述を返す。

    記述はPIDと起動コマンドで構成する。起動コマンドは終了要求より前に取得する。
    終了後のプロセスからはその起動コマンドを取得できないためである。
    同期的に待機するため、イベントループ上から呼ぶ場合は別スレッドへ退避させる。
    """
    if not descendants:
        return []
    descriptions = {process.pid: describe(process) for process in descendants}
    return [descriptions[process.pid] for process in reclaim_processes(descendants, timeout=timeout)]


def reclaim_processes(targets: list[psutil.Process], *, timeout: float = RECLAIM_TIMEOUT_SECONDS) -> list[psutil.Process]:
    """列挙済みのプロセスへ終了要求と強制終了を順に送り、資源を保持したまま残った対象を返す。

    対象は呼び出し元が自ら起動したプロセスの子孫として列挙済みのものに限る。
    `psutil.Process`は列挙時の起動時刻を保持し、PIDが別のプロセスへ再利用された場合はシグナルを送らない。
    このため、残った対象をそのまま再度渡して回収を再試行できる。
    同期的に待機するため、イベントループ上から呼ぶ場合は別スレッドへ退避させる。
    """
    if not targets:
        return []
    for process in targets:
        with contextlib.suppress(psutil.Error, OSError):
            process.terminate()
    alive = _wait_until_released(targets, timeout=timeout)
    for process in alive:
        with contextlib.suppress(psutil.Error, OSError):
            process.kill()
    return _wait_until_released(alive, timeout=timeout)


def _wait_until_released(targets: list[psutil.Process], *, timeout: float) -> list[psutil.Process]:
    """対象が資源を解放するまで待ち、上限までに解放しなかった対象を返す。"""
    remaining = list(targets)
    deadline = time.monotonic() + timeout
    while remaining:
        remaining = [process for process in remaining if _holds_resources(process)]
        if not remaining or time.monotonic() >= deadline:
            break
        time.sleep(_POLL_INTERVAL_SECONDS)
    return remaining


def _holds_resources(process: psutil.Process) -> bool:
    """対象が資源を保持しているかを返す。

    終了済みで親による回収を待つだけの状態（zombie）は解放済みとして扱う。
    この状態はポート、ファイルロックおよびメモリーを保持せず、本モジュールが回収する対象に当たらない。
    起点プロセスの終了により親を失った子孫は、initが回収するまでの間だけこの状態になる。
    """
    try:
        # 列挙時と起動時刻が異なる同じPIDのプロセスは別の主体であり、対象の資源保持に数えない。
        return process.is_running() and process.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
    except psutil.Error, OSError:
        return False


def describe(process: psutil.Process) -> str:
    """PIDと起動コマンドで子孫プロセスを識別できる記述を返す。"""
    try:
        command = " ".join(process.cmdline())
    except psutil.Error, OSError:
        command = ""
    return f"{process.pid}: {command}" if command else str(process.pid)


def identity(pid: int) -> dict[str, int | float | str]:
    """引数や環境を含めず、PID再利用と親子関係を判別する識別情報を返す。"""
    result: dict[str, int | float | str] = {"pid": pid}
    try:
        process = psutil.Process(pid)
        result["created_at"] = process.create_time()
        result["ppid"] = process.ppid()
        result["status"] = process.status()
    except (psutil.Error, OSError) as error:
        result["unavailable"] = type(error).__name__
    return result


def log_residual(residual: list[str], *, context: str, retryable: bool = True) -> None:
    """回収できなかった子孫プロセスを診断へ残す。

    回収の失敗は委譲の失敗として扱わないため、呼び出し元の終了処理は継続する。
    `retryable`は、同じ停止操作の再実行で回収を再試行できるかを示す。
    """
    if residual:
        _LOG.warning("%s", residual_message(residual, context=context, retryable=retryable))


def residual_message(residual: list[str], *, context: str, retryable: bool = True) -> str:
    """回収できなかった対象と、診断を読む主体が次に取る操作を示す診断文を返す。"""
    if retryable:
        unresolved = (
            "「生成の停止を確認できない」がある場合は、一覧に無い子孫が残っている可能性があるため、"
            "同じ停止操作を再実行して回収を再試行し、残れば同じ手順で確かめる"
        )
    else:
        # 再試行を終えて解放済みのため、停止操作の再実行では回収されない。
        unresolved = (
            "所有は解放済みで停止操作を再実行しても回収されないため、"
            "`ps -o pid,ppid,lstart,args -e`などで一覧のPIDの後に起動した不要なプロセスを探して同じ手順で終了させる"
        )
    return (
        f"委譲先の子孫プロセスを回収できませんでした: context={context} residual={residual}。"
        "次の操作: 一覧のPIDと起動コマンドで対象を確かめ、不要なら終了させる"
        "（POSIXは`kill -KILL <PID>`、Windowsは`taskkill /PID <PID> /T /F`）。"
        f"{unresolved}"
    )
