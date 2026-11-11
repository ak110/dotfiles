"""登録簿と状態ファイルを使う共有補助。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import contextlib
import pathlib
import subprocess
import sys
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import mcp_tools, shared_roots, state, status_file
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support_common import FakeBackend, _install_backend, install_backend


@contextlib.asynccontextmanager
async def _resume_test_manager(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, status_file.StatusFileWriter]]:
    """状態ファイルと登録簿を`tmp_path`へ置き、再開後の状態を観測できるmanagerを返す。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "claude")
        backend.publish_registry = True
        _install_backend(manager, "claude", backend)
        writer.activate()
        yield manager, writer


def _expire_launched_session(manager: server_manager.AgentsServerManager, session: state.SessionState) -> None:
    """保持期限を過ぎた終了sessionとして、同じmanagerの`send_message`で再開させる。"""
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session


def _restart_with_registry_record(manager: server_manager.AgentsServerManager, session: state.SessionState) -> None:
    """登録簿のレコードだけを残し、再起動したmanagerが登録簿から復元して再開させる。"""
    del manager, session  # 再起動したmanagerは元のsessionを保持せず、登録簿は終了の公開で書かれている


def _publish_recovered_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    session_id: str,
    status: str,
) -> None:
    """再起動後の解決に用いるversion 2登録簿を保存する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(
        session_id,
        state=session_registry.PublicationState.from_status(cast(Any, status), result_available=True),
        engine="codex",
        cwd=str(tmp_path),
        model="model",
        effort="high",
        model_type="execute",
        turn_seq=2,
    )


@contextlib.asynccontextmanager
async def _writer_backed_manager(
    tmp_path: pathlib.Path,
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, status_file.StatusFileWriter]]:
    """共有結果ファイルを公開するMCPマネージャーを既存のfake backendで組む。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        install_backend(manager, "codex", FakeBackend(manager.sessions, "codex"))
        writer.activate()
        yield manager, writer


async def _show_through_tool(
    monkeypatch: pytest.MonkeyPatch, manager: server_manager.AgentsServerManager, session_id: str
) -> dict[str, Any]:
    """MCPの`show`ツールの関数を、指定したマネージャーで呼ぶ。"""
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    return await mcp_tools.show_session(session_id)


def _publish_orphaned_codex(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, session_id: str, turn_id: str | None
) -> None:
    """所有者が終了を公開せずに終了したCodexの`running`記録を保存する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(
        session_id,
        state=session_registry.PublicationState.PENDING_RUNNING,
        engine="codex",
        cwd=str(tmp_path),
        turn_seq=3,
        turn_id=turn_id,
    )


def exited_process_identity() -> session_registry.ProcessIdentity:
    """識別を取得した後に終了させた実プロセスの識別を返す。所有側と委譲先CLIがともに終了した記録を作成するテストで使う。"""
    with subprocess.Popen([sys.executable, "-c", "pass"]) as process:
        identity = session_registry.ProcessIdentity.of(process.pid)
        process.wait()
    assert identity is not None
    return identity
