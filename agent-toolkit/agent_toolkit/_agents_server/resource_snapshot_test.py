"""公開session操作と共有状態の一覧から資源記録の契約を検証する。"""

import argparse
import datetime
import json
import logging
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from agent_toolkit._agents_server import (
    commands,
    logging_config,
    resource_snapshot,
    session_registry,
    shared_roots,
    status_file,
)
from agent_toolkit._agents_server.manager import AgentsServerManager
from agent_toolkit._atk import config
from agent_toolkit._common import state_paths
from agent_toolkit._testing.agents_server_support import FakeBackend, _complete, install_backend

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def _manager(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str = "codex") -> AgentsServerManager:
    """外部モデルを起動せず共通managerの公開操作を検証する。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-a", "root.json", None),
        state_root=tmp_path,
    )
    manager = AgentsServerManager(writer)
    install_backend(manager, engine, FakeBackend(manager.sessions, engine))
    monkeypatch.setattr(config, "parse_unresolved_model_candidates", lambda _name: [(engine, "model", "high")])
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    return manager


def _snapshots(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        json.loads(record.getMessage().removeprefix("resource_snapshot "))
        for record in caplog.records
        if record.getMessage().startswith("resource_snapshot ")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude", "codex", "agy"])
async def test_resource_snapshot_follows_public_session_lifecycle(
    engine: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共通managerは全engineの新規・reply・登録簿からの再開と終端だけを既存の回転ログへ記録する。"""
    manager = _manager(tmp_path, monkeypatch, engine)
    logger = logging.getLogger("agent-toolkit.agents-server")
    original_handlers, original_level, original_propagate = logger.handlers[:], logger.level, logger.propagate
    logger.handlers = []
    path = logging_config.configure_logging()
    try:
        started = await manager.start("plan", "秘密の本文を記録しない", str(tmp_path))
        session = manager.sessions[started["session_id"]]
        await manager.send_message(session.session_id, "稼働中の追送")
        _complete(session)
        await manager.send_message(session.session_id, "次の作業")
        _complete(session)
        await manager.kill(session.session_id, stop=True)
        await manager.send_message(session.session_id, "保持状態から再開")
        resumed = manager.sessions[session.session_id]
        _complete(resumed)
        await manager.wait()
        session_registry.publish(
            "restored", terminal=True, engine=engine, cwd=str(tmp_path), model="model", effort="high", turn_seq=7
        )
        await manager.send_message("restored", "登録簿から再開")
        _complete(manager.sessions["restored"])
        await manager.wait()
        snapshots = [
            json.loads(line.split("resource_snapshot ", 1)[1])
            for line in path.read_text(encoding="utf-8").splitlines()
            if " resource_snapshot " in line
        ]
        assert [(row["event"], row["turn_seq"], row["running_total"]) for row in snapshots] == [
            ("start", 1, 1),
            ("terminal", 1, 0),
            ("start", 2, 1),
            ("terminal", 2, 0),
            ("start", 3, 1),
            ("terminal", 3, 0),
            ("start", 8, 1),
            ("terminal", 8, 0),
        ]
        assert all(row["root_session_id"] == "root-a" for row in snapshots)
        assert [row["session_id"] for row in snapshots] == [session.session_id] * 6 + ["restored"] * 2
        assert all(datetime.datetime.fromisoformat(row["time"]).tzinfo is not None for row in snapshots)
        assert all(row["counts_complete"] and row["available_memory_bytes"] > 0 for row in snapshots)
        assert "秘密の本文" not in path.read_text(encoding="utf-8")
    finally:
        await manager.close()
        for handler in logger.handlers:
            handler.close()
        logger.handlers, logger.level, logger.propagate = original_handlers, original_level, original_propagate


@pytest.mark.asyncio
async def test_resource_snapshot_counts_shared_live_sessions(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """複数root・孫・重複・失効の一覧と同じ対象を数え、未flushの自sessionを開始と終端へ反映する。"""
    manager = _manager(tmp_path, monkeypatch)
    caplog.set_level(logging.INFO, logger="agent-toolkit.agents-server.resources")
    now = datetime.datetime.now(datetime.UTC)
    for root, name, entries, age in [
        ("root-a", "root.json", [("other-parent", "running", "1"), ("codex-session", "completed", "1")], 0),
        ("root-a", "other-parent.json", [("grandchild", "running", "2"), ("duplicate", "running", "1")], 0),
        ("root-a", "duplicate.json", [("duplicate", "completed", "2")], 0),
        ("root-b", "root.json", [("other-root-child", "running", "1")], 0),
        ("root-b", "stale.json", [("stale", "running", "9")], 121),
    ]:
        directory = tmp_path / "agents-server" / root
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(
            json.dumps(
                {
                    "version": 1,
                    "heartbeat_at": (now - datetime.timedelta(seconds=age)).isoformat(),
                    "sessions": [
                        {"session_id": identifier, "status": status, "updated_at": stamp}
                        for identifier, status, stamp in entries
                    ],
                }
            ),
            encoding="utf-8",
        )
    try:
        parser = argparse.ArgumentParser()
        commands.build_parser(parser)
        # 読取の共有化を一覧の公開操作でも通す。人向け一覧は全rootが対象となる。
        assert commands.dispatch(parser.parse_args(["list"]), environment={}) == 0
        started = await manager.start("plan", "調査", str(tmp_path))
        session = manager.sessions[started["session_id"]]
        _complete(session)
        rows = _snapshots(caplog)
        assert [(row["event"], row["running_by_root"]) for row in rows] == [
            ("start", {"root-a": 3, "root-b": 1}),
            ("terminal", {"root-a": 2, "root-b": 1}),
        ]
        assert [row["running_total"] for row in rows] == [4, 3]
        assert all(row["counts_complete"] for row in rows)
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_name", ["posix", "nt"])
@pytest.mark.parametrize("broken_payload", ["{", '{"heartbeat_at": 123, "sessions": []}'])
async def test_resource_snapshot_failure_does_not_fail_session(
    platform_name: str,
    broken_payload: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """状態欠損とOS値の取得失敗はゼロ件にせず、Windowsのloadは取得せず、sessionの完了は妨げない。"""
    manager = _manager(tmp_path, monkeypatch)
    caplog.set_level(logging.INFO, logger="agent-toolkit.agents-server.resources")
    directory = tmp_path / "agents-server" / "broken-root"
    directory.mkdir(parents=True)
    (directory / "root.json").write_text(broken_payload, encoding="utf-8")
    load = Mock(side_effect=OSError("load unavailable"))
    monkeypatch.setattr(resource_snapshot, "os", SimpleNamespace(name=platform_name, getloadavg=load))
    monkeypatch.setattr(resource_snapshot.psutil, "virtual_memory", Mock(side_effect=OSError("memory unavailable")))
    try:
        started = await manager.start("plan", "調査", str(tmp_path))
        _complete(manager.sessions[started["session_id"]])
        assert (await manager.wait())["status"] == "completed"
        rows = _snapshots(caplog)
        assert len(rows) == 2
        assert all(row["running_total"] is None and row["counts_complete"] is False for row in rows)
        assert [row["observed_running_total"] for row in rows] == [1, 0]
        assert all(row["available_memory_bytes"] is None and row["load_average_1_5_15"] is None for row in rows)
        assert all(row["state_read_failures"] and len(row["unavailable_fields"]) == 2 for row in rows)
        assert load.call_count == (0 if platform_name == "nt" else 2)
    finally:
        await manager.close()
