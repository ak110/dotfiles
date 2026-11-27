import collections.abc
import contextlib
import json
import os
import signal
import subprocess
import sys
import time

import pytest
from pyfltr.command import execution_slot

from agent_toolkit._common import process_tree, verification_slot
from agent_toolkit._testing import git_repository

_DRIVER = """\
import json, os, pathlib, sys, time
from pyfltr.command import execution_slot
from agent_toolkit._common import verification_slot
from agent_toolkit._atk import run_command, run_script
root = pathlib.Path(sys.argv[1])
role, mode = sys.argv[2:4]

def stamp(name):
    (root / name).write_text(str(time.monotonic()))

def wait(name):
    deadline = time.monotonic() + 25
    while not (root / name).exists():
        if time.monotonic() > deadline: raise TimeoutError(name)
        time.sleep(.01)

stamp(role + '.accepted')
if role.startswith('holder'):
    with execution_slot.hold(label=role):
        stamp(role + '.started')
        wait('release-' + role)
        stamp(role + '.ended')
elif role == 'integration':
    if mode == 'baseline':
        def fifo(**kw):
            return execution_slot.hold(label=kw['label'], on_wait_interval=kw['on_wait_interval'])
        run_command.verification_slot.hold = fifo
    run_command.managed_temp.create_output_temp = lambda prefix: root / 'record'
    (root / 'record').mkdir()
    result, code, failure = run_command.execute(
        [sys.executable, __file__, str(root), 'probe-integration', mode], root, 25, verification=True
    )
    (root / 'integration.json').write_text(json.dumps(result))
    raise SystemExit(code)
elif role == 'commit':
    os.chdir(root / 'repo')
    argv = ([sys.executable, str(run_script.registered_script_path('commit-with-timeout')),
             '--timeout', '25', '--', '--allow-empty', '-m', 'probe'] if os.name == 'posix' else
            ['git', 'commit', '--allow-empty', '-m', 'probe'])
    raise SystemExit(verification_slot.main(['--priority', 'normal', '--', *argv]))
elif role == 'urgent':
    if mode == 'cancel':
        try:
            with verification_slot.hold(
                priority='urgent', should_abort=lambda: (root / 'cancel').exists(),
                on_wait=lambda owner: stamp('urgent.queued'),
            ):
                raise AssertionError('cancelled request started')
        except execution_slot.WaitAborted:
            raise SystemExit(130)
    priority = 'normal' if mode == 'baseline' else 'urgent'
    command = ([str(root / 'missing-executable')] if mode == 'spawn-failure' else
               [sys.executable, __file__, str(root), 'probe-urgent', mode])
    raise SystemExit(verification_slot.main(['--priority', priority, '--', *command]))
else:
    name = role.removeprefix('probe-')
    stamp(name + '.started')
    if name == 'urgent': wait('release-urgent')
    stamp(name + '.ended')
    raise SystemExit(7 if mode == 'failure' and name == 'urgent' else 0)
"""


def _wait(predicate: collections.abc.Callable[[], bool], children: list[subprocess.Popen]) -> None:
    deadline = time.monotonic() + 20
    while not predicate():
        assert time.monotonic() < deadline, [(child.args, child.poll()) for child in children]
        time.sleep(0.01)


@pytest.mark.parametrize(
    "mode",
    [
        "baseline",
        "pass",
        "cancel",
        "failure",
        "spawn-failure",
        pytest.param("cancel-signal", marks=pytest.mark.skipif(os.name != "posix", reason="SIGTERMの捕捉はPOSIXで観測する")),
    ],
)
@pytest.mark.parametrize("slots", [1, 2])
@pytest.mark.cpu_limit(2, reason="隔離した実Gitと最大5子プロセスの検証枠の開始・終了・取消を観測する")
def test_priority_preserves_holders_and_resumes_normal_requests(tmp_path, host_environ, mode, slots, request):
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    repo = tmp_path / "repo"
    git_repository.init_repository(repo)
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text(f'#!/bin/sh\n"{sys.executable}" "{driver}" "{tmp_path}" probe-commit {mode}\n', encoding="utf-8")
    hook.chmod(0o755)
    environment = {**host_environ(), "PYFLTR_CACHE_DIR": str(tmp_path / "cache"), "PYFLTR_EXECUTION_SLOTS": str(slots)}
    lock_dir = tmp_path / "cache/locks"
    children = []
    with contextlib.ExitStack() as stack:

        def start(role):
            child = stack.enter_context(
                subprocess.Popen([sys.executable, str(driver), str(tmp_path), role, mode], env=environment)
            )
            children.append(child)
            stack.callback(process_tree.stop_process_tree, child)
            return child

        holders = [start(f"holder{index}") for index in range(slots)]
        _wait(lambda: all((tmp_path / f"holder{index}.started").exists() for index in range(slots)), children)
        commit = start("commit")
        _wait(lambda: execution_slot.waiter_count(lock_dir) == 1, children)
        integration = start("integration")
        _wait(lambda: execution_slot.waiter_count(lock_dir) == 2, children)
        urgent = start("urgent")
        _wait(lambda: execution_slot.waiter_count(lock_dir) == 3, children)
        assert not (tmp_path / "commit.started").exists()
        assert not (tmp_path / "integration.started").exists()
        if mode == "cancel-signal":
            urgent.send_signal(signal.SIGTERM)
            assert urgent.wait(timeout=20) == 130
        if mode == "cancel":
            (tmp_path / "cancel").touch()
            assert urgent.wait(timeout=20) == 130
        (tmp_path / "release-holder0").touch()
        if mode in {"pass", "failure"}:
            _wait((tmp_path / "urgent.started").exists, children)
            if slots == 1:
                assert not (tmp_path / "integration.started").exists()
                assert not (tmp_path / "commit.started").exists()
        for index in range(1, slots):
            assert holders[index].poll() is None
            (tmp_path / f"release-holder{index}").touch()
        (tmp_path / "release-urgent").touch()
        assert (
            urgent.wait(timeout=20)
            == {"baseline": 0, "pass": 0, "cancel": 130, "failure": 7, "spawn-failure": 125, "cancel-signal": 130}[mode]
        )
        assert commit.wait(timeout=20) == integration.wait(timeout=20) == 0
        assert all(child.wait(timeout=20) == 0 for child in holders)
    stamps = {path.stem: float(path.read_text()) for path in tmp_path.glob("*.started")}
    if mode in {"pass", "failure"}:
        assert stamps["urgent"] < stamps["integration"]
        if slots == 1:
            assert stamps["urgent"] < stamps["commit"]
        assert float((tmp_path / "holder0.ended").read_text()) <= stamps["urgent"]
    if mode == "baseline":
        assert stamps["commit"] < stamps["urgent"] and stamps["integration"] < stamps["urgent"]
    metrics = {}
    for role in ("commit", "integration", "urgent"):
        accepted = float((tmp_path / f"{role}.accepted").read_text())
        started = tmp_path / f"{role}.started"
        ended = tmp_path / f"{role}.ended"
        metrics[role] = {
            "accepted": accepted,
            "started": float(started.read_text()) if started.exists() else None,
            "ended": float(ended.read_text()) if ended.exists() else None,
            "wait": float(started.read_text()) - accepted if started.exists() else None,
            "body": float(ended.read_text()) - float(started.read_text()) if ended.exists() else None,
            "attempts": 1,
        }
    request.node.user_properties.append(("comparison", json.dumps({"mode": mode, "slots": slots, "metrics": metrics})))
    assert not execution_slot.waiter_count(lock_dir)
    assert not list(lock_dir.glob("execution.wait-*"))
    record = json.loads((tmp_path / "integration.json").read_text())
    assert record["wrapper_exit_code"] == 0
    assert record["timing"]["slot_waits"][0]["outcome"] == "acquired"


def test_invalid_priority_and_command_failure(tmp_path):
    with pytest.raises(ValueError, match="優先度"), verification_slot.hold(priority="invalid", lock_dir=tmp_path):
        pytest.fail("invalid priority acquired")
    assert verification_slot.main(["--priority", "normal", "--", sys.executable, "-c", "raise SystemExit(9)"]) == 9
