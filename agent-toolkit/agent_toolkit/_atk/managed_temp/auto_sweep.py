from __future__ import annotations

import contextlib
import datetime
import io
import os
import pathlib
import subprocess
import sys
import threading

from agent_toolkit._atk import outcome
from agent_toolkit._atk.managed_temp import paths, sweep
from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._common import environment_usage, plugin_environment
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.windows_job import HIDDEN_DETACHED_CREATIONFLAGS

_DIAGNOSTICS_NAME = ".sweep/last-auto-sweep.log"


def schedule(now: datetime.datetime) -> None:
    try:
        due, low = sweep.automatic_sweep_status(now)
        if not due:
            return
        diagnostic = paths.state_root_path() / _DIAGNOSTICS_NAME
        _start_worker()
    except (OSError, ManagedTempError) as error:
        outcome.report_warning(
            f"managed-tempの自動掃引を起動できなかった: {error}",
            next_action="次のatk起動でも失敗する場合は、update-dotfilesで依存環境を確認してください",
        )
        return
    if low:
        outcome.report_warning(
            "一時rootを含むディスクの空きが下限を割ったため、自動掃引をバックグラウンドで開始した",
            next_action=f"空き不足が続く場合は {diagnostic} の回収結果と一時root内外の使用量を確認してください",
        )


def _start_worker() -> None:
    environment = os.environ.copy()
    root = str(pathlib.Path(__file__).resolve().parents[3])
    previous = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = root + (os.pathsep + previous if previous else "")
    # 子が親のパイプを保持するとCLI終了後も呼び出し元が待つ。  # pyfltr: allow-comment
    worker = subprocess.Popen(  # noqa: SIM115  # pylint: disable=consider-using-with
        [sys.executable, "-P", "-m", "agent_toolkit._atk.managed_temp.auto_sweep"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
        creationflags=HIDDEN_DETACHED_CREATIONFLAGS if os.name == "nt" else 0,
    )
    threading.Thread(target=worker.wait, daemon=True).start()


def _collect_diagnostics() -> str:
    stream = io.StringIO()
    try:
        result = sweep.sweep_managed_temp(now=datetime.datetime.now().astimezone())
    except Exception as error:  # noqa: BLE001  # CLIから切り離した自動掃引の例外も診断に残す
        result = sweep.SweepResult([], ((paths.temp_root_path(), str(error)),))
    with contextlib.redirect_stderr(stream):
        for path, reason in result.failed:
            outcome.report_warning(
                f"managed-tempの対象を自動削除できなかった: {path}: {reason}",
                next_action="表示された所有者や権限などの原因を除去し、次回の自動掃引で確認してください",
            )
        if result.pressure is not None:
            _report_space_pressure(result.pressure)
    return stream.getvalue()


def _run_worker() -> None:
    prefix = pathlib.Path(sys.prefix).resolve()
    shared = prefix.parent == (plugin_environment.runtime_data(os.environ) / "environments").resolve()
    with environment_usage.lock(prefix) if shared else contextlib.nullcontext():
        diagnostics = _collect_diagnostics()
        if diagnostics:
            with contextlib.suppress(OSError, ManagedTempError):
                atomic_write(paths.state_root() / _DIAGNOSTICS_NAME, diagnostics)


def _report_space_pressure(pressure: sweep.SpacePressure) -> None:
    gib = 1024**3
    if pressure.reclaimed_bytes:
        top = ", ".join(f"{name}={size / gib:.2f}GiB" for name, size in pressure.top_reclaimed)
        outcome.report_warning(
            f"managed-tempの空きが下限（容量の10%）を割ったため、使用中でない対象を古い順に回収した: "
            f"空き {pressure.free_before / gib:.1f}GiB→{pressure.free_after / gib:.1f}GiB"
            f"（回収 {pressure.reclaimed_bytes / gib:.2f}GiB）。回収量の上位: {top}",
            next_action="回収量の上位の対象を作成した作業が参照後に回収していたかを確かめると、再発を防げる",
        )
    if pressure.still_low:
        outcome.report_warning(
            f"一時rootを含むディスクの空きが下限（容量の10%）を割っており、自動掃引後も空きが不足している: "
            f"空き {pressure.free_after / gib:.1f}GiB / 容量 {pressure.total / gib:.1f}GiB",
            next_action="一時領域の保持理由・猶予・削除失敗と実際の利用を確認し、未使用の再生成可能な資源を個別に回収してください。登録worktreeや親セッションの保護は作成者の終了だけでは解除されない。一時root内外の使用量も確認してください",
        )


if __name__ == "__main__":
    _run_worker()
