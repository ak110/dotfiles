"""batchの稼働判定を、保存identityとargv探索の境界から検証する。"""

from typing import Any

import psutil
import pytest

from agent_toolkit._atk import run_skill_batch
from agent_toolkit._testing.process_observation import ObservedProcess


@pytest.mark.parametrize("route", ["identity", "argv"])
@pytest.mark.parametrize("observation", ["running", "zombie", "dead", "pid-reused", "access-denied"])
def test_batch_live_checks_process_state_on_both_routes(monkeypatch, tmp_path, route, observation):
    """WI書込を伴うdispatchから分離し、実装のProcessIdentityも含む稼働判定を直接観測する。"""
    manifest: dict[str, Any] = {"manifest_path": str(tmp_path / "manifest.json"), "processes": []}
    process = ObservedProcess(state=observation, arguments=("codex", f"batch manifest: {manifest['manifest_path']}"))
    current = ObservedProcess(pid=2147483001)
    monkeypatch.setattr(psutil, "Process", lambda pid=None, **_kwargs: current if pid is None else process)
    monkeypatch.setattr(psutil, "process_iter", lambda **_kwargs: iter([process] if route == "argv" else []))
    if route == "identity":
        manifest["processes"] = [{"pid": process.pid, "create_time": 42.0}]
    expected = observation == "running" or route == "identity" and observation == "access-denied"
    # argv探索は保存identityを持たず、別世代でも同じmanifestを実行するプロセスは稼働候補になる。
    if route == "argv" and observation == "pid-reused":
        expected = True
    assert run_skill_batch._live(manifest) is expected  # pylint: disable=protected-access
