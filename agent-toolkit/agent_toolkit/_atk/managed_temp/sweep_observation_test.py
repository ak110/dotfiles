from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp import auto_sweep, paths, sweep
from agent_toolkit._common import plugin_root_leases

pytestmark = pytest.mark.windows


@pytest.mark.parametrize("route", ["daily", "pressure", "forced", "worker"])
def test_sweep_reuses_process_observation_for_distinct_plugin_roots(monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    now = datetime.datetime.now(datetime.UTC)
    monkeypatch.setenv(sweep.MIN_FREE_RATIO_ENV, "0.10")
    if route in {"pressure", "forced", "worker"}:
        sweep._save_sweep_time(paths.state_root(), sweep._now_ns(now))  # pylint: disable=protected-access
    monkeypatch.setattr(sweep, "_disk_usage", lambda _: SimpleNamespace(total=100, free=5))
    scans: list[None] = []
    entries: list[bool] = []

    def observe() -> list[object]:
        scans.append(None)
        return []

    def inspect(*_args: object, daily: bool, **_kwargs: object) -> sweep.SweepResult:
        entries.append(daily)
        for name in ("one", "two", "three"):
            area = paths.temp_root() / f"agents-server-plugin-root-shared-{name}"
            assert not plugin_root_leases._server_may_use_root(area)  # pylint: disable=protected-access
        return sweep.SweepResult([], ())

    monkeypatch.setattr(plugin_root_leases, "_current_processes", observe)
    monkeypatch.setattr(sweep, "_sweep_locked", inspect)
    if route == "worker":
        auto_sweep._run_worker()  # pylint: disable=protected-access
    else:
        managed_temp.sweep_managed_temp(now=now, force=route == "forced")

    assert entries == [route in {"daily", "forced"}]
    assert len(scans) == 1
