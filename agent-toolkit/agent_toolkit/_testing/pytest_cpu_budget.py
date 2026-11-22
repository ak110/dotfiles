"""pytestの試験単位のCPU予算と超過の再確認を扱う。"""

import math
import os
import sys
import time
from collections.abc import Generator
from typing import Any

import pytest
from _pytest.runner import runtestprotocol

from agent_toolkit._testing import cpu_accounting

_CPU_TIMES = pytest.StashKey[dict[str, float]]()
_CPU_LIMIT = pytest.StashKey[tuple[float, str]]()
_CPU_EXCLUSIONS = pytest.StashKey[dict[str, dict[str, float]]]()
_CPU_GROSS_LIMIT = pytest.StashKey[float]()
_CPU_BUDGET = pytest.StashKey[cpu_accounting.CpuBudget]()
_CPU_PROCESS_STARTED = pytest.StashKey[float]()
_CPU_RETRY_CI = pytest.StashKey[bool]()
_CPU_RECORD_ONLY = pytest.StashKey[bool]()
_CPU_ONLY_FAILURE = pytest.StashKey[bool]()


def retry(item: pytest.Item, nextitem: pytest.Item | None, browser_reports: pytest.StashKey[Any]) -> bool | None:
    """CIのCPU単独超過だけを再確認し、未確定の初回失敗を集計へ送らない。"""
    if not item.config.stash.get(_CPU_RETRY_CI, False):
        return None
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    reports = runtestprotocol(item, log=False, nextitem=nextitem)
    initial = reports[-1]
    if item.stash.get(_CPU_ONLY_FAILURE, False) and all(report.passed for report in reports[:-1]):
        history = list(initial.user_properties)
        diagnostics = list(initial.sections)
        keys: list[pytest.StashKey[Any]] = [
            _CPU_TIMES,
            _CPU_EXCLUSIONS,
            _CPU_BUDGET,
            _CPU_PROCESS_STARTED,
            browser_reports,
        ]
        for key in keys:
            if key in item.stash:
                del item.stash[key]
        reports = runtestprotocol(item, log=False, nextitem=nextitem)
        final = reports[-1]
        final.user_properties.extend((f"cpu_first_attempt_{name}", value) for name, value in history if name.startswith("cpu_"))
        final.sections.extend(("CPU初回超過", text) for name, text in diagnostics if name == "CPU上限")
    for report in reports:
        item.ihook.pytest_runtest_logreport(report=report)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True


def measure(item: pytest.Item, phase: str) -> Generator[None, object, object]:
    budget = item.stash.setdefault(_CPU_BUDGET, cpu_accounting.CpuBudget())
    with cpu_accounting.activate(budget):
        excluded = cpu_accounting.main_snapshot()
        if _CPU_PROCESS_STARTED not in item.stash:
            item.stash[_CPU_PROCESS_STARTED] = time.process_time()
        started = time.thread_time()
        try:
            return (yield)
        finally:
            finished = time.thread_time()
            after = cpu_accounting.main_snapshot()
            item.stash.setdefault(_CPU_TIMES, {})[phase] = finished - started
            item.stash.setdefault(_CPU_EXCLUSIONS, {})[phase] = {key: after[key] - excluded[key] for key in ("gc", "import")}


def apply_report(item: pytest.Item, report: pytest.TestReport) -> None:
    """完了時の計測値とCPU上限の判定を報告へ加える。"""
    if report.when == "teardown":
        item.stash[_CPU_ONLY_FAILURE] = False
        recorded = item.stash.get(_CPU_TIMES, {})
        times = {phase: recorded.get(phase, 0.0) for phase in ("setup", "call", "teardown")}
        limit, reason = item.stash.get(_CPU_LIMIT, (0.5, "通常上限"))
        owner = item.stash.get(_CPU_BUDGET, None)
        # phase間も所有workerは動き得るため、完了reportで試験の累計を1回だけ加える。
        workers = cpu_accounting.snapshot(owner) if owner is not None else {"worker": 0.0, "gc": 0.0, "import": 0.0}
        total = sum(times.values()) + workers["worker"]
        process_start = item.stash.get(_CPU_PROCESS_STARTED, None)
        process_total = time.process_time() - process_start if process_start is not None else 0.0
        excluded = item.stash.get(_CPU_EXCLUSIONS, {})
        deductions = {kind: sum(row.get(kind, 0.0) for row in excluded.values()) + workers[kind] for kind in ("gc", "import")}
        budget = max(0.0, total - sum(deductions.values()))
        gross_limit = item.stash.get(_CPU_GROSS_LIMIT, max(2.0, limit))
        report.user_properties.extend(
            [(f"cpu_{phase}_seconds", times.get(phase, 0.0)) for phase in ("setup", "call", "teardown")]
            + [
                ("cpu_total_seconds", total),
                ("cpu_worker_seconds", workers["worker"]),
                ("cpu_process_seconds", process_total),
                ("cpu_unattributed_seconds", max(0.0, process_total - total)),
                ("cpu_excluded_gc_seconds", deductions["gc"]),
                ("cpu_excluded_import_seconds", deductions["import"]),
                ("cpu_budget_seconds", budget),
                ("cpu_limit_seconds", limit),
                ("cpu_gross_limit_seconds", gross_limit),
                ("cpu_limit_reason", reason),
            ]
        )
        if budget >= limit or total >= gross_limit:
            diagnostic = (
                f"CPU上限: {item.nodeid}: {times}, worker={workers['worker']:.6f}s, total={total:.6f}s, "
                f"process={process_total:.6f}s, excluded={deductions}, "
                f"budget={budget:.6f}s, limit={limit}s, gross_limit={gross_limit}s, reason={reason}。"
                "不要な処理を削減し、必要な実境界には理由付きcpu_limitを指定してください"
            )
            report.sections.append(("CPU上限", diagnostic))
            if item.stash.get(_CPU_RECORD_ONLY, False):
                item.warn(pytest.PytestWarning(diagnostic))
            elif report.passed:
                item.stash[_CPU_ONLY_FAILURE] = True
                report.outcome = "failed"
                report.longrepr = diagnostic


def configure(config: pytest.Config) -> None:
    """CPUの再確認設定とマーカーを登録する。"""
    config.stash[_CPU_RETRY_CI] = bool(os.environ.get("CI"))
    config.addinivalue_line("markers", "cpu_limit(seconds, reason): 理由付き所有CPU上限")


def add_options(parser: pytest.Parser) -> None:
    """所有CPUの判定を指定するオプションを登録する。"""
    parser.addini("cpu_limit", "試験が所有するCPUの上限（秒）", default="0.5")
    parser.addoption(
        "--cpu-limit-scale",
        type=float,
        default=1.0,
        help="試験が所有するCPUのnetとgrossの上限に適用する係数",
    )
    parser.addoption(
        "--cpu-limit-record-only",
        action="store_true",
        help="CPU上限の超過を警告とJUnitへ記録するだけにし、試験の失敗と再試行にしない",
    )


def set_limits(config: pytest.Config, items: list[pytest.Item]) -> None:
    """収集した各試験に計測の上限を割り当てる。"""
    try:
        configured_limit = config.getini("cpu_limit")
        if isinstance(configured_limit, bool):
            raise ValueError("真偽値はCPU秒数でない")
        default_limit = float(configured_limit)
    except (TypeError, ValueError, OverflowError) as error:
        raise pytest.UsageError("cpu_limit設定には正の有限秒数を指定してください") from error
    if not math.isfinite(default_limit) or default_limit <= 0:
        raise pytest.UsageError("cpu_limit設定には正の有限秒数を指定してください")
    scale = config.getoption("cpu_limit_scale")
    if not isinstance(scale, float) or not math.isfinite(scale) or scale <= 0:
        raise pytest.UsageError("--cpu-limit-scaleには正の有限値を指定してください")
    record_only = bool(config.getoption("cpu_limit_record_only"))
    for item in items:
        item.stash[_CPU_RECORD_ONLY] = record_only
        marker = item.get_closest_marker("cpu_limit")
        seconds: Any = default_limit
        reason = "通常上限"
        if marker is not None:
            if len(marker.args) != 1 or set(marker.kwargs) != {"reason"}:
                raise pytest.UsageError(f"{item.nodeid}: cpu_limit(seconds, reason=...) が必要")
            seconds, reason = marker.args[0], marker.kwargs["reason"]
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or abs(seconds) > sys.float_info.max
            or not math.isfinite(seconds)
            or seconds <= 0
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise pytest.UsageError(f"{item.nodeid}: cpu_limitには正有限値と非空のreasonを指定してください")
        # netから控除する共有初期化費用には、通常0.5/gross2と同じ余裕を重い試験にも残す。
        gross = float(seconds) + 1.5 if marker is not None else max(2.0, default_limit)
        if not math.isfinite(gross * scale):
            raise pytest.UsageError(f"{item.nodeid}: 係数適用後のCPU上限が有限値ではありません")
        item.stash[_CPU_LIMIT] = (float(seconds) * scale, reason)
        item.stash[_CPU_GROSS_LIMIT] = gross * scale
