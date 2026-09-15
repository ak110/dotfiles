"""委譲先プロセスが起動した子孫プロセスを回収する。

委譲先CLIは自身の実行中にMCPサーバーなどの子プロセスを起動する。
終端処理が委譲先プロセスだけを終了させると、これらの子孫は親を失ったまま生存し続ける。
`collect_descendants`と`reclaim_descendants`は、その残留を委譲先セッションの終端に合わせて回収する。

`collect_descendants`は委譲先プロセスへ終了を要求する前に呼ぶ。
委譲先プロセスが終了すると子孫の親が変わり、当該PIDを起点に辿れなくなるためである。

対象の特定は起点PIDからの子孫関係だけを根拠とする。
プロセス名の部分一致とパターン一致で対象集合を広げない。
"""

import contextlib
import logging
import time

import psutil

_LOG = logging.getLogger(__name__)

RECLAIM_TIMEOUT_SECONDS = 5.0
"""終了要求と強制終了のそれぞれで子孫の終了を待つ時間（秒）。

委譲先が起動する子孫はMCPサーバーなどの短命な補助プロセスであり、
終了要求の到達から資源の解放までに要する時間は秒未満である。
既存のCodex App Serverの終了待機と同じ値を用い、委譲先セッションの終端が延びる上限をそろえる。
"""

_POLL_INTERVAL_SECONDS = 0.05
"""子孫の終了を確認する間隔（秒）。"""


def collect_descendants(pid: int | None) -> list[psutil.Process]:
    """`pid`を起点とする子孫プロセスを列挙する。

    起点プロセスが存在しない場合と、権限などで列挙できない場合は空リストを返す。
    回収は委譲の付随処理であり、列挙できないことを委譲の失敗として扱わない。
    """
    if pid is None:
        return []
    try:
        return psutil.Process(pid).children(recursive=True)
    except (psutil.Error, OSError):
        return []


def reclaim_descendants(descendants: list[psutil.Process], *, timeout: float = RECLAIM_TIMEOUT_SECONDS) -> list[str]:
    """列挙済みの子孫プロセスを終了させ、回収できなかった対象の記述を返す。

    記述はPIDと起動コマンドで構成する。起動コマンドは終了要求より前に取得する。
    終了後のプロセスからは当該コマンドを取得できないためである。
    同期的に待機するため、イベントループ上から呼ぶ場合は別スレッドへ退避させる。
    """
    if not descendants:
        return []
    descriptions = {process.pid: _describe(process) for process in descendants}
    for process in descendants:
        with contextlib.suppress(psutil.Error, OSError):
            process.terminate()
    alive = _wait_until_released(descendants, timeout=timeout)
    for process in alive:
        with contextlib.suppress(psutil.Error, OSError):
            process.kill()
    alive = _wait_until_released(alive, timeout=timeout)
    return [descriptions[process.pid] for process in alive]


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
    当該状態はポート、ファイルロック及びメモリーを保持せず、本モジュールが回収する対象に当たらない。
    起点プロセスの終了により親を失った子孫は、initが回収するまでの間だけ当該状態を取る。
    """
    try:
        return process.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, OSError):
        return False


def _describe(process: psutil.Process) -> str:
    """PIDと起動コマンドで子孫プロセスを識別できる記述を返す。"""
    try:
        command = " ".join(process.cmdline())
    except (psutil.Error, OSError):
        command = ""
    return f"{process.pid}: {command}" if command else str(process.pid)


def log_residual(residual: list[str], *, context: str) -> None:
    """回収できなかった子孫プロセスを診断へ残す。

    回収の失敗は委譲の失敗として扱わないため、呼び出し元の終端処理は継続する。
    """
    if residual:
        _LOG.warning("委譲先の子孫プロセスを回収できませんでした: context=%s residual=%s", context, residual)
