"""Codexの開始と設定変更を検証する。"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import shared_roots, state
from agent_toolkit._atk import config_settings
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing import late_descendant
from agent_toolkit._testing.agents_server_support import _complete
from agent_toolkit._testing.codex_backend_support import _completed_turn, _ProcessTierClient, _TierClient
from agent_toolkit._testing.codex_plugin_support import _owned_async_child

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


@pytest.mark.parametrize("failure", ["body", "graceful-close"])
@pytest.mark.asyncio
async def test_owned_async_child_reclaims_process_on_failure(
    monkeypatch: pytest.MonkeyPatch, failure: str, tmp_path: pathlib.Path
) -> None:
    """利用失敗と正常終了待ちの失敗で、所有済みasyncio子とpipeを残さない。"""
    error_type = RuntimeError if failure == "body" else TimeoutError
    script = "import sys; sys.stdin.read()" if failure == "body" else late_descendant.held_source(tmp_path / "async.release")
    acquired: list[asyncio.subprocess.Process] = []
    with pytest.raises(error_type, match="検証中の失敗"):
        async with _owned_async_child(
            sys.executable,
            "-c",
            script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        ) as process:
            acquired.append(process)
            if failure == "body":
                raise RuntimeError("検証中の失敗")
            communicate = process.communicate
            attempts = 0

            async def fail_once(*args: Any, **kwargs: Any) -> tuple[bytes, bytes]:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise TimeoutError("検証中の失敗")
                return await communicate(*args, **kwargs)

            monkeypatch.setattr(process, "communicate", fail_once)
    assert len(acquired) == 1
    assert acquired[0].returncode is not None
    assert acquired[0].stdin is not None and acquired[0].stdin.is_closing()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.parametrize("existing", ["root", "running", "completed"])
@pytest.mark.asyncio
async def test_start_rejects_existing_thread_before_alias_and_client_registration(
    existing: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """重複threadは既存接続と状態を維持し、新規接続だけを回収して索引作成前に失敗する。"""
    async with subject.AppServerManager(root_session_id="tier-thread" if existing == "root" else "owner") as backend:
        old = state.SessionState("tier-thread", str(tmp_path), engine="codex")
        if existing == "completed":
            _complete(old)
        if existing != "root":
            backend.sessions[old.session_id] = old
        client = _TierClient()
        close = AsyncMock(return_value=[])
        monkeypatch.setattr(client, "close", close)
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        alias = Mock()
        monkeypatch.setattr(shared_roots, "write_host_alias", alias)
        with pytest.raises(ActionableError, match="duplicate session_id"):
            await backend.start("重複確認", str(tmp_path))
        if existing != "root":
            assert backend.sessions[old.session_id] is old
        assert not backend._clients
        close.assert_awaited_once()
        alias.assert_not_called()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.parametrize("outcome", ["completed", "execution-failed", "start-failed", "ambiguous"])
@pytest.mark.parametrize("notification_first", [True, False])
@pytest.mark.asyncio
async def test_resume_retains_request_delivery(
    outcome: str, notification_first: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """実backendの配送結果は、開始応答と終了通知の前後を通じて一定である。"""
    async with subject.AppServerManager() as backend:
        client = _TierClient()
        original = client.request
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))

        async def complete() -> None:
            session = backend.sessions["resume-delivery"]
            event = _completed_turn(session)
            event["params"]["turn"]["id"] = "resume-turn"
            if outcome == "execution-failed":
                event["params"]["turn"].update(status="failed", error={"message": "execution failed"})
            await backend._handle_notification(event)

        async def request(method: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            if method != "turn/start":
                return await original(method, params, **kwargs)
            if outcome == "start-failed":
                raise subject.JsonRpcResponseError(method, -32600, "request rejected")
            if outcome == "ambiguous":
                raise TimeoutError("response lost")
            response = await original(method, params, **kwargs)
            response["turn"]["id"] = "resume-turn"
            if notification_first:
                await complete()
            return response

        monkeypatch.setattr(client, "request", request)
        prompt = state.ResumePrompt("再開")
        session = await backend.resume("resume-delivery", prompt, str(tmp_path))
        expected = {"start-failed": "reply_failed", "ambiguous": "reply_ambiguous"}.get(outcome, "reply_started")
        assert prompt.delivery == expected
        if outcome in {"completed", "execution-failed"}:
            if not notification_first:
                await complete()
            assert session.status == ("failed" if outcome == "execution-failed" else "completed")
            assert prompt.delivery == "reply_started"
        elif outcome == "ambiguous":
            assert session.turn_start_ambiguous
        else:
            assert session.status == "failed"


@pytest.mark.asyncio
async def test_start_and_resume_record_owner_and_app_server_processes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新規起動と再開の登録簿の記録は、所有プロセスとturnを実行するApp Serverの識別を持つ。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(config_settings, "config_dir", lambda: tmp_path / "config")
    owner = session_registry.owner_process()
    assert owner is not None
    expected = [owner.payload()] * 2
    record = session_registry.registry_directory() / "tier-thread.json"
    client = _ProcessTierClient(owner.pid)
    async with subject.AppServerManager({}, asyncio.Condition(), publish_registry=True) as manager:
        monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))

        await manager.start("開始", str(tmp_path))
        assert json.loads(record.read_text(encoding="utf-8"))["processes"] == expected

        record.unlink()
        async with subject.AppServerManager({}, asyncio.Condition(), publish_registry=True) as restarted:
            monkeypatch.setattr(restarted, "_ensure_client", AsyncMock(return_value=client))
            await restarted.resume("tier-thread", state.ResumePrompt("再開"), str(tmp_path))
            assert json.loads(record.read_text(encoding="utf-8"))["processes"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [True, False])
async def test_config_changes_apply_to_new_reply_and_restored_turns(
    initial: bool, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開configの変更を3種類のRPCの要求へ明示し、実際に送った最新turnの速度を保持する。"""
    monkeypatch.setattr(config_settings, "config_dir", lambda: tmp_path / "config")
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE", raising=False)

    def set_fast(enabled: bool) -> None:
        capsys.readouterr()
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "set", "codex_fast_mode", str(enabled).lower()], home=tmp_path)
        assert not capsys.readouterr().err

    set_fast(initial)
    client = _TierClient()
    async with subject.AppServerManager({}, asyncio.Condition(), publish_registry=True) as manager:
        monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
        session = await manager.start("開始", str(tmp_path), "gpt-6-sol", "medium")
        tier = "priority" if initial else "default"
        assert [method for method, _params in client.requests] == ["thread/start", "turn/start"]
        assert all(params["serviceTier"] == tier for _method, params in client.requests)
        assert session.fast_mode is initial
        session.status = "completed"
        session.record_turn_completed()
        session.touch()
        set_fast(not initial)
        client.requests.clear()
        reply = await manager.send_message(session, "続行")
        assert reply["delivery"] == "reply_started"
        assert all(params["serviceTier"] == ("default" if initial else "priority") for _method, params in client.requests)
        assert session.fast_mode is (not initial)

        set_fast(initial)
        restored_client = _TierClient()
        async with subject.AppServerManager({}, asyncio.Condition()) as restarted:
            monkeypatch.setattr(restarted, "_ensure_client", AsyncMock(return_value=restored_client))
            restored = await restarted.resume(
                session.session_id,
                state.ResumePrompt("再開"),
                str(tmp_path),
                session.model,
                session.effort,
                turn_seq=session.turn_seq,
                fast_mode=session.fast_mode,
            )
            assert [method for method, _params in restored_client.requests] == ["thread/read", "thread/resume", "turn/start"]
            assert all(
                params["serviceTier"] == tier
                for method, params in restored_client.requests
                if method in {"thread/resume", "turn/start"}
            )
            assert restored.fast_mode is initial
