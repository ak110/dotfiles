"""共有状態の掃引が、解放要求の一時記録と起動単位の終了診断を既存の保持期限と参照に従って回収することを確かめる。"""

import json
import os
import pathlib
import time

import pytest

from agent_toolkit._agents_server import logging_config, shared_layout, shared_sweep
from agent_toolkit._common import session_registry

_DAY = 24 * 60 * 60


def test_orphaned_release_files_follow_existing_session_sweep(tmp_path: pathlib.Path) -> None:
    """異常終了で残った要求・応答を既存session掃引が回収し、現行登録を保持する。"""
    session_registry.publish("active", terminal=False, cwd=str(tmp_path), state_root=tmp_path)
    directory = session_registry.registry_directory(tmp_path)
    now = time.time()
    records = [directory / "orphan.release-request", directory / "orphan.release-response"]
    for path in records:
        path.write_text("{}\n", encoding="utf-8")
        old = now - 8 * 24 * 60 * 60
        os.utime(path, (old, old))
    shared_sweep.sweep_stale_shared_state(keep_root_session_id=None, state_root=tmp_path, now=now)
    assert all(not path.exists() for path in records)
    assert session_registry.resolve("active", state_root=tmp_path).state is session_registry.Resolution.RUNNING


def _diagnostics(state_root: pathlib.Path, instance: str, *, age_days: float, now: float, size: int = 0) -> pathlib.Path:
    """最終更新を`age_days`日前にした起動単位の終了診断を作成する。"""
    path = logging_config.instance_path(instance, state_root)
    logging_config.append_instance_event(path, writer="server", event="started", padding="x" * size)
    stamp = now - age_days * _DAY
    os.utime(path, (stamp, stamp))
    return path


def _age(path: pathlib.Path, *, days: float, now: float) -> None:
    stamp = now - days * _DAY
    os.utime(path, (stamp, stamp))


def test_instance_diagnostics_follow_references(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """参照元が残る診断は7日を超えても保持し、全参照元の回収後の掃引で回収する。結果ファイルの参照も保持の根拠になる。"""
    monkeypatch.setattr(logging_config, "_CURRENT_INSTANCE", None)
    now = time.time()
    by_registry = _diagnostics(tmp_path, "srv-registry", age_days=9, now=now)
    by_result = _diagnostics(tmp_path, "srv-result", age_days=9, now=now)
    unreferenced_old = _diagnostics(tmp_path, "srv-old", age_days=8, now=now)
    unreferenced_new = _diagnostics(tmp_path, "srv-new", age_days=6, now=now)
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", "srv-registry")
    session_registry.publish("held", terminal=True, status="interrupted", turn_seq=3, state_root=tmp_path)
    record = session_registry.registry_directory(tmp_path) / "held.json"
    _age(record, days=5, now=now)
    results = shared_layout.results_directory("root-a", tmp_path)
    results.mkdir(parents=True)
    result = results / "held-result.json"
    result.write_text(json.dumps({"status": "interrupted", "error": {"diagnostics": {"server_instance": "srv-result"}}}))

    shared_sweep.sweep_stale_shared_state(keep_root_session_id="root-a", state_root=tmp_path, now=now)

    assert by_registry.exists() and by_result.exists() and unreferenced_new.exists()
    assert not unreferenced_old.exists()

    # 参照元の登録簿と結果が期限で回収された後は、参照を失った診断も回収する。
    _age(record, days=8, now=now)
    result.unlink()
    shared_sweep.sweep_stale_shared_state(keep_root_session_id="root-a", state_root=tmp_path, now=now)
    assert not record.exists()
    assert not by_registry.exists() and not by_result.exists()
    assert unreferenced_new.exists()


def test_instance_diagnostics_capacity_keeps_referenced(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """合計が上限を超えると参照元の無い診断を古い順に削除し、参照元のある診断と実行中の起動単位は残す。"""
    now = time.time()
    referenced = _diagnostics(tmp_path, "srv-referenced", age_days=5, now=now, size=4000)
    oldest = _diagnostics(tmp_path, "srv-oldest", age_days=4, now=now, size=4000)
    middle = _diagnostics(tmp_path, "srv-middle", age_days=3, now=now, size=4000)
    newest = _diagnostics(tmp_path, "srv-newest", age_days=1, now=now, size=4000)
    current = _diagnostics(tmp_path, "srv-current", age_days=6, now=now, size=4000)
    monkeypatch.setattr(logging_config, "_CURRENT_INSTANCE", logging_config.ServerInstance("srv-current", current))
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", "srv-referenced")
    session_registry.publish("held", terminal=True, state_root=tmp_path)
    size = referenced.stat().st_size

    shared_sweep.sweep_stale_shared_state(
        keep_root_session_id=None, state_root=tmp_path, now=now, instance_max_bytes=size * 3 + size // 2
    )

    assert referenced.exists() and current.exists() and newest.exists()
    assert not oldest.exists() and not middle.exists()
