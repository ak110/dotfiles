"""起動した外部コマンドを子孫プロセスごと回収する。

ラッパー、シェル、エージェントCLIは、自身の実行中に補助プロセスを起動する。
終了処理が直接の子だけを終了させると、これらの子孫は親を失ったまま生存し続ける。
`collect_descendants`と`reclaim_descendants`は、その残留をCLIのプロセスの終了に合わせて回収する。

`collect_descendants`はCLIのプロセスへ終了を要求する前に呼ぶ。
CLIのプロセスが終了すると子孫の親が変わり、そのPIDを起点に辿れなくなるためである。

対象の特定は起点PIDからの子孫関係だけを根拠とする。
プロセス名の部分一致とパターン一致で対象集合を広げない。
"""

import contextlib
import logging
import subprocess
import time
from collections.abc import Generator
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


def stop_process_tree(process: subprocess.Popen[Any]) -> list[str]:
    """所有する子を子孫ごと停止し、回収できなかったPIDと起動コマンドを返す。"""
    descendants = collect_descendants(process.pid)
    try:
        targets = [psutil.Process(process.pid), *descendants]
    except psutil.NoSuchProcess:
        targets = descendants
    residual = reclaim_descendants(targets)
    try:
        process.wait(timeout=0)
    except subprocess.TimeoutExpired:
        if not residual:
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
                log_residual(stop_process_tree(process), context="process_scope")
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
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error, OSError:
        return False


def describe(process: psutil.Process) -> str:
    """PIDと起動コマンドで子孫プロセスを識別できる記述を返す。"""
    try:
        command = " ".join(process.cmdline())
    except psutil.Error, OSError:
        command = ""
    return f"{process.pid}: {command}" if command else str(process.pid)


def log_residual(residual: list[str], *, context: str) -> None:
    """回収できなかった子孫プロセスを診断へ残す。

    回収の失敗は委譲の失敗として扱わないため、呼び出し元の終了処理は継続する。
    """
    if residual:
        _LOG.warning("委譲先の子孫プロセスを回収できませんでした: context=%s residual=%s", context, residual)
