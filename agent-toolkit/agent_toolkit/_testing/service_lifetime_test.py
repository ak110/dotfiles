from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time

import psutil
import pytest

from agent_toolkit._common import process_identity, process_tree
from agent_toolkit._testing import late_descendant, service_lifetime

_WORKER = """
import dataclasses,json,pathlib,subprocess,sys
import psutil
from agent_toolkit._common import process_identity
from agent_toolkit._testing import service_lifetime
base, marker, outcome, child, namespace = sys.argv[1:]
with service_lifetime.guard(pathlib.Path(base)):
    launchers = [
        subprocess.Popen([sys.executable, "-c", child, *args, "agent_toolkit/_agents_server/service.py",
                          namespace, "test-generation"])
        for args in (["--agents-service"], [])
    ]
    path = pathlib.Path(marker)
    draft = path.with_suffix(".tmp")
    draft.write_text(json.dumps([dataclasses.asdict(process_identity.ProcessIdentity.capture(psutil.Process(p.pid)))
                                 for p in launchers]))
    draft.replace(path)
    if outcome == "interrupted":
        sys.stdin.buffer.read()
    if outcome == "failure":
        raise SystemExit(3)
"""


def _namespace(state_base: pathlib.Path) -> str:
    return str(state_base / "agent-toolkit" / "services" / "agents-server")


@pytest.mark.parametrize("outcome", ["success", "failure", "interrupted"])
def test_guard_reclaims_only_the_workers_service(tmp_path: pathlib.Path, outcome: str) -> None:
    owned = tmp_path / "owned"
    marker = tmp_path / "identities.json"
    service = "agent_toolkit/_agents_server/service.py"
    child = late_descendant.held_source(tmp_path / "release")
    with (
        late_descendant.held_process(tmp_path, service, _namespace(tmp_path / "other"), "generation") as other,
        service_lifetime.guard(owned),
        process_tree.process_scope(
            [sys.executable, "-c", _WORKER, str(owned), str(marker), outcome, child, _namespace(owned)],
            stdin=subprocess.PIPE,
        ) as worker,
    ):
        deadline = time.monotonic() + 20
        while not marker.is_file():
            assert worker.poll() is None and time.monotonic() < deadline, "所有プロセスを取得できない"
            time.sleep(0.02)
        identities = [process_identity.ProcessIdentity.parse(record) for record in json.loads(marker.read_text())]
        if outcome == "interrupted":
            worker.kill()
        status = worker.wait(timeout=45)
        if outcome != "interrupted":
            assert status == (3 if outcome == "failure" else 0)
        while any(_running(identity) for identity in identities):
            assert time.monotonic() < deadline + 45, "ワーカー終了後も管理プロセスが残る"
            time.sleep(0.02)
        assert other.poll() is None


def _running(identity: process_identity.ProcessIdentity) -> bool:
    try:
        process = psutil.Process(identity.pid)
        return identity.matches(process) and process.status() not in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}
    except psutil.NoSuchProcess:
        return False
