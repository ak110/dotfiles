from typing import Any

import psutil
import pytest

from agent_toolkit._atk import run_skill_batch
from agent_toolkit._common import process_identity
from agent_toolkit._testing.process_observation import ObservedProcess


@pytest.mark.parametrize("route", ["identity", "argv"])
@pytest.mark.parametrize("observation", ["running", "zombie", "dead", "pid-reused", "access-denied"])
def test_batch_live_checks_process_state_on_both_routes(monkeypatch, tmp_path, route, observation):
    manifest: dict[str, Any] = {"manifest_path": str(tmp_path / "manifest.json"), "processes": []}
    process = ObservedProcess(state=observation, arguments=("codex", f"batch manifest: {manifest['manifest_path']}"))
    current = ObservedProcess(pid=2147483001)
    monkeypatch.setattr(process_identity, "start_id", lambda process, **kwargs: f"test:{process.create_time()}")
    monkeypatch.setattr(psutil, "Process", lambda pid=None, **_kwargs: current if pid is None else process)
    if route == "argv":
        monkeypatch.setattr(psutil, "process_iter", lambda **_kwargs: iter([process]))
    else:
        monkeypatch.setattr(psutil, "process_iter", lambda **_kwargs: pytest.fail("全プロセスを列挙した"))
    if route == "identity":
        manifest["processes"] = [{"pid": process.pid, "create_time": 42.0, "start_id": "test:42.0"}]
    expected = observation == "running" or route == "identity" and observation == "access-denied"

    if route == "argv" and observation == "pid-reused":
        expected = True
    assert run_skill_batch.execution_live(manifest) is expected
