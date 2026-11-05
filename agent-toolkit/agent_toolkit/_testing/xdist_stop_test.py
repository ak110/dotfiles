"""実pytestのworkstealキューで、停止理由の伝達と正常時の対象集合を比較する。"""

import json
import os
import pathlib
import subprocess
import sys
import textwrap
import time


def _run_probe(root: pathlib.Path, *, fail: bool) -> dict:
    root.mkdir()
    (root / "probe_test.py").write_text(
        textwrap.dedent(f"""\
            import os, pathlib, time
            import pytest

            @pytest.fixture(autouse=True)
            def record(request):
                path = pathlib.Path(__file__).parent / (os.environ['PYTEST_XDIST_WORKER'] + '.events')
                with path.open('a', encoding='utf-8') as stream:
                    stream.write('start ' + request.node.name + '\\n')
                yield
                with path.open('a', encoding='utf-8') as stream:
                    stream.write('end ' + request.node.name + '\\n')

            @pytest.mark.parametrize('index', range(80))
            def test_probe(index):
                if index == 0 and {fail!r}:
                    deadline = time.monotonic() + 10
                    while len(list(pathlib.Path(__file__).parent.glob('*.events'))) < 4:
                        assert time.monotonic() < deadline
                        time.sleep(0.01)
                    pathlib.Path(__file__).with_suffix('.release').touch()
                    assert False, 'probe failure'
                if {fail!r}:
                    deadline = time.monotonic() + 10
                    while not pathlib.Path(__file__).with_suffix('.release').exists():
                        assert time.monotonic() < deadline
                        time.sleep(0.01)
                time.sleep(0.02)
        """),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    started = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-p",
            "xdist.plugin",
            "-p",
            "agent_toolkit._testing.xdist_stop",
            "-n",
            "4",
            "--dist=worksteal",
            "--maxfail=1",
            str(root / "probe_test.py"),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    elapsed = time.monotonic() - started
    (root / "stdout").write_text(result.stdout, encoding="utf-8")
    (root / "stderr").write_text(result.stderr, encoding="utf-8")
    assert result.returncode == (2 if fail else 0), result.stdout + result.stderr
    starts: list[str] = []
    ends: list[str] = []
    for events in root.glob("*.events"):
        for line in events.read_text(encoding="utf-8").splitlines():
            kind, _, name = line.partition(" ")
            (starts if kind == "start" else ends).append(name)
    assert sorted(starts) == sorted(ends), "開始済み試験のfixture teardownが欠落した"
    return {"fail": fail, "executed": len(starts), "seconds": elapsed, "code": result.returncode}


def test_maxfail_stops_queued_tests_after_teardown(tmp_path: pathlib.Path) -> None:
    """同じ80試験・4workerで、上限後の未実行分とfixture teardownを比較する。"""
    result = _run_probe(tmp_path / "stopped", fail=True)
    print(json.dumps(result))
    assert result["executed"] < 40


def test_normal_shutdown_keeps_all_tests(tmp_path: pathlib.Path) -> None:
    """正常完了のshutdownでは、全対象とteardownを維持する。"""
    result = _run_probe(tmp_path / "normal", fail=False)
    print(json.dumps(result))
    assert result["executed"] == 80
