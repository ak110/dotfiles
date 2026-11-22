import contextlib
import json
import os
import pathlib
import re
import subprocess
import sys
import textwrap
import time
import xml.etree.ElementTree as ET
from typing import cast

import psutil
import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp import sweep
from agent_toolkit._testing import late_descendant, pytest_plugin, xdist_stop
from agent_toolkit._testing.pytest_plugin_support import _run_child_pytest

# pylint: disable=protected-access


def test_release_keeps_interpreter_resource_tracker(tmp_path: pathlib.Path) -> None:
    evidence = tmp_path / "evidence.json"
    (tmp_path / "tracker_test.py").write_text(
        textwrap.dedent(f"""\
            import json, multiprocessing, pathlib, subprocess, sys
            from multiprocessing import resource_tracker

            KEPT = []

            def test_probe():
                KEPT.append(multiprocessing.get_context("spawn").Semaphore())
                child = subprocess.Popen([sys.executable, "-c", {late_descendant.held_source(tmp_path / "tracker.release")!r}])
                pathlib.Path({str(evidence)!r}).write_text(json.dumps({{
                    "tracker": resource_tracker._resource_tracker._pid, "child": child.pid,
                }}), encoding="utf-8")
        """),
        encoding="utf-8",
    )
    result = _run_child_pytest(tmp_path, "tracker_test.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "relaunching" not in result.stderr, result.stderr
    observed = json.loads(evidence.read_text(encoding="utf-8"))
    assert observed["tracker"] is not None

    with contextlib.suppress(psutil.NoSuchProcess):
        assert psutil.Process(observed["child"]).status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)


@pytest.mark.parametrize("workers", [0, 2])
def test_collection_binds_import_time_home_to_basetemp(tmp_path: pathlib.Path, workers: int) -> None:
    """収集時にimportされたモジュールが確定するホームは、実行環境のホームでなくbasetemp配下を指す。

    試験関数ごとの隔離は収集の後に始まるため、収集区間の隔離が無いとimport時の定数が実ホームを指す。
    """
    (tmp_path / "home_probe.py").write_text("import pathlib\n\nHOME = pathlib.Path.home()\n", encoding="utf-8")
    (tmp_path / "home_test.py").write_text(
        textwrap.dedent(f"""\
            import os, pathlib
            import home_probe

            def test_probe(pytestconfig):
                assert home_probe.HOME == pathlib.Path(pytestconfig.option.basetemp) / "import-time-home"
                assert home_probe.HOME != pathlib.Path({str(pathlib.Path.home())!r})
                assert pathlib.Path.home() == pathlib.Path({str(pathlib.Path.home())!r})
        """),
        encoding="utf-8",
    )
    arguments = ["-p", "xdist.plugin", "-n", str(workers)] if workers else []
    result = _run_child_pytest(tmp_path, *arguments, "home_test.py")
    assert result.returncode == 0, result.stdout + result.stderr


def test_agents_server_logging_reclaimed_before_next_test(tmp_path: pathlib.Path) -> None:
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").touch()
    (tmp_path / "conftest.py").write_text(
        textwrap.dedent("""\
            import pathlib
            from agent_toolkit._testing import pytest_plugin

            def pytest_configure(config):
                pytest_plugin._PACKAGE_ROOT = pathlib.Path(__file__).parent / "sample"
        """),
        encoding="utf-8",
    )
    (package / "logging_test.py").write_text(
        textwrap.dedent("""\
            import logging
            from agent_toolkit._agents_server import logging_config
            from agent_toolkit._common import state_paths

            SERVER = logging.getLogger("agent-toolkit.agents-server")
            BEFORE = (tuple(SERVER.handlers), SERVER.level, SERVER.propagate)

            def test_a_configures_logging(capsys, monkeypatch, tmp_path):
                monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
                logging_config.configure_logging()
                assert SERVER.handlers

            def test_b_later_warning_reaches_capture(capsys, caplog):
                assert (tuple(SERVER.handlers), SERVER.level, SERVER.propagate) == BEFORE
                logging.getLogger("agent-toolkit.agents-server.probe").warning("後続の警告")
                assert "Logging error" not in capsys.readouterr().err
                assert "後続の警告" in caplog.text
        """),
        encoding="utf-8",
    )
    result = _run_child_pytest(tmp_path, "-v", "sample/logging_test.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_pytest_notifies_once_while_waiting_for_slot_held_by_other_process(tmp_path: pathlib.Path) -> None:
    """別プロセスが実行枠を保持する間に起動したpytestは、保持者の情報を含む待機の通知を標準エラーへ1回出す。"""
    probe = tmp_path / "probe_test.py"
    probe.write_text("def test_probe():\n    pass\n", encoding="utf-8")
    holder_script = tmp_path / "holder.py"
    holder_script.write_text(
        textwrap.dedent("""\
            import pathlib, sys, time
            from pyfltr.command import execution_slot

            with execution_slot.hold(label="holder-probe"):
                print("held", flush=True)
                release = pathlib.Path(sys.argv[1])
                deadline = time.monotonic() + 60
                while not release.exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
        """),
        encoding="utf-8",
    )
    release = tmp_path / "release"
    error_path = tmp_path / "child.stderr"
    environment = {**os.environ, "PYFLTR_CACHE_DIR": str(tmp_path / "pyfltr"), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    environment.pop("PYTEST_ADDOPTS", None)
    child_argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts="]
    child_argv += ["-p", pytest_plugin.__name__, str(probe)]
    with (
        subprocess.Popen(
            [sys.executable, str(holder_script), str(release)],
            env=environment,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ) as holder,
        error_path.open("w", encoding="utf-8") as error_stream,
    ):
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "held"
            with subprocess.Popen(
                child_argv,
                cwd=tmp_path,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=error_stream,
                text=True,
                encoding="utf-8",
            ) as child:
                deadline = time.monotonic() + 60
                # 子pytestの待機の開始を確かめてから保持を解く。
                while "実行枠を保持している" not in error_path.read_text(encoding="utf-8"):
                    assert child.poll() is None, error_path.read_text(encoding="utf-8")
                    assert time.monotonic() < deadline
                    time.sleep(0.1)
                release.touch()
                output, _ = child.communicate(timeout=60)
        finally:
            release.touch()
            holder.wait(timeout=60)
    errors = error_path.read_text(encoding="utf-8")
    assert child.returncode == 0, (output, errors)
    assert errors.count("実行枠を保持している") == 1
    assert f"label=holder-probe、pid={holder.pid}" in errors
    assert "待機時間は試験の処理時間に含まれない" in errors


@pytest.mark.parametrize("mode", ["weighted", "unknown", "empty", "missing", "invalid"])
def test_test_shards_partition_collected_tests_across_runs(tmp_path: pathlib.Path, mode: str) -> None:
    (tmp_path / "shard_test.py").write_text(
        textwrap.dedent("""\
            def test_a(): pass
            def test_b(): pass
            def test_c(): pass
            def test_d(): pass
        """),
        encoding="utf-8",
    )
    # 記録の配置だけを隔離し、公開CLIからの読込・収集・選択は実物を通す。
    (tmp_path / "conftest.py").write_text(
        "import pathlib\nfrom agent_toolkit._testing import pytest_plugin\n"
        "pytest_plugin.__file__ = str(pathlib.Path(__file__).with_name('pytest_plugin.py'))\n",
        encoding="utf-8",
    )
    nodeids = [f"shard_test.py::test_{name}" for name in "abcd"]
    weights = {"weighted": [8, 4, 4, 2], "unknown": [6, 2], "empty": [], "invalid": [-1]}
    if mode != "missing":
        recorded = dict(zip(nodeids, weights[mode], strict=False))
        (tmp_path / "test_durations.json").write_text(json.dumps(recorded), encoding="utf-8")
    expected_shards = {
        "weighted": [{nodeids[0], nodeids[3]}, {nodeids[1], nodeids[2]}],
        "unknown": [{nodeids[0], nodeids[1]}, {nodeids[2], nodeids[3]}],
        "empty": [{nodeids[0], nodeids[2]}, {nodeids[1], nodeids[3]}],
        "missing": [{nodeids[0], nodeids[2]}, {nodeids[1], nodeids[3]}],
    }
    if mode == "invalid":
        result = _run_child_pytest(tmp_path, "--test-shard=1/2", "shard_test.py")
        assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
        assert "所要が不正" in result.stderr
        return
    expected = set(nodeids)
    executed: list[set[str]] = []
    # 子pytestの起動回数を抑えるため2分割で確かめ、2番目はworker間で同じ集合を選ぶことも見るためxdistで実行する。
    for index, workers in ((1, []), (2, ["-p", "xdist.plugin", "-n", "2"])):
        targets = nodeids if index == 1 else list(reversed(nodeids))
        result = _run_child_pytest(tmp_path, "-rA", *workers, f"--test-shard={index}/2", *targets)
        assert result.returncode == 0, result.stdout + result.stderr
        executed.append(set(re.findall(r"^PASSED (\S+)$", result.stdout, re.MULTILINE)))
        assert executed[-1] == expected_shards[mode][index - 1]
    assert all(executed)
    assert set().union(*executed) == expected
    assert sum(len(shard) for shard in executed) == len(expected)


def test_invalid_test_shard_stops_before_collection(tmp_path: pathlib.Path) -> None:
    (tmp_path / "probe_test.py").write_text("def test_probe():\n    pass\n", encoding="utf-8")
    result = _run_child_pytest(tmp_path, "--test-shard=4/3", "probe_test.py")
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "--test-shard=INDEX/COUNT" in result.stderr
    assert "passed" not in result.stdout


@pytest.mark.parametrize(
    "targets",
    [
        ["missing_test.py"],
        ["missing-directory"],
        ["valid_test.py::test_missing"],
        ["valid_test.py[x]"],
        ["valid_test.py", "missing_test.py"],
    ],
    ids=["file", "directory", "node-id", "parametrized-path", "mixed"],
)
def test_parallel_collection_preserves_usage_error(tmp_path: pathlib.Path, targets: list[str]) -> None:
    (tmp_path / "valid_test.py").write_text("def test_valid():\n    pass\n", encoding="utf-8")
    serial = _run_child_pytest(tmp_path, *targets, plugin=xdist_stop.__name__)
    assert serial.returncode == pytest.ExitCode.USAGE_ERROR, serial.stdout + serial.stderr
    expected = [line for line in serial.stderr.splitlines() if line.startswith("ERROR:")]
    assert expected, serial.stderr
    # 入力形式の網羅は軽いxdist適応だけで行い、混在の代表例では共通プラグインからの登録も通す。
    plugin = pytest_plugin.__name__ if len(targets) > 1 else xdist_stop.__name__
    parallel = _run_child_pytest(tmp_path, "-p", "xdist.plugin", "-n", "2", *targets, plugin=plugin)
    assert parallel.returncode == serial.returncode, parallel.stdout + parallel.stderr
    assert all(message in parallel.stderr for message in expected), (expected, parallel.stderr)
    assert "引数のパス・node IDを確認して修正し、pytestを再実行してください。" in parallel.stderr


@pytest.mark.parametrize(
    ("markexpr", "name", "body", "expected"),
    [
        ("windows", "posix_only_test.py", "import os\n_UID = os.getuid()\n", True),
        ("windows", "boundary_test.py", "import pytest\n\npytestmark = pytest.mark.windows\n", False),
        ("windows", "helper.py", "import os\n", None),
        ("not browser and not live_agents", "posix_only_test.py", "import os\n_UID = os.getuid()\n", None),
    ],
    ids=["unmarked", "marked", "not-test-module", "other-expression"],
)
def test_windows_selection_skips_unmarked_test_modules(
    tmp_path: pathlib.Path, markexpr: str, name: str, body: str, expected: bool | None
) -> None:
    """`-m windows`では本文にmarkerが無い試験モジュールだけを読み込まず、他の選択では収集を変えない。"""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")

    class Config:
        def getoption(self, option: str) -> str:
            assert option == "markexpr"
            return markexpr

    assert pytest_plugin.pytest_ignore_collect(path, cast(pytest.Config, Config())) is expected


_SLOT_PROBE = """\
import json, os, pathlib, time
import pytest

@pytest.mark.parametrize("index", range(6))
def test_slow(index):
    evidence = pathlib.Path(os.environ["SLOT_EVIDENCE"])
    queued = pathlib.Path(os.environ["SLOT_QUEUED"])
    # 最初の試験の完了後に始まる試験は、待機者が実行枠の待ち列へ並んだ印を見てから進む。
    # 待機者の起動の速さに左右されず、残りの試験の実行中に待機者が並んでいる状態を作る。
    deadline = time.monotonic() + 60
    while list(evidence.glob("test-*.json")) and not queued.exists():
        assert time.monotonic() < deadline, "待機者が待ち列へ並ばない"
        time.sleep(0.02)
    started = time.time()
    (evidence / f"test-{index}.json").write_text(json.dumps({"started": started, "ended": time.time()}), encoding="utf-8")
    assert os.environ.get("SLOT_FAIL") != str(index)
    if os.environ.get("SLOT_INTERRUPT") == str(index):
        raise KeyboardInterrupt
"""


_SLOT_WAITER = """\
import json, os, pathlib, sys, time
from pyfltr.command import execution_slot

# `on_wait`は待ち列への登録後に呼ばれるため、印は登録の完了を示す。
queued = pathlib.Path(os.environ["SLOT_QUEUED"])

def mark(_owner=None):
    queued.write_text("", encoding="utf-8")

with execution_slot.hold(label="waiter-probe", on_wait=mark):
    mark()
    started = time.time()
    output = pathlib.Path(sys.argv[1])
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps({"started": started, "ended": time.time()}), encoding="utf-8")
    temporary.replace(output)
"""


_SLOT_OUTER = """\
import subprocess, sys
from pyfltr.command import execution_slot

with execution_slot.hold(label="outer-probe"):
    raise SystemExit(subprocess.run(sys.argv[1:], check=False).returncode)
"""


_SLOT_RUN_COMMAND = """\
import json, os, pathlib, sys
from agent_toolkit._atk import run_command

record, code, failure = run_command.execute(sys.argv[1:], pathlib.Path.cwd(), 90, verification=True)
pathlib.Path(os.environ["SLOT_RECORD"]).write_text(json.dumps(record), encoding="utf-8")
sys.stdout.write(pathlib.Path(record["stdout_path"]).read_text(encoding="utf-8"))
sys.stderr.write(pathlib.Path(record["stderr_path"]).read_text(encoding="utf-8"))
raise SystemExit(code)
"""


@pytest.mark.parametrize(
    ("workers", "route", "mode"),
    [
        (2, "direct", "pass"),
        (0, "direct", "pass"),
        (2, "direct", "fail"),
        (0, "direct", "interrupt"),
        (2, "inherited", "pass"),
        (2, "pyfltr", "pass"),
        (2, "run-command", "pass"),
        pytest.param(2, "idle-fork", "pass", marks=pytest.mark.skipif(not hasattr(os, "fork"), reason="fork未対応")),
    ],
)
@pytest.mark.timeout(120)
def test_pytest_holds_slot_until_all_tests_finish(tmp_path: pathlib.Path, workers: int, route: str, mode: str) -> None:
    """試験の途中で並んだ別の検証は、pytestの全試験が終わるまで待ってから実行枠を取得する。

    直接起動、xdistのworker、祖先の所有を継承する入れ子（外側の保持、pyfltr経由、統合検証経由）と、
    失敗・中断する試験を含む実行で、待機者の保持区間がどの試験の実行区間よりも後に始まることを確かめる。
    残りの試験は待機者が待ち列へ並んだ印を待ってから進むため、待機者の起動が遅れても判定は変わらない。
    """
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    probe = _SLOT_PROBE
    if route == "idle-fork":
        (tmp_path / "fork.py").write_text("pass\n", encoding="utf-8")
        probe = probe.replace(
            "    evidence =",
            "    from agent_toolkit._testing import fork_runner\n"
            "    assert fork_runner.run_script(pathlib.Path(__file__).with_name('fork.py')).returncode == 0\n"
            "    evidence =",
            1,
        )
    (tmp_path / "probe_test.py").write_text(probe, encoding="utf-8")
    (tmp_path / "waiter.py").write_text(_SLOT_WAITER, encoding="utf-8")
    (tmp_path / "outer.py").write_text(_SLOT_OUTER, encoding="utf-8")
    (tmp_path / "run_command.py").write_text(_SLOT_RUN_COMMAND, encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.update(
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYFLTR_CACHE_DIR=str(tmp_path / "pyfltr"),
        SLOT_EVIDENCE=str(evidence),
        SLOT_QUEUED=str(tmp_path / "queued"),
        SLOT_RECORD=str(tmp_path / "run-command.json"),
    )
    if mode == "fail":
        environment["SLOT_FAIL"] = "5"
    elif mode == "interrupt":
        environment["SLOT_INTERRUPT"] = "5"
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", "-p", pytest_plugin.__name__]
    argv += [
        "-p",
        "xdist.plugin",
        "-n",
        str(workers),
        "--junitxml",
        str(tmp_path / "pytest.xml"),
        str(tmp_path / "probe_test.py"),
    ]
    if route == "inherited":
        argv = [sys.executable, str(tmp_path / "outer.py"), *argv]
    elif route in ("pyfltr", "run-command"):
        pytest_bin = pathlib.Path(sys.executable).with_name("pytest.exe" if os.name == "nt" else "pytest")
        assert pytest_bin.is_file()
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pyfltr]\npytest = true\npytest-runner = "direct"\nrespect-gitignore = false\n'
            f"pytest-path = {json.dumps(str(pytest_bin))}\npytest-args = {json.dumps(argv[3:-1])}\n",
            encoding="utf-8",
        )
        argv = [sys.executable, "-m", "pyfltr", "run", "--verbose", "--commands=pytest", "probe_test.py"]
        if route == "run-command":
            argv = [sys.executable, str(tmp_path / "run_command.py"), *argv]
    waited = tmp_path / "waiter.json"
    with subprocess.Popen(
        argv, cwd=tmp_path, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8"
    ) as child:
        deadline = time.monotonic() + 60
        # 最初の試験の完了後に待機者を起動する。残りの試験は待機者が並んだ印を待ってから進む。
        while not list(evidence.glob("test-*.json")):
            assert child.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        with subprocess.Popen([sys.executable, str(tmp_path / "waiter.py"), str(waited)], env=environment) as waiter:
            try:
                output, _ = child.communicate(timeout=90)
                waiter.wait(timeout=60)
            finally:
                if waiter.poll() is None:
                    waiter.kill()
                    waiter.wait(timeout=5)
    assert waiter.returncode == 0
    assert child.returncode == {"pass": 0, "fail": 1, "interrupt": 2}[mode], output
    if mode != "interrupt":
        assert len(ET.parse(tmp_path / "pytest.xml").findall(".//testcase")) == 6
    tests = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(evidence.glob("test-*.json"))]
    assert len(tests) == 6
    window = json.loads(waited.read_text(encoding="utf-8"))
    # 待機者は試験の区切りでは進まず、最後の試験が終わった後に保持を始める。
    assert max(test["ended"] for test in tests) <= window["started"], (window, tests)
    if route == "run-command":
        record = json.loads((tmp_path / "run-command.json").read_text(encoding="utf-8"))
        assert record["child_exit_code"] == record["wrapper_exit_code"] == 0
        assert not record["timed_out"] and record["failure"] is None
        # 統合検証は開始前の待機だけを記録し、実行中に実行枠を取り直さない。
        assert all(wait["kind"] == "initial" for wait in record["timing"]["slot_waits"])


def test_reclaim_stale_pytest_fixtures_keeps_running_owner(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "temp-root"
    root.mkdir()
    monkeypatch.setattr(managed_temp, "temp_root_path", lambda: root)
    with subprocess.Popen([sys.executable, "-c", "pass"]) as finished:
        finished.wait()
        dead_token = f"{finished.pid} "
    live = root / "pytest-fixtures-live"
    dead = root / "pytest-fixtures-dead"
    unknown = root / "pytest-fixtures-unknown"
    for area in (live, dead, unknown):
        area.mkdir()
        (area / "data").write_text("x", encoding="utf-8")
    (live / sweep.OWNER_FILE).write_text(sweep.owner_token(), encoding="ascii")
    (dead / sweep.OWNER_FILE).write_text(dead_token, encoding="ascii")
    monkeypatch.setattr(managed_temp, "cleanup_managed_temp", lambda area: __import__("shutil").rmtree(area))

    reclaimed = pytest_plugin.reclaim_stale_pytest_fixtures()

    assert reclaimed == [dead]
    assert live.exists() and unknown.exists() and not dead.exists()
