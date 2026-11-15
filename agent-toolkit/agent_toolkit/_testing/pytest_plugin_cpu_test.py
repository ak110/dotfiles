"""共通pytestのCPU上限を確認する。"""

import collections
import concurrent.futures
import gc
import importlib
import sys
import threading
import types
from typing import Any, Literal, cast

import pytest

from agent_toolkit._testing import cpu_accounting, pytest_plugin

# pylint: disable=protected-access


@pytest.mark.parametrize("elapsed", [0.499, 0.5, 0.501])
@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_cpu_clock_boundary(monkeypatch: pytest.MonkeyPatch, elapsed: float, phase: str) -> None:
    """制御時計で段階計測を確認し、実時間に依存させない。"""

    class Item:
        stash = pytest.Stash()
        nodeid = "cpu_test.py::test_cpu"

    item = cast(pytest.Item, Item())
    clock = iter([0.0, elapsed])
    with monkeypatch.context() as patch:
        patch.setattr(pytest_plugin.time, "thread_time", lambda: next(clock))
        patch.setattr(cpu_accounting, "main_snapshot", lambda: {"gc": 0.0, "import": 0.0})
        hook = getattr(pytest_plugin, f"pytest_runtest_{phase}")(item)
        next(hook)
        with pytest.raises(StopIteration):
            hook.send(None)
    report = pytest.TestReport(item.nodeid, ("cpu_test.py", 0, "test_cpu"), {}, "passed", None, "teardown")
    reporting = pytest_plugin.pytest_runtest_makereport(item, cast(Any, None))
    next(reporting)
    with pytest.raises(StopIteration):
        reporting.send(report)
    assert report.failed == (elapsed >= 0.5)


@pytest.mark.parametrize("mode", ["foreign", "thread", "pool", "exception", "nested", "spanning"])
def test_cpu_budget_keeps_worker_ownership_across_phases(monkeypatch, mode) -> None:
    """無関係な仕事の誤課金、既存poolの計上漏れ、二重計上、段階をまたぐ計上漏れを検出する。"""
    item = cast(pytest.Item, types.SimpleNamespace(stash=pytest.Stash(), nodeid="cpu_test.py::test_cpu"))
    clocks: collections.defaultdict[int, float] = collections.defaultdict(float)
    main = threading.get_ident()
    ready, proceed = threading.Event(), threading.Event()
    native_clocks: dict[int, int] = {}
    future: concurrent.futures.Future[None] | None = None

    def work():
        ident = threading.get_ident()
        native_clocks[threading.get_native_id()] = ident
        clocks[ident] += 0.3 if mode in {"nested", "spanning"} else 0.6
        if mode == "spanning":
            ready.set()
            assert proceed.wait(10)
            clocks[ident] += 0.3
        elif mode == "nested":

            def child_work():
                clocks[threading.get_ident()] += 0.3

            child = threading.Thread(target=child_work)
            child.start()
            child.join(10)
            assert not child.is_alive()
        elif mode == "exception":
            raise ValueError("仕事は失敗してもCPUを所有する")

    def foreign_work():
        assert proceed.wait(10)
        work()

    def thread_clocks():
        return [
            types.SimpleNamespace(id=native, user_time=clocks[ident], system_time=0.0)
            for native, ident in native_clocks.items()
        ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(lambda: None).result(timeout=10)  # 既存workerへの投入も所有する。
        foreign = threading.Thread(target=foreign_work) if mode == "foreign" else None
        if foreign is not None:
            token = cpu_accounting._budget.set(None)
            try:
                foreign.start()
            finally:
                cpu_accounting._budget.reset(token)
        automatic = gc.isenabled()
        gc.disable()
        try:
            with monkeypatch.context() as patch:
                patch.setattr(cpu_accounting, "_thread_cpu", lambda: clocks[threading.get_ident()])
                patch.setattr(pytest_plugin.time, "thread_time", lambda: clocks[threading.get_ident()])
                patch.setattr(pytest_plugin.time, "process_time", lambda: sum(clocks.values()))
                patch.setattr(cpu_accounting, "_process_threads", thread_clocks)
                hook = pytest_plugin.pytest_runtest_setup(item)
                next(hook)
                clocks[main] += 0.01
                if foreign is not None:
                    proceed.set()
                    foreign.join(10)
                    assert not foreign.is_alive()
                elif mode in {"pool", "exception", "spanning"}:
                    future = executor.submit(work)
                    if mode == "spanning":
                        assert ready.wait(10)
                    elif mode == "exception":
                        with pytest.raises(ValueError):
                            future.result(timeout=10)
                    else:
                        future.result(timeout=10)
                else:
                    thread = threading.Thread(target=work)
                    thread.start()
                    thread.join(10)
                    assert not thread.is_alive()
                with pytest.raises(StopIteration):
                    hook.send(None)
                if mode == "spanning":
                    assert future is not None
                    assert cpu_accounting.snapshot(item.stash[pytest_plugin._CPU_BUDGET])["worker"] == pytest.approx(0.3)
                    # report生成前のphase間の仕事も、試験自身の累計から失わない。
                    proceed.set()
                    future.result(timeout=10)
                    hook = pytest_plugin.pytest_runtest_call(item)
                    next(hook)
                    with pytest.raises(StopIteration):
                        hook.send(None)
                report = pytest.TestReport(item.nodeid, ("cpu_test.py", 0, "test_cpu"), {}, "passed", None, "teardown")
                reporting = pytest_plugin.pytest_runtest_makereport(item, cast(Any, None))
                next(reporting)
                with pytest.raises(StopIteration):
                    reporting.send(report)
                assert report.failed is (mode != "foreign")
                properties = dict(report.user_properties)
                assert properties["cpu_total_seconds"] == pytest.approx(0.01 if mode == "foreign" else 0.61)
                assert properties["cpu_process_seconds"] == pytest.approx(0.61)
                assert properties["cpu_unattributed_seconds"] == pytest.approx(0.6 if mode == "foreign" else 0.0)
        finally:
            proceed.set()
            if automatic:
                gc.enable()


@pytest.mark.parametrize("outcome", ["passed", "failed", "skipped"])
@pytest.mark.parametrize("times", [{"setup": 0.125, "call": 0.125, "teardown": 0.25}, {"call": 0.125}])
def test_cpu_report_preserves_outcomes_and_records_total(
    outcome: Literal["passed", "failed", "skipped"], times: dict[str, float]
) -> None:
    """合算で境界を検出し、元の失敗・skipと段階値をreportへ残す。"""

    class Item:
        def __init__(self) -> None:
            self.stash = pytest.Stash()
            self.nodeid = "cpu_test.py::test_cpu"

    for recorded in (times, {}):
        item = cast(pytest.Item, Item())
        item.stash[pytest_plugin._CPU_TIMES] = recorded
        original = "元の診断" if outcome == "failed" else ("cpu_test.py", 1, "skip理由") if outcome == "skipped" else None
        report = pytest.TestReport(item.nodeid, ("cpu_test.py", 0, "test_cpu"), {}, outcome, original, "teardown")
        hook = pytest_plugin.pytest_runtest_makereport(item, cast(Any, None))
        next(hook)
        with pytest.raises(StopIteration):
            hook.send(report)
        expected_failure = bool(recorded) and sum(recorded.values()) >= 0.5
        assert report.outcome == ("failed" if outcome == "passed" and expected_failure else outcome)
        if outcome != "passed":
            assert report.longrepr == original
        properties = dict(report.user_properties)
        assert properties["cpu_total_seconds"] == sum(recorded.values())
        assert properties["cpu_limit_seconds"] == 0.5
        assert any(name == "CPU上限" for name, _ in report.sections) == expected_failure


@pytest.mark.parametrize(
    "marker",
    [
        pytest.mark.cpu_limit(True, reason="理由"),
        pytest.mark.cpu_limit("1", reason="理由"),
        pytest.mark.cpu_limit(float("nan"), reason="理由"),
        pytest.mark.cpu_limit(float("inf"), reason="理由"),
        pytest.mark.cpu_limit(0, reason="理由"),
        pytest.mark.cpu_limit(-1, reason="理由"),
        pytest.mark.cpu_limit(1),
        pytest.mark.cpu_limit(1, reason=""),
        pytest.mark.cpu_limit(1, reason="  "),
        pytest.mark.cpu_limit(1, reason=None),
        pytest.mark.cpu_limit(1, 2, reason="理由"),
    ],
)
def test_cpu_limit_rejects_invalid_declaration_at_collection(
    request: pytest.FixtureRequest, marker: pytest.MarkDecorator
) -> None:
    """不正特例は本体を実行する前に拒否する。"""
    assert request.node.parent is not None
    item = pytest.Function.from_parent(request.node.parent, name="cpu_probe", callobj=lambda: None)
    item.add_marker(marker)
    with pytest.raises(pytest.UsageError, match="cpu_limit"):
        pytest_plugin.pytest_collection_modifyitems(request.config, [item])


@pytest.mark.parametrize("value", ["invalid", "0", "-1", "nan", "inf", True])
def test_cpu_limit_rejects_invalid_default(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, value: str | bool
) -> None:
    """通常設定の不正は特例の有無や試験集合によらず収集時に拒否する。"""
    with monkeypatch.context() as patch:
        patch.setattr(request.config, "getini", lambda _name: value)
        with pytest.raises(pytest.UsageError, match="cpu_limit設定"):
            pytest_plugin.pytest_collection_modifyitems(request.config, [])


@pytest.mark.parametrize(
    ("gross", "gc_cpu", "import_cpu", "gross_limit", "failed"),
    [(0.8, 0.2, 0.2, 2.0, False), (1.0, 0.25, 0.25, 2.0, True), (2.1, 1.8, 0.1, 2.0, True), (2.6, 1.5, 0.0, 2.5, True)],
)
def test_exclusions_do_not_hide_budget_or_gross_overrun(gross, gc_cpu, import_cpu, gross_limit, failed) -> None:
    """区間和を控除した予算とgrossの両上限を守り、原計測を失わない。"""
    item = cast(pytest.Item, types.SimpleNamespace(stash=pytest.Stash(), nodeid="cpu_test.py::test_cpu"))
    item.stash[pytest_plugin._CPU_TIMES] = {"call": gross}
    item.stash[pytest_plugin._CPU_EXCLUSIONS] = {"call": {"gc": gc_cpu, "import": import_cpu}}
    item.stash[pytest_plugin._CPU_GROSS_LIMIT] = gross_limit
    report = pytest.TestReport(item.nodeid, ("cpu_test.py", 0, "test_cpu"), {}, "passed", None, "teardown")
    hook = pytest_plugin.pytest_runtest_makereport(item, cast(Any, None))
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(report)
    assert report.failed is failed
    properties = dict(report.user_properties)
    assert properties["cpu_total_seconds"] == gross
    assert properties["cpu_budget_seconds"] == pytest.approx(gross - gc_cpu - import_cpu)
    assert properties["cpu_excluded_gc_seconds"] == gc_cpu
    assert properties["cpu_excluded_import_seconds"] == import_cpu


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("initial", {"gc": 0.2, "import": 0.3}),
        ("explicit", {"gc": 0.0, "import": 0.0}),
        ("young", {"gc": 0.0, "import": 0.0}),
        ("explicit-import", {"gc": 0.0, "import": 0.3}),
        ("young-import", {"gc": 0.0, "import": 0.3}),
        ("worker-initial", {"gc": 0.2, "import": 0.0}),
        ("worker-explicit", {"gc": 0.0, "import": 0.0}),
        ("worker-young", {"gc": 0.0, "import": 0.0}),
        ("foreign-gc-import", {"gc": 0.2, "import": 0.3}),
    ],
)
def test_accounting_joins_import_gc_and_keeps_owned_collection(monkeypatch, mode, expected) -> None:
    """公開import/collectとGC通知へ制御区間を与え、重複控除とDUTのGC費用消失を検出する。"""
    before = cpu_accounting.snapshot()
    clock: collections.defaultdict[int, float] = collections.defaultdict(float)
    worker = mode.startswith("worker-")
    operation = mode.removeprefix("worker-")
    name = "cpu_accounting_controlled_probe"
    original_import = cpu_accounting._state.original_import

    def collection(*_args, **_kwargs):
        info = {"generation": 0 if operation.startswith("young") else 2, "collected": 0, "uncollectable": 0}
        for callback in gc.callbacks:
            callback("start", info)
        clock[threading.get_ident()] += 0.2
        for callback in gc.callbacks:
            callback("stop", info)
        return 0

    def importing(module_name, *args, **kwargs):
        if module_name != name:
            return original_import(module_name, *args, **kwargs)
        if operation == "explicit-import":
            gc.collect()
        elif operation == "foreign-gc-import":
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(collection).result(timeout=10)
        else:
            collection()
        clock[threading.get_ident()] += 0.3
        module = types.ModuleType(name)
        sys.modules[name] = module
        return module

    def execute():
        if operation in {"initial", "explicit-import", "young-import", "foreign-gc-import"}:
            importlib.import_module(name)
        elif operation == "explicit":
            gc.collect()
        else:
            collection()

    automatic = gc.isenabled()
    # 制御時計の値を実際のGCの観測値にしないよう、自動GCを止める。
    gc.disable()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(cpu_accounting, "_thread_cpu", lambda: clock[threading.get_ident()])
            patch.setattr(cpu_accounting._state, "original_import", importing)
            patch.setattr(cpu_accounting._state, "original_collect", collection)
            if worker:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(execute).result(timeout=10)
            else:
                execute()
        after = cpu_accounting.snapshot()
        assert {key: after[key] - before[key] for key in expected} == pytest.approx(expected)
    finally:
        if automatic:
            gc.enable()
        sys.modules.pop(name, None)
