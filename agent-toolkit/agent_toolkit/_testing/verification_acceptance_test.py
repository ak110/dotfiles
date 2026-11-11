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
from agent_toolkit._testing import late_descendant, verification_acceptance_support

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
        await verification_acceptance_support._serve(tmp_path, failure=False)  # pylint: disable=protected-access  # ready公開直前の内部境界を観測する。
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
        release = pathlib.Path(output).parent / (label + '.release')
        deadline = time.monotonic() + 30
        while not release.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
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


def _release_probe(events: pathlib.Path) -> None:
    deadline = time.monotonic() + 30
    while True:
        rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()] if events.exists() else []
        for row in rows:
            release = events.parent / (row["label"] + ".release")
            if row["event"] == "start" and not release.exists():
                release.touch()
                return
        assert time.monotonic() < deadline, "実行枠取得後の開始通知が届かない"
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
    stop = mock.Mock(return_value=None, side_effect=RuntimeError("停止失敗") if stop_failure else None)
    monkeypatch.setattr(process_tree, "stop_owned_process", stop)

    with pytest.raises(OSError, match="ストリーム回収失敗"), _process([], {}, tmp_path):
        pass

    stop.assert_called_once_with(process, context="process_scope")
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()


def _environment(root: pathlib.Path) -> dict[str, str]:
    return {**os.environ, "PYFLTR_CACHE_DIR": str(root / "pyfltr-cache")}


def test_pytest_collect_only_does_not_wait_for_execution_slot(tmp_path: pathlib.Path) -> None:
    """収集だけのpytestは、別の検証が実行枠を保持していても待たずに終了する。"""
    environment = _environment(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample_test.py").write_text("def test_sample():\n    pass\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts=['-p', 'agent_toolkit._testing.pytest_plugin']\n", encoding="utf-8"
    )
    argv = ["-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q", str(project / "sample_test.py")]
    with contextlib.ExitStack() as stack:
        holder = stack.enter_context(_process(_driver("hold", tmp_path, "holder"), environment, project))
        _wait(tmp_path / "holder.ready", holder)
        collecting = stack.enter_context(_process(_driver("entry", tmp_path, "collect", *argv), environment, project))
        stdout, stderr = collecting.communicate(timeout=60)
        assert collecting.returncode == 0, f"標準出力:\n{stdout}\n標準エラー:\n{stderr}"
        assert "sample_test.py::test_sample" in stdout
        assert not (tmp_path / "collect.waiting").exists()
        (tmp_path / "holder.release").touch()
        assert holder.wait(timeout=10) == 0


def test_two_independent_verifications_run_together_and_third_waits(tmp_path: pathlib.Path) -> None:
    """枠2つの構成では、独立した2件の検証は待たずに並行し、3件目は空きができるまで待つ。"""
    environment = {**_environment(tmp_path), "PYFLTR_EXECUTION_SLOTS": "2"}
    project = tmp_path / "project"
    project.mkdir()
    with contextlib.ExitStack() as stack:
        first = stack.enter_context(_process(_driver("hold", tmp_path, "first"), environment, project))
        _wait(tmp_path / "first.ready", first)
        second = stack.enter_context(_process(_driver("hold", tmp_path, "second"), environment, project))
        _wait(tmp_path / "second.ready", second)
        third = stack.enter_context(_process(_driver("hold", tmp_path, "third"), environment, project))
        # 3件目は2つの枠が埋まっている間は取得できない。待機列への登録を待ってから、未取得を確かめる。
        deadline = time.monotonic() + 30
        while not any((tmp_path / "pyfltr-cache" / "locks").glob("execution.wait-*")):
            assert third.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        assert not (tmp_path / "third.ready").exists()
        (tmp_path / "first.release").touch()
        assert first.wait(timeout=10) == 0
        _wait(tmp_path / "third.ready", third)
        for label, process in (("second", second), ("third", third)):
            (tmp_path / f"{label}.release").touch()
            assert process.wait(timeout=10) == 0


def _driver(mode: str, root: pathlib.Path, label: str, *argv: str) -> list[str]:
    return [sys.executable, str(_DRIVER), mode, str(root), label, *argv]


@pytest.mark.parametrize("failure", ["exit-before-ready", "body"])
def test_process_reclaims_real_child_and_pipes_on_failure(tmp_path: pathlib.Path, failure: str) -> None:
    """ready待機中の終了とwith本体の例外でも、元の診断と実子・両パイプの回収を保持する。"""
    script = tmp_path / "child.py"
    script.write_text(
        "import pathlib, sys, time\n"
        "print('child-stdout', flush=True)\n"
        "print('child-stderr', file=sys.stderr, flush=True)\n"
        "if sys.argv[1] == 'exit-before-ready':\n"
        "    sys.exit(7)\n"
        "pathlib.Path(sys.argv[2]).touch()\n" + late_descendant.held_source(tmp_path / "failure.release"),
        encoding="utf-8",
    )
    ready = tmp_path / "child.ready"
    expected = "同期点より前に終了した.*child-stdout.*child-stderr" if failure == "exit-before-ready" else "本体の例外"
    with (
        pytest.raises(RuntimeError, match=expected),
        _process([sys.executable, str(script), failure, str(ready)], _environment(tmp_path), tmp_path) as process,
    ):
        _wait(ready, process)
        raise RuntimeError("本体の例外")
    assert process.poll() is not None
    assert process.returncode == 7 if failure == "exit-before-ready" else process.returncode != 0
    assert process.stdout is not None and process.stdout.closed
    assert process.stderr is not None and process.stderr.closed


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
        for _ in commands:
            _release_probe(events)
        for label, process in running.items():
            stdout, stderr = process.communicate(timeout=40)
            # タプルのreprでは子pytestのtraceback中央が省略されるため、診断は文字列で渡す。
            assert process.returncode == 0, f"{label}\n標準出力:\n{stdout}\n標準エラー:\n{stderr}"
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
# 実serveのObserverが停止・joinを終えてOSの監視資源を解放してから、次の独立検証へ進むことを確かめる。
@pytest.mark.real_observer
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
        _release_probe(events)
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
