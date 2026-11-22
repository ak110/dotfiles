import collections
import concurrent.futures
import gc
import importlib
import json
import pathlib
import sys
import threading
import types
import xml.etree.ElementTree as ET
from typing import Any, Literal, cast

import pytest

from agent_toolkit._testing import cpu_accounting, pytest_cpu_budget, pytest_plugin
from agent_toolkit._testing.pytest_plugin_support import _run_child_pytest

# pylint: disable=protected-access


@pytest.mark.parametrize(
    ("ci", "workers", "phase"),
    [(False, 0, "call"), (True, 0, "call"), (True, 2, "setup"), (True, 2, "call"), (True, 2, "teardown")],
)
def test_cpu_retry_keeps_functional_failures_and_attempt_evidence(
    tmp_path: pathlib.Path, monkeypatch, ci, workers, phase
) -> None:
    """公開protocolでCPUだけの再試行・機能失敗維持・回数上限を検出する。"""
    monkeypatch.setenv("CI", "1" if ci else "")
    (tmp_path / "conftest.py").write_text(
        "import json, pathlib, pytest\n"
        "from agent_toolkit._testing import pytest_cpu_budget as p\n"
        "attempts = {}\n"
        "@pytest.hookimpl(wrapper=True, tryfirst=True)\n"
        "def pytest_runtest_makereport(item, call):\n"
        "    if call.when == 'setup':\n"
        "        attempts[item.name] = attempts.get(item.name, 0) + 1\n"
        f"        path = pathlib.Path({str(tmp_path)!r}) / (item.name + '.json')\n"
        "        path.write_text(json.dumps(attempts[item.name]), encoding='utf-8')\n"
        "    if call.when == 'teardown':\n"
        "        recovered = item.name == 'test_recovers' and attempts[item.name] == 2\n"
        "        cpu = 0.499 if item.name == 'test_before' or recovered else 0.501\n"
        f"        item.stash[p._CPU_TIMES] = {{{phase!r}: cpu}}\n"
        "        item.stash[p._CPU_EXCLUSIONS] = {}\n"
        "        item.stash[p._CPU_BUDGET] = p.cpu_accounting.CpuBudget()\n"
        "    report = yield\n"
        "    return report\n",
        encoding="utf-8",
    )
    (tmp_path / "probe_test.py").write_text(
        "def test_before(): pass\n"
        "def test_recovers(): pass\n"
        "def test_repeats(): pass\n"
        "def test_functional(): assert False, 'functional failure'\n",
        encoding="utf-8",
    )
    arguments = ["-p", "xdist.plugin", "-n", str(workers)] if workers else []
    result = _run_child_pytest(tmp_path, *arguments, "-rA", "--junitxml=result.xml", "probe_test.py")
    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout + result.stderr
    expected = {
        "test_before": 1,
        "test_recovers": 2 if ci else 1,
        "test_repeats": 2 if ci else 1,
        "test_functional": 1,
    }
    assert {name: json.loads((tmp_path / f"{name}.json").read_text(encoding="utf-8")) for name in expected} == expected
    assert "functional failure" in result.stdout
    xml = (tmp_path / "result.xml").read_text(encoding="utf-8")
    cases = {case.get("name"): case for case in ET.fromstring(xml).iter("testcase")}
    assert cases["test_repeats"].find("error") is not None
    assert (cases["test_recovers"].find("error") is None) is ci
    for name in ("test_recovers", "test_repeats"):
        properties = {prop.attrib["name"]: prop.attrib["value"] for prop in cases[name].findall("properties/property")}
        assert float(properties["cpu_budget_seconds"]) == pytest.approx(0.499 if ci and name == "test_recovers" else 0.501)
        assert float(properties["cpu_limit_seconds"]) == 0.5
        if ci:
            assert float(properties["cpu_first_attempt_cpu_budget_seconds"]) == pytest.approx(0.501)
            assert float(properties["cpu_first_attempt_cpu_limit_seconds"]) == 0.5
        else:
            assert "cpu_first_attempt_cpu_budget_seconds" not in properties


def test_cpu_limit_scale_adjusts_net_and_gross_without_disabling_detection(tmp_path: pathlib.Path) -> None:
    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "from agent_toolkit._testing import pytest_cpu_budget as p\n"
        "@pytest.hookimpl(wrapper=True, tryfirst=True)\n"
        "def pytest_runtest_makereport(item, call):\n"
        "    if call.when == 'teardown':\n"
        "        total, excluded = {'test_within': (0.69, 0), 'test_net': (0.71, 0), "
        "'test_gross': (2.81, 2.2)}[item.name]\n"
        "        item.stash[p._CPU_TIMES] = {'call': total}\n"
        "        item.stash[p._CPU_EXCLUSIONS] = {'call': {'gc': excluded, 'import': 0}}\n"
        "        item.stash[p._CPU_BUDGET] = p.cpu_accounting.CpuBudget()\n"
        "    return (yield)\n",
        encoding="utf-8",
    )
    (tmp_path / "probe_test.py").write_text(
        "def test_within(): pass\ndef test_net(): pass\ndef test_gross(): pass\n", encoding="utf-8"
    )
    result = _run_child_pytest(tmp_path, "--cpu-limit-scale=1.4", "--junitxml=result.xml", "probe_test.py")
    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout + result.stderr
    cases = {case.get("name"): case for case in ET.parse(tmp_path / "result.xml").iter("testcase")}
    assert {name for name, case in cases.items() if case.find("error") is not None} == {"test_net", "test_gross"}
    properties = {prop.attrib["name"]: prop.attrib["value"] for prop in cases["test_within"].findall("properties/property")}
    assert float(properties["cpu_budget_seconds"]) == pytest.approx(0.69)
    assert float(properties["cpu_limit_seconds"]) == pytest.approx(0.7)
    assert float(properties["cpu_gross_limit_seconds"]) == pytest.approx(2.8)


def test_cpu_record_only_reports_overrun_without_failure(tmp_path: pathlib.Path, monkeypatch) -> None:
    """記録のみの指定では、CPU超過を警告とJUnitへ残して合否と再試行から外し、機能失敗は保つ。"""
    monkeypatch.setenv("CI", "1")
    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "from agent_toolkit._testing import pytest_cpu_budget as p\n"
        "@pytest.hookimpl(wrapper=True, tryfirst=True)\n"
        "def pytest_runtest_makereport(item, call):\n"
        "    if call.when == 'teardown':\n"
        "        item.stash[p._CPU_TIMES] = {'call': 0.501}\n"
        "        item.stash[p._CPU_EXCLUSIONS] = {}\n"
        "        item.stash[p._CPU_BUDGET] = p.cpu_accounting.CpuBudget()\n"
        "    report = yield\n"
        "    return report\n",
        encoding="utf-8",
    )
    (tmp_path / "probe_test.py").write_text(
        "def test_overrun(): pass\ndef test_functional(): assert False, 'functional failure'\n",
        encoding="utf-8",
    )
    result = _run_child_pytest(tmp_path, "--cpu-limit-record-only", "-rA", "--junitxml=result.xml", "probe_test.py")
    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout + result.stderr
    assert "functional failure" in result.stdout
    assert "CPU上限: probe_test.py::test_overrun" in result.stdout
    xml = (tmp_path / "result.xml").read_text(encoding="utf-8")
    cases = {case.get("name"): case for case in ET.fromstring(xml).iter("testcase")}
    assert [child.tag for child in cases["test_overrun"] if child.tag in ("error", "failure")] == []
    properties = {prop.attrib["name"]: prop.attrib["value"] for prop in cases["test_overrun"].findall("properties/property")}
    assert float(properties["cpu_budget_seconds"]) == pytest.approx(0.501)
    assert "cpu_first_attempt_cpu_budget_seconds" not in properties


@pytest.mark.parametrize("elapsed", [0.499, 0.5, 0.501])
@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_cpu_clock_boundary(monkeypatch: pytest.MonkeyPatch, elapsed: float, phase: str) -> None:

    class Item:
        stash = pytest.Stash()
        nodeid = "cpu_test.py::test_cpu"

    item = cast(pytest.Item, Item())
    clock = iter([0.0, elapsed])
    with monkeypatch.context() as patch:
        patch.setattr(pytest_cpu_budget.time, "thread_time", lambda: next(clock))
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


@pytest.mark.parametrize(
    "mode",
    ["foreign", "thread", "pool", "exception", "nested", "spanning", "initializer", "initializer-child", "initializer-error"],
)
def test_cpu_budget_keeps_worker_ownership_across_phases(monkeypatch, mode) -> None:
    item = cast(pytest.Item, types.SimpleNamespace(stash=pytest.Stash(), nodeid="cpu_test.py::test_cpu"))
    clocks: collections.defaultdict[int, float] = collections.defaultdict(float)
    main = threading.get_ident()
    ready, proceed = threading.Event(), threading.Event()
    native_clocks: dict[int, int] = {}
    future: concurrent.futures.Future[None] | None = None

    def work():
        ident = threading.get_ident()
        native_clocks[threading.get_native_id()] = ident
        clocks[ident] += 0.3 if mode in {"nested", "spanning", "initializer-child"} else 0.6
        if mode == "spanning":
            ready.set()
            assert proceed.wait(10)
            clocks[ident] += 0.3
        elif mode in {"nested", "initializer-child"}:

            def child_work():
                clocks[threading.get_ident()] += 0.3

            child = threading.Thread(target=child_work)
            child.start()
            child.join(10)
            assert not child.is_alive()
        elif mode in {"exception", "initializer-error"}:
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
        executor.submit(lambda: None).result(timeout=10)
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
                patch.setattr(pytest_cpu_budget.time, "thread_time", lambda: clocks[threading.get_ident()])
                patch.setattr(pytest_cpu_budget.time, "process_time", lambda: sum(clocks.values()))
                patch.setattr(cpu_accounting, "_process_threads", thread_clocks)
                hook = pytest_plugin.pytest_runtest_setup(item)
                next(hook)
                clocks[main] += 0.01
                if foreign is not None:
                    proceed.set()
                    foreign.join(10)
                    assert not foreign.is_alive()
                elif mode.startswith("initializer"):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1, initializer=work) as initialized:
                        initial = initialized.submit(lambda: None)
                        if mode == "initializer-error":
                            with pytest.raises(RuntimeError):
                                initial.result(timeout=10)
                        else:
                            initial.result(timeout=10)
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
                    assert cpu_accounting.snapshot(item.stash[pytest_cpu_budget._CPU_BUDGET])["worker"] == pytest.approx(0.3)

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
        item.stash[pytest_cpu_budget._CPU_TIMES] = recorded
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
    assert request.node.parent is not None
    item = pytest.Function.from_parent(request.node.parent, name="cpu_probe", callobj=lambda: None)
    item.add_marker(marker)
    with pytest.raises(pytest.UsageError, match="cpu_limit"):
        pytest_plugin.pytest_collection_modifyitems(request.config, [item])


@pytest.mark.parametrize("value", ["invalid", "0", "-1", "nan", "inf", True])
def test_cpu_limit_rejects_invalid_default(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, value: str | bool
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(request.config, "getini", lambda _name: value)
        with pytest.raises(pytest.UsageError, match="cpu_limit設定"):
            pytest_plugin.pytest_collection_modifyitems(request.config, [])


@pytest.mark.parametrize(
    ("gross", "gc_cpu", "import_cpu", "net_limit", "failed"),
    [
        (0.8, 0.2, 0.2, 0.5, False),
        (1.0, 0.25, 0.25, 0.5, True),
        (2.1, 1.8, 0.1, 0.5, True),
        (2.999, 1.75, 0.0, 1.5, False),
        (3.0, 1.75, 0.0, 1.5, True),
        (3.001, 1.75, 0.0, 1.5, True),
        (2.0, 0.5, 0.0, 1.5, True),
    ],
)
def test_exclusions_do_not_hide_budget_or_gross_overrun(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, gross, gc_cpu, import_cpu, net_limit, failed
) -> None:
    assert request.node.parent is not None
    item = pytest.Function.from_parent(request.node.parent, name="cpu_probe", callobj=lambda: None)
    if net_limit != 0.5:
        item.add_marker(pytest.mark.cpu_limit(net_limit, reason="必要な境界の制御入力"))
    # 制御入力は倍率1の境界値。外側のpytestの設定を合成itemへ持ち込まない。
    with monkeypatch.context() as patch:
        patch.setattr(request.config.option, "cpu_limit_scale", 1.0)
        patch.setattr(request.config.option, "cpu_limit_record_only", False)
        pytest_plugin.pytest_collection_modifyitems(request.config, [item])
    item.stash[pytest_cpu_budget._CPU_TIMES] = {"call": gross}
    item.stash[pytest_cpu_budget._CPU_EXCLUSIONS] = {"call": {"gc": gc_cpu, "import": import_cpu}}
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
    assert properties["cpu_limit_seconds"] == net_limit
    assert properties["cpu_gross_limit_seconds"] == net_limit + 1.5


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
