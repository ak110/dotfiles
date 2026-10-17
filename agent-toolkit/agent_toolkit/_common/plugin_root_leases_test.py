"""共有leaseの使用登録と生存判定を、PID再利用・記録破損・取得拒否の条件で検証する。"""

from __future__ import annotations

import json
import pathlib
import types
from typing import Any

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


@pytest.mark.parametrize(
    "observation",
    [
        "live",
        "other-namespace",
        "denied",
        "vanished",
        "other-user",
        "zombie",
        "missing-log",
        "denied-files",
        "wrong-log",
        "starting-wrapper",
        "non-mcp-account-denied",
        "non-mcp-status-denied",
        "foreign-cmdline-denied",
        "account-and-cmdline-denied",
        "foreign-cmdline-oserror",
        "same-account-cmdline-oserror",
    ],
)
@pytest.mark.parametrize("shared", [False, True])
def test_legacy_plugin_root_cleanup_checks_unregistered_consumers(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    observation: str,
    shared: bool,
) -> None:
    """leaseを持たない旧領域も共通の回収判定で保護し、不在を確定した後に解放する。"""
    area = tmp_path / ("agents-server-plugin-root-shared-current" if shared else "agents-server-plugin-root-legacy")
    area.mkdir()
    (area / "old-version").mkdir()
    real_process = subject.psutil.Process()
    state = tmp_path / "other/agent-toolkit" if observation == "other-namespace" else state_paths.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    log = state / "agents-server.log"
    log.touch()

    class LegacyServer:
        """OSから得る起動引数と、参照不能・終了・別名前空間の境界を供給する。"""

        def username(self) -> str:
            if observation in {"non-mcp-account-denied", "account-and-cmdline-denied"}:
                raise subject.psutil.AccessDenied()
            return (
                "other-user"
                if observation in {"other-user", "foreign-cmdline-denied", "foreign-cmdline-oserror"}
                else real_process.username()
            )

        def status(self) -> str:
            if observation == "non-mcp-status-denied":
                raise subject.psutil.AccessDenied()
            return subject.psutil.STATUS_ZOMBIE if observation == "zombie" else subject.psutil.STATUS_RUNNING

        def cmdline(self) -> list[str]:
            if observation in {"foreign-cmdline-oserror", "same-account-cmdline-oserror"}:
                raise OSError("synthetic process observation failure")
            if observation in {"denied", "foreign-cmdline-denied", "account-and-cmdline-denied"}:
                raise subject.psutil.AccessDenied()
            if observation == "vanished":
                raise subject.psutil.NoSuchProcess(123)
            if observation == "starting-wrapper":
                return ["uv", "run", "--project", str(area / "old-version"), "agent_toolkit/agents_server_mcp.py"]
            if observation in {"non-mcp-account-denied", "non-mcp-status-denied"}:
                return ["python", "/unrelated/helper.py"]
            return ["python", "/removed/source/agent_toolkit/agents_server_mcp.py"]

        def open_files(self) -> list[types.SimpleNamespace]:
            if observation == "missing-log":
                return []
            if observation == "denied-files":
                raise subject.psutil.AccessDenied()
            if observation == "wrong-log":
                return [types.SimpleNamespace(path=str(tmp_path / "arbitrary/agents-server.log"))]
            return [types.SimpleNamespace(path=str(log))]

    def processes(**_kwargs: Any) -> list[LegacyServer]:
        return [LegacyServer()]

    monkeypatch.setattr(subject.psutil, "process_iter", processes)
    protected = (
        observation in {"denied", "starting-wrapper", "account-and-cmdline-denied", "same-account-cmdline-oserror"}
        or not shared
        and observation in {"live", "missing-log", "denied-files", "wrong-log"}
    )
    assert subject.plugin_root_in_use(area) is protected


def test_nonblocking_lock_reports_conflict(tmp_path: pathlib.Path) -> None:
    """非待機の取得は保持中のロックを待たずに失敗を返し、解放後は非待機でも取得できる。"""
    area = tmp_path / "plugin-root"
    area.mkdir()
    with subject.plugin_root_lock(area), pytest.raises(OSError), subject.plugin_root_lock(area, blocking=False):
        pytest.fail("保持中のロックを非待機で取得した")
    with subject.plugin_root_lock(area, blocking=False):
        pass
