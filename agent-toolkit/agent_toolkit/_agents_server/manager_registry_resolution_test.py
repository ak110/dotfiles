"""`_agents_server/manager_registry.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import dataclasses
import pathlib

import pytest

from agent_toolkit._agents_server import state
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_support import (
    _actionable_message,
    _complete,
    _manager_with_fake,
    _publish_recovered_session,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_show_reports_another_writer_for_running_registry_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """登録簿が実行中として保持する識別子は、別主体の実行中として案内する。"""
    async with _manager_with_fake("codex") as (manager, _):
        session_id = "0ba2f3f4-3e6c-4a1a-9a35-9f5e30b9f9b1"
        monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
        session_registry.publish(
            session_id, state=session_registry.PublicationState.PENDING_RUNNING, engine="codex", cwd=str(tmp_path)
        )

        with pytest.raises(ValueError) as excinfo:
            (await manager.show_session(session_id))

        message = _actionable_message(excinfo.value)
        assert "may have restarted" not in message
        assert "another writer" in message
        assert "atk agents wait" in message
        await manager.close()


@pytest.mark.asyncio
async def test_show_keeps_lost_session_diagnosis_without_registry_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """登録簿にレコードが無い識別子では従来の喪失の案内を返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        session_id = "5c9c2ec4-08f0-4a1e-9a02-9e8f0a4a4f21"
        monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

        with pytest.raises(ValueError, match=f"unknown session: {session_id}"):
            (await manager.show_session(session_id))

        await manager.close()


@pytest.mark.asyncio
async def test_show_returns_terminal_session_restored_from_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """登録簿から復元できた終了sessionは通常の応答として返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        session_id = "9f2a37f4-40f8-4d38-9c1b-1f6f9f52a1c3"
        _publish_recovered_session(monkeypatch, tmp_path, session_id, "completed")

        response = await manager.show_session(session_id)

        assert response["session_id"] == session_id
        assert response["status"] == "completed"
        await manager.close()


@pytest.mark.asyncio
async def test_other_manager_shows_saved_activity_and_lists_unknown_legacy_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """別Managerが復元したsessionの一覧・詳細は保存時刻を使い、旧記録を照会時刻で補わない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with _manager_with_fake("codex") as (owner, _):
        session = state.SessionState("saved-activity", str(tmp_path), engine="codex", publish_registry=True)
        session.started_at = "2026-09-27T14:38:32+00:00"
        session.status = "completed"
        session.record_turn_completed()
        session.touch()
        owner.sessions[session.session_id] = session
        await owner.close()
        session_registry.publish("legacy-activity", state=session_registry.PublicationState.RESULT_COMPLETED, cwd=str(tmp_path))
        async with _manager_with_fake("codex") as (observer, _):
            saved = await observer.show_session(session.session_id, verbose=True)
            legacy = await observer.show_session("legacy-activity", verbose=True)
            listed = {item["session_id"]: item for item in (await observer.list_sessions(include_terminated=True))["sessions"]}

            assert saved["started_at"] == session.started_at
            assert saved["updated_at"] == session.updated_at
            assert legacy["started_at"] is None
            assert legacy["updated_at"] is None
            assert {"started_at", "updated_at", "output_updated_at"}.isdisjoint(listed[session.session_id])
            assert "updated_at" not in listed["legacy-activity"]
            await observer.close()
            await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["wait", "kill"])
async def test_stopped_result_remains_available_after_retention_deadline(
    operation: str,
    tmp_path: pathlib.Path,
) -> None:
    """stop=trueで保持した結果は期限到来後も最初の観測へ本文を返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        session = state.SessionState(f"stopped-{operation}", str(tmp_path), engine="codex")
        _complete(session, message="保持結果")
        manager.sessions[session.session_id] = session
        await manager.kill(session.session_id, timeout=0, stop=True)
        retained = manager.stopped_sessions[session.session_id]
        assert retained.retention_deadline == session.retention_deadline
        manager.stopped_sessions[session.session_id] = dataclasses.replace(
            retained,
            retention_deadline=asyncio.get_running_loop().time() - 1,
        )

        if operation == "wait":
            response = await manager.wait()
            assert response["status"] == "completed"
            assert response["agent_message"] == "保持結果"
        else:
            response = await manager.kill(session.session_id, timeout=0)
            assert response["status"] == "completed"
            assert response["kill_requested"] is False
            assert response["agent_message"] == "保持結果"
        assert manager.stopped_sessions[session.session_id].result_delivered is True


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send_message", "kill"])
async def test_unknown_session_is_distinct_from_expired_session(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """未登録のUUIDを期限切れ識別子と区別し、記録が無いことと復旧手順を返す。

    登録簿のレコードは再起動では削除されないため、再起動を原因として案内しない。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with _manager_with_fake("codex") as (manager, _):
        session_id = "3468feae-b2bf-4d67-ac55-3c40207e8b5b"
        with pytest.raises(ValueError) as exc_info:
            if operation == "send_message":
                await manager.send_message(session_id, "続行")
            else:
                await manager.kill(session_id, timeout=0)
        message = _actionable_message(exc_info.value)
        assert message.startswith(f"unknown session: {session_id}")
        assert "restarted" not in message
        assert "no agents_server on this host has a record" in message
        next_action = message.split(f"\n{NEXT_ACTION_PREFIX}", 1)[1]
        assert "`list`" in next_action
        assert "atk agents wait" in next_action


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["show", "send_message", "kill", "stop"])
@pytest.mark.parametrize(("release", "reason_text"), [("expire", "retention expired"), ("stop", "stopped")])
async def test_session_released_by_another_server_is_reported_as_released(
    operation: str,
    release: str,
    reason_text: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """別のagents_serverが解放したsessionの照会は、解放済みであることと理由・時刻を返す。

    どちらの応答も継続不能の判定に使う`unknown session: <id>`で始め、再起動を案内しない。
    記録が無い識別子とは文面が異なる。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_id = "7f0c2b8e-5d1a-4e3b-9c6f-2a8d4e1b7c90"
    async with _manager_with_fake("codex") as (owner, _):
        session = state.SessionState(session_id, str(tmp_path), engine="codex", publish_registry=True)
        _complete(session)
        owner.sessions[session_id] = session
        if release == "expire":
            owner._expire_session(session_id)
            await owner.close()
        else:
            await owner.stop(session_id)
        async with _manager_with_fake("codex") as (observer, _):
            with pytest.raises(ValueError) as exc_info:
                if operation == "show":
                    (await observer.show_session(session_id))
                elif operation == "send_message":
                    await observer.send_message(session_id, "続行")
                elif operation == "kill":
                    await observer.kill(session_id, timeout=0)
                else:
                    await observer.stop(session_id)
            message = _actionable_message(exc_info.value)
            assert "atk agents wait" in message.split(NEXT_ACTION_PREFIX, 1)[1]
            released_at = session_registry.resolve(session_id).released_at
            assert released_at is not None
            assert message.startswith(f"unknown session: {session_id}")
            assert f"released by the owning agents_server ({reason_text}, {released_at})" in message
            assert "restarted" not in message
            assert "no agents_server on this host has a record" not in message
            await owner.close()
            await observer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send_message", "kill", "stop"])
async def test_non_uuid_session_id_reports_identifier_scheme_mismatch(operation: str) -> None:
    """体系外の識別子をsession喪失と区別して公開操作から返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        with pytest.raises(ValueError) as exc_info:
            if operation == "send_message":
                await manager.send_message("agent-session", "続行")
            elif operation == "kill":
                await manager.kill("agent-session", timeout=0)
            else:
                await manager.stop("agent-session")
        message = _actionable_message(exc_info.value)
        assert message.startswith("session identifier scheme mismatch: agent-session")
        assert "unknown session" not in message
        assert "`list`" in message.split(NEXT_ACTION_PREFIX, 1)[1]


@pytest.mark.asyncio
async def test_identifier_resolution_precedes_scheme_classification(tmp_path: pathlib.Path) -> None:
    """登録済み状態の解決後にだけ未解決識別子の体系を判定する。"""
    async with _manager_with_fake("codex") as (manager, _):
        registered = state.SessionState("agent-session", str(tmp_path), engine="codex")
        _complete(registered, message="登録済み")
        manager.sessions[registered.session_id] = registered

        assert (await manager.kill(registered.session_id, timeout=0))["agent_message"] == "登録済み"

        manager.sessions.pop(registered.session_id)
        with pytest.raises(ValueError) as mismatch_exc:
            await manager.kill(registered.session_id, timeout=0)
        assert "identifier scheme mismatch" in str(mismatch_exc.value)
        assert "unknown session" not in str(mismatch_exc.value)
