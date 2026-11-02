"""実行終了の状態とPID同一性を、登録・保持・活動・回収の公開操作で比較する。"""

import json
import typing

import psutil
import pytest

from agent_toolkit._common import plugin_root_leases, process_tree, session_activity, session_registry, state_paths


@pytest.mark.parametrize("observation", ["running", "zombie", "dead", "pid-reused", "access-denied"])
@pytest.mark.parametrize("consumer", ["lease-basis", "lease-use", "identity", "activity", "reclaim"])
def test_process_state_consumers_preserve_identity_and_end_states(tmp_path, monkeypatch, observation, consumer):
    """同じOS状態でも、終了・同一性・取得不能の各契約を区別する。"""
    original = psutil.Process
    username = original().username()
    value = {"state": "running"}

    class ObservedProcess:
        """PIDを再利用した場合と、一時的な終了状態を決定論的に供給する。"""

        pid = 2147483000

        def create_time(self):
            return 43.0 if value["state"] == "pid-reused" else 42.0

        def status(self):
            if value["state"] == "access-denied":
                raise psutil.AccessDenied(self.pid)
            return psutil.STATUS_RUNNING if value["state"] == "pid-reused" else value["state"]

        def username(self):
            return username

        def name(self):
            return "codex"

        def exe(self):
            return str(tmp_path / "codex")

        def is_running(self):
            return value["state"] != "pid-reused"

        def terminate(self):
            pass

        def kill(self):
            pass

    process = ObservedProcess()

    def observe(pid=None, **kwargs):
        return process if pid == process.pid else original(pid, **kwargs)

    monkeypatch.setattr(psutil, "Process", observe)
    monkeypatch.setattr(psutil, "process_iter", lambda **kwargs: iter([process]))
    if consumer in {"identity", "activity", "reclaim"}:
        value["state"] = observation
        if consumer == "identity":
            assert session_registry.ProcessIdentity(process.pid, 42.0).exists() is (observation in {"running", "access-denied"})
        elif consumer == "activity":
            assert bool(session_activity.host_processes()) is (observation not in {"zombie", "dead"})
        else:
            assert bool(process_tree.reclaim_processes([typing.cast(psutil.Process, process)], timeout=0)) is (
                observation == "running"
            )
        return
    area = tmp_path / "plugin-root"
    area.mkdir()
    with plugin_root_leases.plugin_root_lock(area):
        plugin_root_leases.register_plugin_root_use(area)
        record = next((state_paths.state_dir() / "plugin-root-leases").glob("*.json"))
        raw = json.loads(record.read_text(encoding="utf-8"))
        raw.update(pid=process.pid, created_at=42.0)
        record.write_text(json.dumps(raw), encoding="utf-8")
        assert plugin_root_leases.plugin_root_in_use(area)
        basis = plugin_root_leases.usage_basis()
        assert basis is not None
        value["state"] = observation
        if consumer == "lease-basis":
            assert plugin_root_leases.usage_basis_is_current(basis) is (observation == "running")
        else:
            assert plugin_root_leases.plugin_root_in_use(area) is (observation in {"running", "access-denied"})
