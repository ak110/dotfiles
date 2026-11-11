"""Codexのnotificationsを検証する。"""

from __future__ import annotations

import json
import pathlib
import shlex
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._agents_server import responses, state, turn_state
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support import FakeCodexClient, ServerRequestClient, install_backend
from agent_toolkit._testing.codex_backend_support import _completed_turn, _InspectableAppServerManager, _item_started

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


@pytest.mark.asyncio
@pytest.mark.parametrize("output_type", ["reasoning", "agentMessage"])
async def test_model_output_is_observed_only_after_non_input_item(tmp_path: pathlib.Path, output_type: str) -> None:
    """入力の記録である`userMessage`ではモデル出力を観測済みにせず、モデル由来のitemで観測済みにする。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    async with _InspectableAppServerManager({session.session_id: session}) as manager:
        await manager.handle_notification(_item_started(session, "userMessage"))
        after_input = session.model_output_observed
        await manager.handle_notification(_item_started(session, output_type))

        assert not after_input
        assert session.model_output_observed


@pytest.mark.asyncio
async def test_completed_turn_with_live_child_holds_result_for_auto_resume(tmp_path: pathlib.Path) -> None:
    """未観測の子sessionが残るturnは、待機表明を完了報告として公開せず、自動再開まで結果を保留する。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    async with _InspectableAppServerManager({session.session_id: session}) as manager:
        await manager.handle_notification(_completed_turn(session))

        assert session.result_available is False
        assert session.awaiting_auto_resume is True
        assert session.status == "running"
        assert session.pending_result == {"status": "completed", "agent_message": "", "error": None}
        assert session.error is None
        assert session.live_child_session_ids == {"child-1"}
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("turn_status", ["completed", "failed", "interrupted"])
async def test_send_message_child_reactivation_holds_parent_until_child_terminates(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, turn_status: str
) -> None:
    """再開した子が残る親の完了・失敗・中断をshowで保留し、子の終了後の再開結果をwaitへ公開する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with (
        server_manager.AgentsServerManager(None) as manager,
        subject.AppServerManager(manager.sessions, manager._condition) as backend,
    ):
        install_backend(manager, "codex", backend)
        client = FakeCodexClient()
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        session = await backend.start("親の作業", str(tmp_path))
        session.terminal_child_session_ids.add("child")
        session_registry.publish("child", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(tmp_path))
        item = {
            "type": "mcpToolCall",
            "id": "reply-child",
            "server": "agents_server",
            "tool": "send_message",
            "arguments": {"session_id": "child", "prompt": "残作業を続ける"},
            "result": {
                "structuredContent": responses.public_send_message_response({"delivery": "reply_started", "label": "子の担当"})
            },
        }
        try:
            await backend._handle_notification(
                {"method": "item/completed", "params": {"threadId": session.session_id, "item": item}}
            )
            completion = _completed_turn(session)
            completion["params"]["turn"].update(
                {
                    "status": turn_status,
                    "error": {"codexErrorInfo": "usageLimitExceeded"} if turn_status == "failed" else None,
                    # turn/completedが同じtool結果を再掲するApp Serverの形式も通す。
                    "items": [item],
                }
            )
            await backend._handle_notification(completion)
            shown = await manager.show_session(session.session_id)
            assert shown["status"] == "running" and shown["result_held"]
            assert shown["live_child_sessions"] == [{"session_id": "child", "cwd": str(tmp_path)}]
            assert not session.result_available
            session_registry.publish("child", state=session_registry.PublicationState.RESULT_COMPLETED, cwd=str(tmp_path))
            pending = await manager.wait()
            assert pending["status"] in {"starting", "running"}
            await backend._handle_notification(_completed_turn(session))
            result = await manager.wait()
            assert result["status"] == "completed"
            assert not session.awaiting_auto_resume
        finally:
            await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delivery",
    (
        "stdout",
        "output-file",
        "shell-stdout",
        "shell-output-file",
        "absolute-stdout",
        "absolute-shell",
        "shell-unrelated",
        "failure",
        "unrelated",
        "quoted-data",
        "chained",
    ),
)
async def test_cli_wait_updates_observed_child_sessions(tmp_path: pathlib.Path, delivery: str) -> None:
    """CLI待機が回収した子だけを観測済みにし、失敗時は未観測として残す。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    async with _InspectableAppServerManager({session.session_id: session}) as manager:
        result = json.dumps({"session_id": "child-1", "status": "completed"}) + "\n"
        if delivery in {"output-file", "shell-output-file"}:
            output_path = tmp_path / "wait-results.jsonl"
            output_path.write_text(result, encoding="utf-8")
            output = f"保存先: {output_path}\n"
        else:
            output = result
        command = "atk agents wait"
        if delivery in {"output-file", "shell-output-file"}:
            command = "timeout 300 atk agents wait"
        if delivery == "unrelated":
            command = "printf 'unrelated command'"
        if delivery == "shell-stdout":
            command = '/bin/bash -lc "atk agents wait"'
        if delivery == "shell-output-file":
            command = '/bin/sh -c "timeout 300 atk agents wait"'
        if delivery == "shell-unrelated":
            command = '/bin/bash -lc "printf unrelated"'
        # 引用した待機コマンドは出力するデータであり、実際の連結の後ろにある待機だけを回収する。
        if delivery == "quoted-data":
            command = "printf '%s' 'atk agents wait'"
        if delivery == "chained":
            command = "true && atk agents wait"
        if delivery in {"absolute-stdout", "absolute-shell"}:
            command = shlex.quote(str(plugin_roots.SERVER_PLUGIN_ROOT / "scripts" / "atk")) + " agents wait"
            if delivery == "absolute-shell":
                command = "/bin/sh -c " + shlex.quote(command)
        item = {
            "type": "commandExecution",
            "id": "wait-command",
            "command": command,
            "aggregatedOutput": output,
            "exitCode": 1 if delivery == "failure" else 0,
        }

        await manager.handle_notification(
            {"method": "item/completed", "params": {"threadId": session.session_id, "turnId": session.turn_id, "item": item}}
        )
        await manager.handle_notification(_completed_turn(session))

        # 回収できなかった子は追跡に残り、自動再開まで結果を保留する。回収済みなら保留せず直ちに公開する。
        unobserved = delivery in {"failure", "unrelated", "shell-unrelated", "quoted-data"}
        assert session.awaiting_auto_resume is unobserved
        assert session.result_available is not unobserved
        assert session.live_child_session_ids == ({"child-1"} if unobserved else set())
        assert session.error is None
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_server_request_marks_target_failed_and_replies(tmp_path: pathlib.Path) -> None:
    """非対話server requestへerror応答し、対象sessionをfailedへ遷移する。"""
    async with subject.AppServerManager() as manager:
        client = ServerRequestClient()
        session = state.SessionState("thread-1", str(tmp_path), engine="codex")
        manager.sessions[session.session_id] = session
        manager._clients[session.session_id] = cast(Any, client)

        await manager._handle_server_request(
            {"id": 7, "method": "item/tool/requestUserInput", "params": {"threadId": session.session_id}}, client
        )

        assert session.status == "failed"
        assert session.result_available is True
        assert client.sent[-1]["id"] == 7
        assert client.sent[-1]["error"]["code"] == -32601


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_reader_failure_marks_only_owned_sessions_failed(tmp_path: pathlib.Path) -> None:
    """reader異常は失敗した接続を所有するactive sessionだけをfailedへ遷移し、別の接続のsessionを変えない。"""
    async with subject.AppServerManager() as manager:
        failed_client, other_client = FakeCodexClient(), FakeCodexClient()
        for session_id, client in (("thread-1", failed_client), ("thread-2", failed_client), ("thread-3", other_client)):
            manager.sessions[session_id] = state.SessionState(session_id, str(tmp_path), engine="codex")
            manager._clients[session_id] = cast(Any, client)

        await manager._handle_client_failure(RuntimeError("invalid JSON line"), failed_client)

        owned = [manager.sessions["thread-1"], manager.sessions["thread-2"]]
        assert {session.status for session in owned} == {"failed"}
        assert all(session.result_available for session in owned)
        assert all(session.retention_deadline is not None for session in owned)
        other = manager.sessions["thread-3"]
        assert other.status != "failed" and not other.result_available


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reader", "unknown-request"])
async def test_codex_connection_failure_preserves_other_engines(tmp_path: pathlib.Path, failure: str) -> None:
    """共有一覧に他engineと保留中Claudeがあっても、Codex接続の失敗はCodexだけを変更する。"""
    async with subject.AppServerManager() as manager:
        active = state.SessionState("codex-active", str(tmp_path), engine="codex")
        completed = state.SessionState(
            "codex-done", str(tmp_path), engine="codex", status="completed", turn_end=turn_state.TurnEnd.COMPLETED
        )
        others = [
            state.SessionState("claude-active", str(tmp_path), engine="claude", turn_id="claude-turn"),
            state.SessionState(
                "claude-held",
                str(tmp_path),
                engine="claude",
                hold=turn_state.BackgroundWait({"status": "completed", "agent_message": "待機中"}, None),
            ),
            state.SessionState("agy-active", str(tmp_path), engine="agy", turn_id="agy-turn"),
        ]
        client = FakeCodexClient()
        for session in (active, completed, *others):
            manager.sessions[session.session_id] = session
        for session in (active, completed):
            manager._clients[session.session_id] = cast(Any, client)
        before = [
            (item.status, item.error, item.awaiting_auto_resume, item.result_available, item.pending_result, item.updated_at)
            for item in others
        ]
        if failure == "reader":
            await manager._handle_client_failure(RuntimeError("invalid JSON line"), client)
        else:
            await manager._fail_for_request({}, "item/tool/requestUserInput", client)
        assert active.status == "failed" and active.result_available
        assert completed.status == "completed"
        assert [
            (item.status, item.error, item.awaiting_auto_resume, item.result_available, item.pending_result, item.updated_at)
            for item in others
        ] == before
        for item in others:
            assert manager._find_session({"threadId": item.session_id}) is None
            if item.turn_id:
                assert manager._find_session({"turnId": item.turn_id}) is None
        assert manager._find_session({"threadId": active.session_id}) is active


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_progress_notification_is_shared(tmp_path: pathlib.Path) -> None:
    """Codexのdelta通知を共有SessionStateのprogressへ反映する。"""
    async with subject.AppServerManager() as manager:
        session = state.SessionState("thread-codex", str(tmp_path), engine="codex")
        session.turn_id = "turn-1"
        manager.sessions[session.session_id] = session
        await manager._handle_notification(
            {
                "method": "item/agentMessage/delta",
                "params": {"threadId": "thread-codex", "turnId": "turn-1", "itemId": "item-1", "delta": "進捗\n"},
            }
        )
        assert session.progress == "進捗 "

        await manager._handle_notification(
            {
                "method": "item/agentMessage/delta",
                "params": {"threadId": "thread-codex", "turnId": "turn-1", "itemId": "item-1", "delta": "途中"},
            }
        )
        assert session.progress == "進捗 途中"

        await manager._handle_notification(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-codex",
                    "turnId": "turn-1",
                    "item": {"id": "item-1", "type": "agentMessage", "text": "完成した全文"},
                },
            }
        )
        assert session.progress == "完成した全文"

        await manager._handle_notification(
            {
                "method": "item/plan/delta",
                "params": {"threadId": "thread-codex", "turnId": "turn-1", "itemId": "plan-1", "delta": "計画"},
            }
        )
        assert session.progress == "完成した全文"


@pytest.mark.asyncio
async def test_context_usage_notification_is_session_scoped(tmp_path: pathlib.Path) -> None:
    """0.162.1の通知が持つ`last`だけを同じthread・turnへ配送し、累計と活動を混ぜない。"""
    # codex-cli 0.162.1 generate-json-schemaのThreadTokenUsageUpdatedNotification。
    session = state.SessionState("usage-thread", str(tmp_path), engine="codex", turn_id="usage-turn")
    other = state.SessionState("other-thread", str(tmp_path), engine="codex", turn_id="other-turn")
    async with _InspectableAppServerManager({session.session_id: session, other.session_id: other}) as manager:
        initial_activity = session.updated_at

        async def notify(thread: str, turn: str, tokens: Any, window: Any = 1000000) -> None:
            await manager.handle_notification(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "threadId": thread,
                        "turnId": turn,
                        "tokenUsage": {
                            "last": {"totalTokens": tokens},
                            "total": {"totalTokens": 999999},
                            "modelContextWindow": window,
                        },
                    },
                }
            )

        await notify(session.session_id, session.turn_id, 53500)
        assert session.context_tokens == 53500 and session.context_window == 1000000
        await notify("missing-thread", session.turn_id, 1)
        await notify(other.session_id, session.turn_id, 2)
        await notify(session.session_id, "old-turn", 3)
        assert session.context_tokens == 53500 and other.context_tokens is None
        await notify(session.session_id, session.turn_id, 1000)
        assert session.context_tokens == 1000
        await notify(session.session_id, session.turn_id, 0, None)
        assert session.context_tokens == 0 and session.context_window is None
        assert session.updated_at == initial_activity
        await notify(session.session_id, session.turn_id, True)
        assert session.context_tokens is None
        await manager.close()
