"""Codexのwriter_conflictを検証する。"""

from __future__ import annotations

import json
import pathlib
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import mcp_tools, responses, resume_waits, state
from agent_toolkit._common import session_registry
from agent_toolkit._testing.agents_server_support import FakeCodexClient, _complete
from agent_toolkit._testing.codex_backend_support import _assert_writer_conflict_action, _ResumeRejectionClient

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


@pytest.mark.parametrize("path", ["warm", "cold", "restored"])
@pytest.mark.parametrize("provider", ["openai", "api-other"])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_send_message_reports_thread_writer_conflict(
    path: str, provider: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開追送はwriter競合を未配送として返し、turn開始・retry・provider切替を行わない。"""
    async with server_manager.AgentsServerManager(None) as manager:
        backend = manager._backend("codex")
        client = _ResumeRejectionClient()
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        session_id = "writer-conflict"
        if path == "warm":
            session = state.SessionState(session_id, str(tmp_path), engine="codex")
            _complete(session)
            session.codex_model_provider = provider
            session.codex_subscription_provider = "openai"
            session.codex_state_provider = provider
            manager.sessions[session_id] = session
            cast(Any, backend)._clients[session_id] = client
        else:
            saved = state.SessionResumeState(
                session_id,
                str(tmp_path),
                None,
                None,
                "codex",
                codex_model_provider=provider,
                codex_subscription_provider="openai",
                codex_state_provider=provider,
            )
            if path == "cold":
                manager.expired_sessions[session_id] = saved
            else:
                session_registry.publish(
                    session_id,
                    state=session_registry.PublicationState.RESULT_COMPLETED,
                    cwd=str(tmp_path),
                    engine="codex",
                    codex_model_provider=provider,
                    codex_subscription_provider="openai",
                    codex_state_provider=provider,
                )
        response = await mcp_tools.send_message(session_id, "残る作業を続ける")
        assert response["delivery"] == "reply_failed"
        _assert_writer_conflict_action(response["next_action"])
        session = manager.sessions[session_id]
        assert session.error["codexErrorInfo"] == "threadWriterConflict"
        assert "already has an active writer" in session.error["message"]
        assert session.result_available and not session.awaiting_auto_resume
        assert not session.codex_provider_resume_pending
        assert [method for method, _params in client.requests] == ["thread/resume"]
        assert session.codex_model_provider == provider
        result = await manager.wait()
        _assert_writer_conflict_action(result["next_action"])
        assert result["error"]["codexErrorInfo"] == "threadWriterConflict"


@pytest.mark.parametrize("provider", ["openai", "api-other"])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_auto_resume_publishes_thread_writer_conflict(
    provider: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """子の終了後の自動再開の競合は保留を解き、専用案内を持つ終了結果へ進める。"""
    async with server_manager.AgentsServerManager(None) as manager:
        backend = manager._backend("codex")
        client = _ResumeRejectionClient()
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        session = state.SessionState("auto-writer", str(tmp_path), engine="codex", turn_seq=1)
        _complete(session)
        session.codex_model_provider = provider
        session.codex_subscription_provider = "openai"
        session.codex_state_provider = provider
        session.terminal_child_session_ids.add("finished-child")
        resume_waits.begin_auto_resume_wait(session, {"status": "completed", "agent_message": "待機中", "error": None})
        session.status = "running"
        manager.sessions[session.session_id] = session
        result = await manager.wait()
        assert result["status"] == "failed"
        assert result["error"]["codexErrorInfo"] == "threadWriterConflict"
        _assert_writer_conflict_action(result["next_action"])
        assert not session.awaiting_auto_resume and session.pending_result is None
        assert [method for method, _params in client.requests] == ["thread/resume"]


@pytest.mark.parametrize(
    ("method", "message"),
    [
        ("thread/read", "thread saved-thread already has an active writer"),
        ("thread/resume", "thread different-thread already has an active writer"),
        ("thread/resume", "thread saved-thread resume rejected"),
    ],
)
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_resume_keeps_unrecognized_writer_errors(
    method: str, message: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """method・対象thread・既知断片がそろわない失敗は元の診断を保つ。"""
    async with subject.AppServerManager() as backend:
        client = _ResumeRejectionClient(method, message)
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        prompt = state.ResumePrompt("再開")
        try:
            session = await backend.resume("saved-thread", prompt, str(tmp_path), state_provider="openai")
            assert prompt.delivery == "reply_failed"
        finally:
            prompt.close()
        assert session.error == {"message": f"{method}: {message}"}
        assert session.status == "failed" and not session.turn_start_ambiguous
        assert [method for method, _params in client.requests] == ["config/read", "thread/resume"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_provider_switch_stops_at_thread_writer_conflict(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """利用枠の失敗から移行する途中のwriter競合も、次providerを試さず公開する。"""
    async with subject.AppServerManager() as backend:
        client = _ResumeRejectionClient()
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        monkeypatch.setattr(
            subject.codex_providers,
            "select",
            AsyncMock(return_value=subject.codex_providers.ProviderSelection("openai", True, ("api-one", "api-two"))),
        )
        session = state.SessionState("switch-writer", str(tmp_path), engine="codex")
        _complete(session, error={"codexErrorInfo": "usageLimitExceeded", "usageLimit": {"type": "weekly"}})
        session.codex_model_provider = "openai"
        session.codex_subscription_provider = "openai"
        session.codex_state_provider = "openai"
        backend.sessions[session.session_id] = session
        result = await backend.send_message(session, "接続失敗からの継続")
        assert result["delivery"] == "reply_failed"
        assert session.error["codexErrorInfo"] == "threadWriterConflict"
        assert not session.awaiting_auto_resume and not session.codex_provider_resume_pending
        resumes = [params for method, params in client.requests if method == "thread/resume"]
        assert len(resumes) == 1 and resumes[0]["modelProvider"] == "api-one"
        assert not any(method == "turn/start" for method, _params in client.requests)


@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_send_message_preserves_incompatible_resume_guidance(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成先の異なる不透明状態の拒否は、writer競合と区別して既存の案内を保つ。"""
    async with server_manager.AgentsServerManager(None) as manager:
        backend = manager._backend("codex")
        client = FakeCodexClient()
        record = tmp_path / "opaque.jsonl"
        # 生成先の検証が読む既存のrollout形式。暗号化値自体は架空の入力である。
        record.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "opaque-thread", "model_provider": "other"}})
            + "\n"
            + json.dumps({"type": "response_item", "payload": {"encrypted_content": "opaque"}})
            + "\n",
            encoding="utf-8",
        )
        client.rollout_path = record
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        manager.expired_sessions["opaque-thread"] = state.SessionResumeState(
            "opaque-thread",
            str(tmp_path),
            None,
            None,
            "codex",
            codex_state_provider="other",
        )
        response = await mcp_tools.send_message("opaque-thread", "続行")
        assert response["delivery"] == "reply_failed"
        assert response["next_action"] == responses.REPLY_NEXT_ACTIONS["reply_failed"]
        result = await manager.wait()
        assert result["error"]["codexErrorInfo"] == "threadStateIncompatible"
        assert "handoff_from" in result["next_action"]
        assert [method for method, _params in client.requests] == ["config/read", "thread/read"]
