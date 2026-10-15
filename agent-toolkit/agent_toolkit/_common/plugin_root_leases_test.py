"""共有leaseの使用登録と生存判定を、PID再利用・記録破損・取得拒否の条件で検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._common import plugin_root_leases as subject
from agent_toolkit._common import state_paths


@pytest.mark.parametrize("observation", ["live", "broken-json", "pid-reused", "access-denied"])
def test_plugin_root_in_use_distinguishes_live_stale_and_unknown(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    observation: str,
) -> None:
    """生存中と取得不能の使用記録を保持し、PID再利用だけを失効として回収する。"""
    area = tmp_path / "plugin-root"
    area.mkdir()
    with subject.plugin_root_lock(area):
        subject.register_plugin_root_use(area)
        assert subject.plugin_root_in_use(area)
    records = list((state_paths.state_dir() / "plugin-root-leases").glob("*.json"))
    assert len(records) == 1
    if observation == "broken-json":
        records[0].write_text("{", encoding="utf-8")
    elif observation == "pid-reused":
        record = json.loads(records[0].read_text(encoding="utf-8"))
        record["created_at"] = 0
        records[0].write_text(json.dumps(record), encoding="utf-8")
    elif observation == "access-denied":

        def denied(*_args: object, **_kwargs: object) -> object:
            raise subject.psutil.AccessDenied()

        monkeypatch.setattr(subject.psutil, "Process", denied)

    with subject.plugin_root_lock(area):
        in_use = subject.plugin_root_in_use(area)
    assert in_use is (observation != "pid-reused")
    assert records[0].exists() is in_use
