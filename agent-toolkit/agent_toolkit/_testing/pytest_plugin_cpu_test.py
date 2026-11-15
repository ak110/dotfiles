"""共通pytestのCPU上限を確認する。"""

import pathlib
import xml.etree.ElementTree as ET
from typing import Any, Literal, cast

import pytest

from agent_toolkit._testing import pytest_plugin
from agent_toolkit._testing.pytest_plugin_support import _run_child_pytest

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
        patch.setattr(pytest_plugin.time, "process_time", lambda: next(clock))
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


@pytest.mark.parametrize("workers", [0, 2])
def test_cpu_limit_public_failure(tmp_path: pathlib.Path, workers: int) -> None:
    """短い実CPU超過が公開pytestとworkerの終了コードへ伝わる。"""
    (tmp_path / "cpu_test.py").write_text(
        "import time, pytest\n@pytest.mark.cpu_limit(0.01, reason='CPU境界の試験')\n"
        "def test_cpu():\n    start = time.process_time()\n"
        "    while time.process_time() - start < 0.02:\n        pass\n",
        encoding="utf-8",
    )
    arguments = ["-p", "xdist.plugin", "-n", "2"] if workers else []
    result = _run_child_pytest(tmp_path, *arguments, "--junitxml=cpu.xml", "cpu_test.py")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "CPU上限" in result.stdout + result.stderr
    properties = {
        entry.attrib["name"]: entry.attrib["value"] for entry in ET.parse(tmp_path / "cpu.xml").findall(".//property")
    }
    assert float(properties["cpu_total_seconds"]) >= 0.01
    assert properties["cpu_limit_reason"] == "CPU境界の試験"


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


def test_cpu_limit_accepts_reasoned_override_and_preserves_wall_duration(tmp_path: pathlib.Path) -> None:
    """成功・skip・既存失敗のCPU値と実時間durationを公開結果へ保持する。"""
    (tmp_path / "cpu_test.py").write_text(
        "import time, pytest\npytestmark = pytest.mark.cpu_limit(1, reason='公開特例の確認')\n"
        "def test_cpu():\n    time.sleep(0.01)\n"
        "def test_skip():\n    pytest.skip('元のskip理由')\n"
        "def test_fail():\n    assert False, '元の失敗理由'\n",
        encoding="utf-8",
    )
    result = _run_child_pytest(
        tmp_path, "-o", "cpu_limit=0.000001", "--junitxml=cpu.xml", "--durations=1", "--durations-min=0", "cpu_test.py"
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "durations" in result.stdout
    cases = {case.attrib["name"]: case for case in ET.parse(tmp_path / "cpu.xml").findall(".//testcase")}
    assert set(cases) == {"test_cpu", "test_skip", "test_fail"}
    assert cases["test_cpu"].find("failure") is None
    skipped = cases["test_skip"].find("skipped")
    failure = cases["test_fail"].find("failure")
    assert skipped is not None and "元のskip理由" in str(skipped.attrib)
    assert failure is not None and "元の失敗理由" in str(failure.attrib)
    for case in cases.values():
        properties = {entry.attrib["name"]: entry.attrib["value"] for entry in case.findall(".//property")}
        stages = [float(properties[f"cpu_{phase}_seconds"]) for phase in ("setup", "call", "teardown")]
        assert float(properties["cpu_total_seconds"]) == pytest.approx(sum(stages))
        assert all(value >= 0 for value in stages)
        assert float(properties["cpu_limit_seconds"]) == 1
        assert properties["cpu_limit_reason"] == "公開特例の確認"
