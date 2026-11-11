"""共有leaseの使用登録と生存判定を、PID再利用・記録破損・取得拒否の条件で検証する。"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Any

import pytest

from agent_toolkit._common import plugin_root_leases as subject
from agent_toolkit._common import state_paths

pytestmark = pytest.mark.windows


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
        record["start_id"] = "other-generation"
        records[0].write_text(json.dumps(record), encoding="utf-8")
    elif observation == "access-denied":

        def denied(*_args: object, **_kwargs: object) -> object:
            raise subject.psutil.AccessDenied()

        monkeypatch.setattr(subject.psutil, "Process", denied)

    with subject.plugin_root_lock(area):
        in_use = subject.plugin_root_in_use(area)
    assert in_use is (observation != "pid-reused")
    assert records[0].exists() is in_use


@pytest.mark.parametrize("observation", ["direct", "unattributed", "denied", "vanished", "other-user", "zombie", "dead"])
@pytest.mark.parametrize("shared", [False, True])
def test_legacy_plugin_root_cleanup_checks_unregistered_consumers(tmp_path, monkeypatch, observation, shared):
    """旧形式も共有形式も、直接参照を保護し、無帰属のプロセスへは帰属させない。"""
    area = tmp_path / ("agents-server-plugin-root-shared-current" if shared else "agents-server-plugin-root-legacy")
    area.mkdir()
    own = subject.psutil.Process()

    class Consumer:
        pid = 123

        def create_time(self):
            return 1.0

        def username(self):
            return "other-user" if observation == "other-user" else own.username()

        def status(self):
            return {"zombie": subject.psutil.STATUS_ZOMBIE, "dead": subject.psutil.STATUS_DEAD}.get(
                observation, subject.psutil.STATUS_RUNNING
            )

        def cmdline(self):
            if observation == "denied":
                raise subject.psutil.AccessDenied(self.pid)
            if observation == "vanished":
                raise subject.psutil.NoSuchProcess(self.pid)
            target = (
                "/unrelated/agent_toolkit/agents_server_mcp.py"
                if observation == "unattributed"
                else str(area / "agent_toolkit/agents_server_mcp.py")
            )
            return ["python", target]

    consumer = Consumer()
    monkeypatch.setattr(subject.process_identity, "start_id", lambda process, **kwargs: f"test:{process.create_time()}")
    monkeypatch.setattr(subject.psutil, "process_iter", lambda: [consumer])
    monkeypatch.setattr(subject.psutil, "Process", lambda pid=None: consumer if pid == consumer.pid else own)
    assert subject.plugin_root_in_use(area) is (observation in {"direct", "denied"})


def test_nonblocking_lock_reports_conflict(tmp_path: pathlib.Path) -> None:
    """非待機の取得は保持中のロックを待たずに失敗を返し、解放後は非待機でも取得できる。"""
    area = tmp_path / "plugin-root"
    area.mkdir()
    with subject.plugin_root_lock(area), pytest.raises(OSError), subject.plugin_root_lock(area, blocking=False):
        pytest.fail("保持中のロックを非待機で取得した")
    with subject.plugin_root_lock(area, blocking=False):
        pass


def test_legacy_area_not_attributed_to_unreferencing_mcp(tmp_path, monkeypatch):
    """使用の記録も直接参照もないMCPは旧形式の保持根拠にならない。"""
    area = tmp_path / "agents-server-plugin-root-legacy"
    area.mkdir()
    process = subject.psutil.Process()
    monkeypatch.setattr(subject.psutil, "process_iter", lambda: [process])
    monkeypatch.setattr(type(process), "cmdline", lambda self: ["python", "/source/agent_toolkit/agents_server_mcp.py"])
    assert not subject.plugin_root_in_use(area)
    with subject.plugin_root_lock(area):
        subject.register_plugin_root_use(area)
        assert subject.plugin_root_in_use(area)


def test_legacy_area_judgement_reads_current_generation_despite_cached_previous_generation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """列挙キャッシュに以前の世代の起動時刻が残っていても、直接参照の保持根拠を現在の起動時刻で記録する。"""
    area = tmp_path / "agents-server-plugin-root-legacy"
    area.mkdir()
    process = subject.psutil.Process()
    created_at = process.create_time()
    # PIDの自然な再利用を待たず、process_iterが再利用するProcessだけを以前の世代にする。
    stale = subject.psutil.Process(process.pid)
    monkeypatch.setattr(stale, "_create_time", created_at - 10000)
    monkeypatch.setattr(stale, "_ident", (process.pid, created_at - 10000))
    monkeypatch.setitem(vars(subject.psutil)["_pmap"], process.pid, stale)
    real_process_iter = subject.psutil.process_iter

    def own_process(*args: Any, **kwargs: Any) -> list[Any]:
        return [item for item in real_process_iter(*args, **kwargs) if item.pid == process.pid]

    monkeypatch.setattr(subject.psutil, "process_iter", own_process)
    monkeypatch.setattr(type(process), "cmdline", lambda self: ["python", str(area / "agent_toolkit/agents_server_mcp.py")])

    assert subject.plugin_root_in_use(area)
    basis = subject.usage_basis()
    assert basis is not None
    assert basis["created_at"] == created_at
    assert subject.usage_basis_is_current(basis)

    # 1回の掃引で共有する列挙も、現在の世代のProcessを保持する。
    with subject.reuse_process_observation():
        assert subject.plugin_root_in_use(area)
        assert subject.plugin_root_in_use(area)


@pytest.mark.parametrize(
    ("status", "reported", "expected"),
    [
        ("failed", True, True),
        ("completed", False, True),
        ("completed", True, False),
    ],
)
def test_submission_runtime_reference_outlives_process_lease(tmp_path, status, reported, expected):
    area = tmp_path / "held-plugin"
    area.mkdir()
    run = state_paths.state_dir() / "wi-submit/runs" / ("a" * 32) / "run.json"
    run.parent.mkdir(parents=True)
    run.write_text(
        json.dumps(
            {
                "runtime_root": str(area / "agent-toolkit"),
                "status": status,
                "report_synced": reported,
            }
        ),
        encoding="utf-8",
    )
    with subject.plugin_root_lock(area):
        assert subject.plugin_root_in_use(area) is expected


@pytest.mark.parametrize("kind", ["lease", "capability"])
def test_sweep_removes_records_of_exited_processes_only(tmp_path, monkeypatch, kind):
    """終了・PID再利用だけを掃引し、生存・取得不能・破損と競合中のロックは残す。"""
    directory = state_paths.state_dir() / "plugin-root-leases"
    directory.mkdir(parents=True, exist_ok=True)
    original = subject.psutil.Process
    own = original()
    names = {}
    for index, state in enumerate(["live", "exited", "reused", "denied", "broken"], 1):
        key = hashlib.sha256(state.encode()).hexdigest()
        name = f"capability-{index}.json" if kind == "capability" else f"{key}-{index}.json"
        record: dict[str, object] = {"pid": index, "created_at": 42.0, "start_id": "test:42.0"}
        record.update({"lease_capable": True} if kind == "capability" else {"path": str(tmp_path / state)})
        path = directory / name
        path.write_text("{" if state == "broken" else json.dumps(record), encoding="utf-8")
        lock = directory / f"{key}.lock"
        lock.touch()
        names[state] = (path, lock)

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            if self.pid == 2:
                raise subject.psutil.NoSuchProcess(self.pid)
            if self.pid == 4:
                raise subject.psutil.AccessDenied(self.pid)
            return 41.0 if self.pid == 3 else 42.0

        def status(self):
            return subject.psutil.STATUS_RUNNING

    monkeypatch.setattr(subject.psutil, "process_iter", lambda: [Process(i) for i in range(1, 5)])
    monkeypatch.setattr(subject.psutil, "Process", lambda pid=None: own if pid is None else Process(pid))
    monkeypatch.setattr(subject.process_identity, "start_id", lambda process, **kwargs: f"test:{process.create_time()}")
    busy = tmp_path / "busy"
    with subject.plugin_root_lock(busy):
        with subject.reuse_process_observation():
            subject.sweep_plugin_root_records()
        key = hashlib.sha256(os.fsencode(os.path.normcase(os.path.abspath(busy)))).hexdigest()
        assert (directory / f"{key}.lock").exists()
    for state, (path, lock) in names.items():
        assert path.exists() is (state not in {"exited", "reused"})
        # 対応能力の記録は変更前の版の生存を示す。生存・取得不能・破損が残る間はロックを全て残す。
        assert lock.exists() is (kind == "capability" or state not in {"exited", "reused"})
    if kind == "capability":
        for path, _lock in names.values():
            path.unlink(missing_ok=True)
    subject.sweep_plugin_root_records()
    assert not (directory / f"{key}.lock").exists()
    if kind == "capability":
        assert not any(lock.exists() for _path, lock in names.values())


@pytest.mark.skipif(os.name == "nt", reason="Windowsは開いたロックファイルの削除を拒否する")
def test_sweep_keeps_locks_while_legacy_server_may_hold_them(tmp_path):
    """変更前の版がロックを開いてから取得するまでの間に掃引しても、新旧が別々に排他を得ない。"""
    directory = state_paths.state_dir() / "plugin-root-leases"
    directory.mkdir(parents=True, exist_ok=True)
    own = subject.psutil.Process()
    # 変更前の版のサーバーが起動時に書く対応能力の記録。生存中の自プロセスを変更前の版とみなす。
    (directory / f"capability-{own.pid}.json").write_text(
        json.dumps({"pid": own.pid, "created_at": own.create_time(), "lease_capable": True}), encoding="utf-8"
    )
    area = tmp_path / "area"
    key = hashlib.sha256(os.fsencode(os.path.normcase(os.path.abspath(area)))).hexdigest()
    path = directory / f"{key}.lock"
    # 変更前の版の取得手順（開いてから取得し、開いたファイルが同じパスに残るかを確かめない）を再現する。
    with path.open("a+", encoding="utf-8") as legacy:
        subject.sweep_plugin_root_records()
        assert path.exists()
        subject.file_lock.acquire_lock(legacy, blocking=False)
        try:
            with pytest.raises(OSError), subject.plugin_root_lock(area, blocking=False):
                pytest.fail("変更前の版が保持するロックとは別に排他を取得した")
        finally:
            subject.file_lock.release_lock(legacy)


@pytest.mark.skipif(os.name == "nt", reason="Windowsは開いたロックファイルの削除を拒否する")
def test_lock_is_reacquired_when_removed_during_wait(tmp_path, monkeypatch):
    """取得待ちで掃引されたファイルのロックを返さず、現在のファイルで排他する。"""
    acquire = subject.file_lock.acquire_lock
    attempts = []

    def remove_during_acquire(handle, **kwargs):
        attempts.append(handle.name)
        if len(attempts) == 1:
            pathlib.Path(handle.name).unlink()
        acquire(handle, **kwargs)

    monkeypatch.setattr(subject.file_lock, "acquire_lock", remove_during_acquire)
    with subject.plugin_root_lock(tmp_path / "area"):
        assert len(attempts) == 2
        with pytest.raises(OSError), subject.plugin_root_lock(tmp_path / "area", blocking=False):
            pytest.fail("削除されたロックとは別に排他を取得した")
