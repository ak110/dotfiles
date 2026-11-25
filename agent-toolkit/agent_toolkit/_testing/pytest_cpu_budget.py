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
_CALL_CPU_BUDGET = pytest.StashKey[cpu_accounting.CpuBudget]()
_CALL_ONLY = pytest.StashKey[bool]()
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
            _CALL_CPU_BUDGET,
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
    budget_key = _CALL_CPU_BUDGET if phase == "call" and item.stash.get(_CALL_ONLY, False) else _CPU_BUDGET
    budget = item.stash.setdefault(budget_key, cpu_accounting.CpuBudget())
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


def _worker_times(owner: cpu_accounting.CpuBudget | None) -> dict[str, float]:
    return cpu_accounting.snapshot(owner) if owner is not None else {"worker": 0.0, "gc": 0.0, "import": 0.0}


def apply_report(item: pytest.Item, report: pytest.TestReport) -> None:
    if report.when == "teardown":
        item.stash[_CPU_ONLY_FAILURE] = False
        recorded = item.stash.get(_CPU_TIMES, {})
        times = {phase: recorded.get(phase, 0.0) for phase in ("setup", "call", "teardown")}
        limit, reason = item.stash.get(_CPU_LIMIT, (0.5, "通常上限"))
        call_only = item.stash.get(_CALL_ONLY, False)
        shared_workers = _worker_times(item.stash.get(_CPU_BUDGET, None))
        call_workers = _worker_times(item.stash.get(_CALL_CPU_BUDGET, None))
        worker_total = shared_workers["worker"] + call_workers["worker"]
        total = sum(times.values()) + worker_total
        scope_total = times["call"] + call_workers["worker"] if call_only else total
        process_start = item.stash.get(_CPU_PROCESS_STARTED, None)
        process_total = time.process_time() - process_start if process_start is not None else 0.0
        excluded = item.stash.get(_CPU_EXCLUSIONS, {})
        workers = call_workers if call_only else shared_workers
        deductions = {
            kind: sum(excluded.get(phase, {}).get(kind, 0.0) for phase in (("call",) if call_only else times)) + workers[kind]
            for kind in ("gc", "import")
        }
        budget = max(0.0, scope_total - sum(deductions.values()))
        gross_limit = item.stash.get(_CPU_GROSS_LIMIT, max(2.0, limit))
        report.user_properties.extend(
            [(f"cpu_{phase}_seconds", times.get(phase, 0.0)) for phase in ("setup", "call", "teardown")]
            + [
                ("cpu_total_seconds", total),
                ("cpu_worker_seconds", worker_total),
                ("cpu_non_target_seconds", max(0.0, total - scope_total)),
                ("cpu_limit_scope", "call" if call_only else "test"),
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
                f"CPU上限: {item.nodeid}: {times}, worker={worker_total:.6f}s, total={total:.6f}s, "
                f"process={process_total:.6f}s, excluded={deductions}, "
                f"measured={scope_total:.6f}s, budget={budget:.6f}s, limit={limit}s, gross_limit={gross_limit}s, "
                f"reason={reason}。"
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
    config.stash[_CPU_RETRY_CI] = bool(os.environ.get("CI"))
    config.addinivalue_line(
        "markers",
        "cpu_limit(seconds, reason, call_limit=None): 理由付き所有CPU上限。"
        "call_limitを指定すると判定CPUを試験本体だけで測り、全体上限はsecondsから決める",
    )


def add_options(parser: pytest.Parser) -> None:
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


def _valid_seconds(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 < value <= sys.float_info.max


def set_limits(config: pytest.Config, items: list[pytest.Item]) -> None:
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
        call_limit = None
        if marker is not None:
            if len(marker.args) != 1 or "reason" not in marker.kwargs or marker.kwargs.keys() - {"reason", "call_limit"}:
                raise pytest.UsageError(f"{item.nodeid}: cpu_limit(seconds, reason=..., call_limit=None) が必要")
            seconds, reason = marker.args[0], marker.kwargs["reason"]
            call_limit = marker.kwargs.get("call_limit")
        if not (_valid_seconds(seconds) and isinstance(reason, str) and reason.strip()):
            raise pytest.UsageError(f"{item.nodeid}: cpu_limitには正有限値と非空のreasonを指定してください")
        if call_limit is not None and (not _valid_seconds(call_limit) or call_limit > seconds):
            raise pytest.UsageError(f"{item.nodeid}: cpu_limitのcall_limitにはseconds以下の正有限値を指定してください")

        gross = float(seconds) + 1.5 if marker is not None else max(2.0, default_limit)
        if not math.isfinite(gross * scale):
            raise pytest.UsageError(
                f"{item.nodeid}: 係数適用後のCPU上限が有限値ではありません。"
                "--cpu-limit-scaleまたはcpu_limitを下げて再実行してください"
            )
        item.stash[_CALL_ONLY] = call_limit is not None
        item.stash[_CPU_LIMIT] = (float(seconds if call_limit is None else call_limit) * scale, reason)
        item.stash[_CPU_GROSS_LIMIT] = gross * scale
