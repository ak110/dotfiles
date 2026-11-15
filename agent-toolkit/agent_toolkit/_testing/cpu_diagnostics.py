"""CPU移行の候補CIで、超過の内訳と安全な固定入力の再計測を診断する。

最初の判定は変更せず、再計測はfixtureを再構築して報告を非公開で取得する。
再計測は副作用の範囲を確認した入力だけ、workerあたり3件・各10秒へ制限する。
"""

import gc
import json
import sys
import threading
import time
import warnings
from collections.abc import Callable, Generator
from importlib import _bootstrap
from typing import Any

import pytest
from _pytest.runner import runtestprotocol

lazy from agent_toolkit._testing import pytest_plugin as cpu_plugin

# 制御時計を使う試験から、診断用の時計を独立させる。
_PROCESS_CLOCK = time.process_time
_THREAD_CLOCK = time.thread_time
# CPythonのimport計時点は型stubへ公開されないため、moduleの実namespaceから扱う。
_IMPORT_NAMESPACE: Any = vars(_bootstrap)
_ORIGINAL_IMPORT: Callable[..., Any] = _IMPORT_NAMESPACE["_find_and_load"]
_LOCK = threading.RLock()
_LOCAL = threading.local()
_COUNTERS = {"gc_cpu": 0.0, "import_thread_cpu": 0.0, "import_attempts": 0.0}
_STATE: dict[str, Any] = {"session": None, "original_import": None, "gc_started": None, "rechecking": False, "rechecks": 0}
_PHASES = pytest.StashKey[dict[str, dict[str, float]]]()
_FAILED = pytest.StashKey[bool]()
_SAFE_RECHECK = {
    ("agent_toolkit/_atk/wi/effect_check_test.py", "test_public_effect_whole_item_confirmed_without_file"),
    (
        "agent_toolkit/_hooks/pretooluse/read_delivery_dispatch_test.py",
        "test_new_body_keeps_writing_standards_without_existing_edit_materials",
    ),
    (
        "agent_toolkit/_agents_server/manager_start_contract_test.py",
        "test_codex_usage_limit_without_connection_advances_to_next_engine",
    ),
    ("skills/write-plan/scripts/check_plan_file_migration_test.py", "test_rejects_each_migration_warning_for_new_creation"),
    ("agent_toolkit/_atk/wi/mutations/content_metadata_test.py", "test_edit_preserves_plan_file_when_only_body_changes"),
    ("agent_toolkit/_atk/wi/mutations/transitions_property_test.py", "test_dependency_order_matches_reference_model"),
    ("agent_toolkit/_hooks/pretooluse/process_loop_question_test.py", "test_question_is_not_denied_because_of_process_loop"),
    ("agent_toolkit/_plan/structure/references_test.py", "test_action_decisions_accept_all_declared_values"),
    ("agent_toolkit/_agents_server/launch_requests_routing_test.py", "test_public_lane_resolves_high_tier_candidates"),
    (
        "agent_toolkit/_hooks/pretooluse/read_delivery_resolution_test.py",
        "test_project_skill_missing_material_row_is_skipped_with_single_warning",
    ),
    ("agent_toolkit/_hooks/message_language_invariant_test.py", "test_hook_source_does_not_name_removed_agents_server_tools"),
    ("agent_toolkit/_hooks/pretooluse/block_notices_test.py", "test_block_notices_preserve_cause_and_next_action"),
    ("agent_toolkit/_hooks/pretooluse/operation_skills_test.py", "test_only_own_handoff_is_exempt"),
    ("agent_toolkit/_plan/structure/sections_test.py", "test_refactoring_table_rejects_empty_cells_and_column_mismatch"),
    ("agent_toolkit/_atk/wi/mutations/content_dry_run_test.py", "test_edit_dry_run_opening_and_unchanged_input"),
    ("agent_toolkit/_agents_server/launcher_record_evidence_test.py", "test_task_record_reaches_public_evidence"),
    ("agent_toolkit/_atk/git_sync_property_test.py", "test_pull_event_sequence_matches_reference_model"),
    ("agent_toolkit/_hooks/message_language_invariant_test.py", "test_hook_source_notice_texts_are_japanese"),
    ("skills/write-plan/scripts/check_plan_file_test.py", "test_accepts_relative_target_repo_matching_worktree"),
    ("agent_toolkit/_atk/serve/sessions_detail_test.py", "test_detail_returns_every_event_of_a_long_record"),
}


def _gc_event(phase: str, _info: dict[str, int]) -> None:
    if phase == "start":
        _STATE["gc_started"] = _PROCESS_CLOCK()
    elif _STATE["gc_started"] is not None:
        with _LOCK:
            _COUNTERS["gc_cpu"] += _PROCESS_CLOCK() - _STATE["gc_started"]
        _STATE["gc_started"] = None


def _import(*args: Any, **kwargs: Any) -> Any:
    depth = getattr(_LOCAL, "depth", 0)
    _LOCAL.depth = depth + 1
    started = _THREAD_CLOCK()
    try:
        return _ORIGINAL_IMPORT(*args, **kwargs)
    finally:
        _LOCAL.depth = depth
        if not depth:
            with _LOCK:
                _COUNTERS["import_thread_cpu"] += _THREAD_CLOCK() - started
                _COUNTERS["import_attempts"] += 1


def pytest_sessionstart(session: pytest.Session) -> None:
    """同じworker内の累計だけを観測し、nested sessionは所有し直さない。"""
    if _STATE["session"] is not None:
        return
    _STATE["session"] = session
    _STATE["original_import"] = _IMPORT_NAMESPACE["_find_and_load"]
    _IMPORT_NAMESPACE["_find_and_load"] = _import
    gc.callbacks.append(_gc_event)


def pytest_sessionfinish(session: pytest.Session) -> None:
    if _STATE["session"] is session:
        _IMPORT_NAMESPACE["_find_and_load"] = _STATE["original_import"]
        if _gc_event in gc.callbacks:
            gc.callbacks.remove(_gc_event)
        _STATE["session"] = None


def _snapshot() -> dict[str, float]:
    with _LOCK:
        return {**_COUNTERS, "process": _PROCESS_CLOCK(), "thread": _THREAD_CLOCK(), "modules": float(len(sys.modules))}


def _measure(item: pytest.Item, phase: str) -> Generator[None, object, object]:
    before = _snapshot()
    try:
        return (yield)
    finally:
        after = _snapshot()
        item.stash.setdefault(_PHASES, {})[phase] = {name: after[name] - value for name, value in before.items()}


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, object, object]:
    item.stash[_PHASES] = {}
    return (yield from _measure(item, "setup"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    return (yield from _measure(item, "call"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Generator[None, object, object]:
    return (yield from _measure(item, "teardown"))


def _summary(item: pytest.Item) -> dict[str, Any]:
    phases = item.stash.get(_PHASES, {})
    times = item.stash.get(cpu_plugin._CPU_TIMES, {})  # pylint: disable=protected-access
    return {
        "phases": phases,
        "total": {name: sum(phase[name] for phase in phases.values()) for name in _snapshot()},
        "policy_cpu": dict(times.items()),
        "gc_and_import_can_overlap": True,
        "gc_observer_active": _gc_event in gc.callbacks,
        "import_observer_active": _IMPORT_NAMESPACE["_find_and_load"] is _import,
    }


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if report.when == "teardown":
        failed = any(name == "CPU上限" for name, _ in report.sections)
        item.stash[_FAILED] = failed
        if failed and not _STATE["rechecking"]:
            report.sections.append(("CPU内訳", json.dumps(_summary(item), ensure_ascii=False)))
    return report


def _recheck(item: pytest.Item, nextitem: pytest.Item | None) -> dict[str, Any]:
    module, _, function = item.nodeid.removeprefix("agent-toolkit/").partition("::")
    key = (module, function.rsplit("::", 1)[-1].partition("[")[0])
    diagnostic: dict[str, Any] = {"nodeid": item.nodeid, "first": _summary(item), "recheck": None}
    if key in _SAFE_RECHECK and _STATE["rechecks"] < 3:
        # 最初のreportは送信済み。log=Falseで再計測のreportを合否・件数へ公開しない。
        saved_times = item.stash[cpu_plugin._CPU_TIMES]  # pylint: disable=protected-access
        saved_limit = item.stash[cpu_plugin._CPU_LIMIT]  # pylint: disable=protected-access
        saved_reports = item.stash[cpu_plugin._BROWSER_REPORTS].copy()  # pylint: disable=protected-access
        saved_markers = item.own_markers.copy()
        try:
            _STATE["rechecking"] = True
            _STATE["rechecks"] += 1
            item.stash[cpu_plugin._CPU_TIMES] = {}  # pylint: disable=protected-access
            item.stash[cpu_plugin._CPU_LIMIT] = (60.0, "診断再計測。合否へ使用しない")  # pylint: disable=protected-access
            item.add_marker(pytest.mark.timeout(10), append=False)
            reports = runtestprotocol(item, log=False, nextitem=nextitem)
            diagnostic["recheck"] = {**_summary(item), "outcomes": [report.outcome for report in reports]}
        except Exception as error:  # 診断の不成立で、公開済みの元の判定を置き換えない。
            diagnostic["recheck"] = {"error": f"{type(error).__name__}: {error}"}
        finally:
            item.stash[cpu_plugin._CPU_TIMES] = saved_times  # pylint: disable=protected-access
            item.stash[cpu_plugin._CPU_LIMIT] = saved_limit  # pylint: disable=protected-access
            item.stash[cpu_plugin._BROWSER_REPORTS] = saved_reports  # pylint: disable=protected-access
            item.own_markers[:] = saved_markers
            _STATE["rechecking"] = False
    return diagnostic


@pytest.hookimpl(wrapper=True, trylast=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None, object, object]:
    result = yield
    try:
        if item.stash.get(_FAILED, False) and not _STATE["rechecking"]:
            warnings.warn(
                "CPU再計測: " + json.dumps(_recheck(item, nextitem), ensure_ascii=False), pytest.PytestWarning, stacklevel=2
            )
    finally:
        # 成功した全itemへ診断の浮動小数点辞書を保持しない。
        if _PHASES in item.stash:
            del item.stash[_PHASES]
        if _FAILED in item.stash:
            del item.stash[_FAILED]
    return result
