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
"""

import contextlib
import dataclasses
import logging
import os
import subprocess
import sys
import time
from collections.abc import Generator, Iterable
from typing import Any

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
    root: psutil.Process,
    *,
    include_root: bool = True,
    known: Iterable[psutil.Process] = (),
    timeout: float = RECLAIM_TIMEOUT_SECONDS,
) -> TreeStopResult:
    """`root`の子孫（`include_root`なら`root`自身も）の生成を止め、終了させて回収する。

    Windowsでは一時停止した全対象を強制終了する（一時停止中でも終了できる）。
    POSIXでは全対象へSIGTERMを送ってから再開し、正常終了の機会を保つ。
    猶予中に増えた子孫にもSIGTERMを送り、期限で再び一時停止してからSIGKILLを送る。
    `known`は呼び出し元が既に所有する対象であり、同じ扱いで終了させる。
    途中で例外が起きても、一時停止した対象を残さないよう所有対象を強制終了してから例外を伝播させる。
    同期的に待機するため、イベントループ上から呼ぶ場合は別スレッドへ退避させる。
    """
    tree = _OwnedTree(root, include_root=include_root, known=known)
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
        finished = True
    finally:
        if not finished:
            for process in tree.targets():
                with contextlib.suppress(psutil.Error, OSError):
                    process.kill()
    return TreeStopResult(tree.targets(), residual, tree.unresolved, descriptions)


class _OwnedTree:
    """`stop_tree`が所有する終了対象と、子孫を辿る起点の集合。"""

    def __init__(self, root: psutil.Process, *, include_root: bool, known: Iterable[psutil.Process]) -> None:
        self._anchors: dict[tuple[int, float], psutil.Process] = {}
        self._owned: dict[tuple[int, float], psutil.Process] = {}
        self.suspended: set[tuple[int, float]] = set()
        self.unresolved: list[str] = []
        root_key = _key(root)
        if root_key is not None:
            if include_root and root.pid != os.getpid():
                self._owned[root_key] = root
            else:
                self._anchors[root_key] = root
        self.add(known)

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
                    self._unresolve(f"pid {process.pid} を一時停止できない: {type(error).__name__}: {error}")
            if not self.scan():
                return
        self._unresolve(f"{_FREEZE_MAX_ROUNDS}回の一時停止と列挙の後も子孫が増え続けた")

    def _unresolve(self, reason: str) -> None:
        # POSIXでは終了猶予の前後で2回凍結するため、同じ理由を重複して報告しない。
        if reason not in self.unresolved:
            self.unresolved.append(reason)

    def scan(self) -> list[psutil.Process]:
        """所有対象と起点の子孫のうち未所有の対象を所有に加えて返す。列挙の失敗は未確定として記録する。"""
        try:
            found = _scan_descendants([*self._anchors.values(), *self._owned.values()])
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


def _scan_descendants(parents: list[psutil.Process]) -> list[psutil.Process]:
    """`parents`の子孫を、1回のプロセス一覧から親PIDを辿って返す。

    終了済みの`parents`も親として辿る。Windowsでは親の終了後も子の親PIDが残るため、親を失った子孫を見つけられる。
    子の起動時刻が親より前の場合と、親PIDを別のプロセスが再利用している場合のその新しいプロセスの子は含めない。
    """
    entries: list[tuple[psutil.Process, int]] = []
    for process in psutil.process_iter(["ppid"]):
        ppid = process.info.get("ppid")
        if isinstance(ppid, int):
            entries.append((process, ppid))
    holders = {process.pid: process for process, _ in entries}
    created: dict[int, float] = {}
    for parent in parents:
        with contextlib.suppress(psutil.Error, OSError):
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
            next_pending.add(process.pid)
        pending = next_pending
    return found


def stop_process_tree(process: subprocess.Popen[Any]) -> list[str]:
    """所有する子を子孫ごと停止し、回収できなかったPIDと起動コマンドを返す。"""
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
    if process.poll() is None:
        residual.append(f"{process.pid}: {process.args!r}")
    return residual


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
        try:
            if process.poll() is None:
                try:
                    log_residual(stop_process_tree(process), context="process_scope")
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=RECLAIM_TIMEOUT_SECONDS)
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
            residual = stop_process_tree(process)
            log_residual(residual, context="run_with_timeout")
            # 残存がパイプを保持していても、元の時間上限の報告へ戻る。
            if not residual:
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


def collect_descendants(pid: int | None) -> list[psutil.Process]:
    """`pid`を起点とする子孫プロセスを列挙する。

    起点プロセスが存在しない場合と、権限などで列挙できない場合は空リストを返す。
    回収は委譲の付随処理であり、列挙できないことを委譲の失敗として扱わない。
    """
    if pid is None:
        return []
    try:
        return psutil.Process(pid).children(recursive=True)
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


def log_residual(residual: list[str], *, context: str) -> None:
    """回収できなかった子孫プロセスを診断へ残す。

    回収の失敗は委譲の失敗として扱わないため、呼び出し元の終了処理は継続する。
    """
    if residual:
        _LOG.warning("委譲先の子孫プロセスを回収できませんでした: context=%s residual=%s", context, residual)
