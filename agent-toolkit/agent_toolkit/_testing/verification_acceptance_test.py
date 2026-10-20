"""公開検証コマンドの競合と、実serve・Observerの終了後の引継ぎを観測する。"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Generator

import pytest

from agent_toolkit._atk.lane_test import prepare_lane_verification
from agent_toolkit._common import process_tree
from agent_toolkit._testing import verification_acceptance_support

_DRIVER = pathlib.Path(verification_acceptance_support.__file__)
_PLUGIN = _DRIVER.parents[2]
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
    process = subprocess.Popen(  # pylint: disable=consider-using-with
        argv,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        yield process
    finally:
        try:
            residual = process_tree.stop_process_tree(process) if process.poll() is None else []
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        assert not residual, residual


def _environment(root: pathlib.Path) -> dict[str, str]:
    return {**os.environ, "PYFLTR_CACHE_DIR": str(root / "pyfltr-cache")}


def _driver(mode: str, root: pathlib.Path, label: str, *argv: str) -> list[str]:
    return [sys.executable, str(_DRIVER), mode, str(root), label, *argv]


def test_four_public_verification_entries_share_one_execution_interval(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    record_property: Callable[[str, object], None],
) -> None:
    """4種類の検証コマンドが保持者の解放を待ち、検証区間が重ならず全て終了する。"""
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
    plan = tmp_path / "plan.md"
    command = shlex.join([sys.executable, str(probe), "plan", str(events)])
    plan.write_text(
        f"# 計画\n\n## 検証\n\n| 区分 | 検証コマンド |\n| --- | --- |\n| 変更範囲の検証 | `{command}` |\n",
        encoding="utf-8",
    )
    commands = {
        "plan": [
            str(_PLUGIN / "skills/plan-mode/scripts/run_plan_verifications.py"),
            "--plan",
            str(plan),
            "--worktree",
            str(project),
            "--timeout",
            "20",
        ],
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
    assert len(intervals) == 8
    assert {row["label"] for row in intervals} == set(commands)
    assert len({row["owner"] for row in intervals}) == 4
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
        following = stack.enter_context(
            _process(
                _driver(
                    "entry",
                    tmp_path,
                    "next",
                    str(_PLUGIN / "agent_toolkit/atk.py"),
                    "run-command",
                    "--verification",
                    "--",
                    sys.executable,
                    str(probe),
                    "next",
                    str(events),
                ),
                environment,
                tmp_path,
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


def test_shared_wait_longer_than_child_timeout_keeps_exit_and_outputs(
    tmp_path: pathlib.Path, record_property: Callable[[str, object], None]
) -> None:
    """共有待機が実行上限を超えても、子を実行して終了値と両出力を保持する。"""
    environment = _environment(tmp_path)
    with contextlib.ExitStack() as stack:
        holder = stack.enter_context(_process(_driver("hold", tmp_path, "holder"), environment, tmp_path))
        _wait(tmp_path / "holder.ready", holder)
        waiting = stack.enter_context(
            _process(
                _driver(
                    "entry",
                    tmp_path,
                    "bounded",
                    str(_PLUGIN / "agent_toolkit/atk.py"),
                    "run-command",
                    "--verification",
                    "--timeout",
                    "1",
                    "--",
                    sys.executable,
                    "-c",
                    "import sys; print('retained-out'); print('retained-err', file=sys.stderr); sys.exit(7)",
                ),
                environment,
                tmp_path,
            )
        )
        _wait(tmp_path / "bounded.waiting", waiting)
        started = time.monotonic()
        time.sleep(1.2)
        assert waiting.poll() is None
        (tmp_path / "holder.release").touch()
        assert holder.wait(timeout=10) == 0
        stdout, stderr = waiting.communicate(timeout=20)
        assert waiting.returncode == 7, (stdout, stderr)
    metadata = json.loads(stdout)
    assert metadata["child_exit_code"] == metadata["wrapper_exit_code"] == 7
    assert not metadata["timed_out"] and time.monotonic() - started > 1
    output = pathlib.Path(metadata["stdout_path"]).read_text(encoding="utf-8")
    diagnostic = pathlib.Path(metadata["stderr_path"]).read_text(encoding="utf-8")
    assert output == "retained-out\n" and diagnostic == "retained-err\n"
    record_property(
        "shared_wait_observation", json.dumps({"record": metadata, "stdout": output, "stderr": diagnostic}, ensure_ascii=False)
    )
