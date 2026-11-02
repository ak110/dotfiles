"""記録済み模擬App Serverの状態とPID再利用を、照会と回収の双方から確認する。"""

import json

import psutil
import pytest

from agent_toolkit._testing.codex_app_server_support import FakeAppServer
from agent_toolkit._testing.process_observation import ObservedProcess


@pytest.mark.parametrize("observation", ["running", "zombie", "dead", "pid-reused", "access-denied"])
def test_recorded_server_alive_and_cleanup_use_the_same_identity(tmp_path, monkeypatch, observation):
    process = ObservedProcess(state=observation)
    log = tmp_path / "events.jsonl"
    log.write_text(json.dumps({"event": "server", "pid": process.pid, "created": 42.0}) + "\n", encoding="utf-8")
    monkeypatch.setattr(psutil, "Process", lambda _pid, **_kwargs: process)
    waited = []

    def wait_procs(targets, **_kwargs):
        waited.extend(targets)
        return targets, []

    monkeypatch.setattr(psutil, "wait_procs", wait_procs)
    server = FakeAppServer(log)
    assert server.alive(process.pid) is (observation == "running")
    server.terminate_recorded()
    assert process.calls == (["kill"] if observation == "running" else [])
    assert waited == ([process] if observation == "running" else [])
