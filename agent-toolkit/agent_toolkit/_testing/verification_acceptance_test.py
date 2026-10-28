"""公開検証コマンドの競合と、実serve・Observerの終了後の引継ぎを観測する。"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Generator
from unittest import mock

import pytest
import watchdog.observers

from agent_toolkit._atk.lane_test import prepare_lane_verification
from agent_toolkit._common import atomic_file, process_tree
from agent_toolkit._testing import verification_acceptance_support

_DRIVER = pathlib.Path(verification_acceptance_support.__file__)
_PLUGIN = _DRIVER.parents[2]


@pytest.mark.asyncio
async def test_ready_is_published_after_complete_json(tmp_path, monkeypatch):
    """置換直前までreadyは不在で、公開する一時ファイルは完全なJSONを保持する。"""
    replace = atomic_file._replace
    observed = []

    def observe(source, destination):
        if destination.name == "serve.ready":
            assert not destination.exists()
            observed.append(json.loads(pathlib.Path(source).read_text(encoding="utf-8")))
            (tmp_path / "serve.release").touch()
        replace(source, destination)

    monkeypatch.setattr(atomic_file, "_replace", observe)
    unrelated = watchdog.observers.Observer()
    unrelated.start()
    try:
        await verification_acceptance_support._serve(tmp_path, failure=False)
        assert unrelated.is_alive()
    finally:
        unrelated.stop()
        unrelated.join(timeout=5)
        assert not unrelated.is_alive()
    assert len(observed) == 1 and observed[0]["port"] > 0
    assert json.loads((tmp_path / "serve.ready").read_text(encoding="utf-8")) == observed[0]


_PROBE = """\
import json, pathlib, sys, time
from pyfltr.command import execution_slot
label, output = sys.argv[1:]
owner = execution_slot.read_owner()
assert owner is not None
for event in ('start', 'end'):
    record = {'label': label, 'event': event, 'time': time.monotonic_ns(), 'owner': owner.pid}
    with pathlib.Path(output).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record) + '\\n')
    if event == 'start':
        time.sleep(0.2)
print('記録用検証の出力')
print('記録用検証の診断', file=sys.stderr)
"""


def _wait(path: pathlib.Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while not path.exists():
        if process.poll() is not None:
            raise RuntimeError(f"同期点より前に終了した: {process.communicate()}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"同期点へ到達しない: {path}")
        time.sleep(0.01)


@contextlib.contextmanager
def _process(argv: list[str], environment: dict[str, str], cwd: pathlib.Path) -> Generator[subprocess.Popen[str]]:
    # 停止確認が失敗した場合も、Popen.__exit__の無期限waitで診断を妨げない。
    with process_tree.process_scope(
        argv,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as process:
        yield process


@pytest.mark.parametrize("failing_stream", ["stdout", "stderr"])
@pytest.mark.parametrize("stop_failure", [False, True])
def test_process_closes_both_streams_after_close_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failing_stream: str, stop_failure: bool
) -> None:
    """停止と片方のcloseが失敗しても、両ストリームのcloseを実行する。"""
    process = mock.Mock()
    process.poll.return_value = None
    getattr(process, failing_stream).close.side_effect = OSError("ストリーム回収失敗")
    monkeypatch.setattr(subprocess, "Popen", mock.Mock(return_value=process))
    stop = mock.Mock(return_value=[], side_effect=RuntimeError("停止失敗") if stop_failure else None)
    monkeypatch.setattr(process_tree, "stop_process_tree", stop)

    with pytest.raises(OSError, match="ストリーム回収失敗"), _process([], {}, tmp_path):
        pass

    stop.assert_called_once_with(process)
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()


def _environment(root: pathlib.Path) -> dict[str, str]:
    return {**os.environ, "PYFLTR_CACHE_DIR": str(root / "pyfltr-cache")}


def _driver(mode: str, root: pathlib.Path, label: str, *argv: str) -> list[str]:
    return [sys.executable, str(_DRIVER), mode, str(root), label, *argv]


def test_public_verification_entries_share_one_execution_interval(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    record_property: Callable[[str, object], None],
) -> None:
    """laneの統合検証・pytest・pyfltrの3種類が保持者の解放を待ち、検証区間が重ならず全て終了する。"""
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    events = tmp_path / "events.jsonl"
    lane = prepare_lane_verification(
        tmp_path / "lane-case", monkeypatch, capsys, [sys.executable, str(probe), "lane", str(events)]
    )
    environment = _environment(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "work.py").write_text("# 検証対象\n", encoding="utf-8")
    (project / "probe_test.py").write_text(
        "import subprocess\n"
        "def test_record():\n"
        f"    subprocess.run({[sys.executable, str(probe), 'pytest', str(events)]!r}, check=True)\n",
        encoding="utf-8",
    )
    (project / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts=['-p', 'agent_toolkit._testing.pytest_plugin']\n"
        "[tool.pyfltr.custom-commands.probe]\ntype='linter'\n"
        f"path={json.dumps(sys.executable)}\nargs={json.dumps([str(probe), 'pyfltr', str(events)])}\n"
        "targets=['*.py']\npass-filenames=false\n",
        encoding="utf-8",
    )
    commands = {
        "lane": lane[1:],
        "pytest": ["-m", "pytest", "-p", "no:cacheprovider", "-q", str(project / "probe_test.py")],
        "pyfltr": ["-m", "pyfltr", "run", "--commands=probe", "--no-cache", "--no-fix", "--no-clear", str(project / "work.py")],
    }
    observed = {}
    with contextlib.ExitStack() as stack:
        holder = stack.enter_context(_process(_driver("hold", tmp_path, "holder"), environment, project))
        _wait(tmp_path / "holder.ready", holder)
        running = {
            label: stack.enter_context(_process(_driver("entry", tmp_path, label, *entry), environment, project))
            for label, entry in commands.items()
        }
        for label in commands:
            _wait(tmp_path / f"{label}.waiting", running[label])
        assert not events.exists()
        (tmp_path / "holder.release").touch()
        assert holder.wait(timeout=10) == 0
        for label, process in running.items():
            stdout, stderr = process.communicate(timeout=40)
            assert process.returncode == 0, (label, stdout, stderr)
            observed[label] = {"stdout": stdout, "stderr": stderr}
    intervals = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    assert len(intervals) == 6
    assert {row["label"] for row in intervals} == set(commands)
    assert len({row["owner"] for row in intervals}) == 3
    for start, end in zip(intervals[::2], intervals[1::2], strict=True):
        assert start["event"] == "start" and end["event"] == "end"
        assert start["label"] == end["label"] and start["time"] < end["time"]
    assert all(end["time"] <= start["time"] for end, start in zip(intervals[1::2], intervals[2::2], strict=False))
    record_property(
        "shared_execution_observation", json.dumps({"intervals": intervals, "outputs": observed}, ensure_ascii=False)
    )


@pytest.mark.parametrize("failure", [False, True])
def test_real_serve_releases_observer_before_next_verification(
    tmp_path: pathlib.Path, failure: bool, record_property: Callable[[str, object], None]
) -> None:
    """実HTTP配信の観測・失敗後にObserverを終了し、次の独立検証が進む。"""
    environment = _environment(tmp_path)
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    events = tmp_path / "next.jsonl"
    with contextlib.ExitStack() as stack:
        server = stack.enter_context(
            _process(_driver("serve", tmp_path, "failure" if failure else "success"), environment, tmp_path)
        )
        _wait(tmp_path / "serve.ready", server)
        port = json.loads((tmp_path / "serve.ready").read_text(encoding="utf-8"))["port"]
        deadline = time.monotonic() + 20
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            try:
                with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    assert response.status == 200
                break
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        project = tmp_path / "next-project"
        project.mkdir()
        (project / "probe_test.py").write_text(
            "import subprocess\n"
            "def test_record():\n"
            f"    subprocess.run({[sys.executable, str(probe), 'next', str(events)]!r}, check=True)\n",
            encoding="utf-8",
        )
        (project / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\naddopts=['-p', 'agent_toolkit._testing.pytest_plugin']\n", encoding="utf-8"
        )
        following = stack.enter_context(
            _process(
                _driver(
                    "entry", tmp_path, "next", "-m", "pytest", "-p", "no:cacheprovider", "-q", str(project / "probe_test.py")
                ),
                environment,
                project,
            )
        )
        _wait(tmp_path / "next.waiting", following)
        assert not events.exists()
        (tmp_path / "serve.release").touch()
        stdout, stderr = server.communicate(timeout=20)
        assert server.returncode == (1 if failure else 0), (stdout, stderr)
        next_stdout, next_stderr = following.communicate(timeout=20)
        assert following.returncode == 0, (next_stdout, next_stderr)
    stopped = json.loads((tmp_path / "serve.stopped").read_text(encoding="utf-8"))
    assert stopped and not any(thread["alive"] for thread in stopped)
    assert [json.loads(line)["event"] for line in events.read_text(encoding="utf-8").splitlines()] == ["start", "end"]
    record_property(
        "serve_shutdown_observation",
        json.dumps(
            {
                "watcher_threads": stopped,
                "serve_stdout": stdout,
                "serve_stderr": stderr,
                "next_stdout": next_stdout,
                "next_stderr": next_stderr,
            },
            ensure_ascii=False,
        ),
    )
