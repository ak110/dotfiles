"""組み込みの子と自分宛て送信の保留・再開を公開結果まで検証する。"""

# 通知入力と監視の境界を決定論的に駆動する。
# pylint: disable=protected-access
import pathlib
from typing import cast
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import codex, manager, resume_waits, state
from agent_toolkit._testing.agents_server_support import FakeCodexClient, install_backend

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def _activity(thread: str, kind: str, identifier: str) -> dict:
    return {"id": identifier, "type": "subAgentActivity", "kind": kind, "agentThreadId": thread, "agentPath": "/root/child"}


async def _item(backend, session, item):
    await backend._handle_notification(
        {
            "method": "item/completed",
            "params": {
                "threadId": session.session_id,
                "turnId": session.turn_id,
                "item": item,
            },
        }
    )


async def _complete(backend, session, items=()):
    await backend._handle_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": session.session_id,
                "turn": {"id": session.turn_id, "status": "completed", "error": None, "items": list(items)},
            },
        }
    )


@pytest.mark.parametrize("terminal", ["completed", "interrupted", "child-turn"])
@pytest.mark.asyncio
async def test_native_children_hold_and_resume_once(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, terminal: str):
    """子が全て終端した後の同一sessionの継続結果を公開し、再掲と次世代の子も扱う。"""
    async with manager.AgentsServerManager(None) as owner, codex.AppServerManager(owner.sessions, owner._condition) as backend:
        install_backend(owner, "codex", backend)
        client = FakeCodexClient()
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        session = await backend.start("作業", str(tmp_path))
        original_turn = session.turn_id
        started = [_activity("child-1", "started", "start-1"), _activity("child-2", "started", "start-2")]
        for activity in started:
            await _item(backend, session, activity)
        await _complete(backend, session, started)
        assert session.awaiting_auto_resume and not session.result_available
        assert (await owner.show_session(session.session_id))["result_held"]
        await _item(backend, session, _activity("child-1", "completed", "end-1"))
        await owner._advance_child_session_wait(session)
        assert len([r for r in client.requests if r[0] == "turn/start"]) == 1
        if terminal == "child-turn":
            await backend._handle_notification(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "child-2",
                        "turn": {"id": "child-turn", "status": "completed"},
                    },
                }
            )
        else:
            await _item(backend, session, _activity("child-2", terminal, "end-2"))
        await owner._advance_child_session_wait(session)
        await owner._advance_child_session_wait(session)
        assert session.turn_seq == 2 and session.auto_resume_consumed
        prompts = [r[1]["input"] for r in client.requests if r[0] == "turn/start"]
        assert len(prompts) == 2 and "child-1" in str(prompts[-1]) and "child-2" in str(prompts[-1])
        # 終端後に元turnの項目が再掲されても子を再稼働へ戻さない。
        await backend._handle_notification(
            {
                "method": "item/completed",
                "params": {
                    "threadId": session.session_id,
                    "turnId": original_turn,
                    "item": started[0],
                },
            }
        )
        assert not session.codex_live_subagent_thread_ids
        await _item(backend, session, {"id": "answer", "type": "agentMessage", "text": "回収後の全文"})
        await _complete(backend, session)
        assert (await owner.wait())["agent_message"] == "回収後の全文"
        # 再開したturnが新しい子を起動すると、新たな保留を一度行える。
        state.initialize_turn(session)
        await _item(backend, session, _activity("child-3", "started", "start-3"))
        await _complete(backend, session)
        assert session.awaiting_auto_resume


@pytest.mark.asyncio
async def test_all_children_terminal_before_parent_returns_result(tmp_path: pathlib.Path):
    """親turnの完了より前に子が終端していれば、そのturnの結果を返す。"""
    session = state.SessionState("parent", str(tmp_path), engine="codex", turn_id="turn")
    async with codex.AppServerManager({session.session_id: session}) as backend:
        await _item(backend, session, _activity("child", "started", "start"))
        await _item(backend, session, _activity("child", "completed", "start"))
        await _complete(backend, session)
        assert session.result_available and not session.awaiting_auto_resume


@pytest.mark.asyncio
async def test_child_message_to_parent_is_not_parent_self_message(tmp_path):
    """子から親への通常の配送は自分宛ての修復から分離し、item IDの名前空間も保つ。"""
    session = state.SessionState("parent", str(tmp_path), engine="codex", turn_id="turn")
    async with codex.AppServerManager({session.session_id: session}) as backend:
        await _item(backend, session, _activity("child", "started", "start"))
        await backend._handle_notification(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "child",
                    "turnId": "child-turn",
                    "item": _activity("parent", "interacted", "message"),
                },
            }
        )
        await backend._handle_notification(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "child",
                    "turn": {"id": "child-turn", "status": "completed"},
                },
            }
        )
        await _complete(backend, session)
        assert session.result_available and not session.codex_self_message_observed
        state.initialize_turn(session)
        await _item(backend, session, _activity("parent", "interacted", "message"))
        await _complete(backend, session)
        assert session.awaiting_auto_resume and session.codex_self_message_observed


@pytest.mark.parametrize("launch_kind", ["explore", "shell", "write", "delegate", "task"])
@pytest.mark.asyncio
async def test_self_message_repairs_final_response_once(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, launch_kind):
    """全modeで自分宛て送信を一度修復し、子への送信を通常の結果として返す。"""
    async with manager.AgentsServerManager(None) as owner, codex.AppServerManager(owner.sessions, owner._condition) as backend:
        install_backend(owner, "codex", backend)
        client = FakeCodexClient()
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        # 公開taskは役割のfrontmatterを解決した後にbackendのdelegateへ入る。
        session = await backend.start("作業", str(tmp_path), launch_kind="delegate" if launch_kind == "task" else launch_kind)
        await _item(backend, session, _activity("child", "interacted", "child-message"))
        await _complete(backend, session)
        assert session.result_available
        state.initialize_turn(session)
        await _item(backend, session, _activity(session.session_id, "interacted", "self-message"))
        await _complete(backend, session)
        assert session.awaiting_auto_resume and not session.result_available
        await owner._advance_child_session_wait(session)
        assert session.codex_self_message_repair_consumed
        assert "全文を最終応答" in str(client.requests)
        await _item(backend, session, _activity(session.session_id, "interacted", "self-again"))
        await _item(backend, session, {"id": "answer", "type": "agentMessage", "text": "修復後の全文"})
        await _complete(backend, session)
        assert (await owner.wait())["agent_message"] == "修復後の全文"
        assert len([r for r in client.requests if r[0] == "turn/start"]) == 2


@pytest.mark.asyncio
async def test_deadline_and_lost_connection_report_unfinished_threads(tmp_path: pathlib.Path):
    """保留の期限と接続喪失で、子が未観測のまま確定した診断を残す。"""
    session = state.SessionState("parent", str(tmp_path), engine="codex", turn_id="turn")
    async with codex.AppServerManager({session.session_id: session}) as backend:
        await _item(backend, session, _activity("child", "started", "start"))
        await _complete(backend, session)
        resume_waits.finalize_pending_result(session)
        assert session.error["heldResultFinalized"]
        assert session.error["unobservedSessions"] == ["child"]
        state.initialize_turn(session)
        await _item(backend, session, _activity("child", "started", "new-start"))
        await _complete(backend, session)
        client = FakeCodexClient()
        backend._clients[session.session_id] = cast(codex.JsonRpcProcess, client)
        await backend._handle_client_failure(RuntimeError("接続喪失"), client)
        assert session.result_available and session.status == "failed"
        assert session.error["unobservedSessions"] == ["child"]


@pytest.mark.asyncio
async def test_self_message_and_child_share_resume_and_restore_rejected_delivery(tmp_path, monkeypatch):
    """子の終端を待つ修復指示を一度にまとめ、配送拒否でも保留理由を保持する。"""
    async with manager.AgentsServerManager(None) as owner, codex.AppServerManager(owner.sessions, owner._condition) as backend:
        install_backend(owner, "codex", backend)
        client = FakeCodexClient()
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        session = await backend.start("作業", str(tmp_path))
        await _item(backend, session, _activity("child", "started", "start"))
        await _item(backend, session, _activity(session.session_id, "interacted", "self"))
        await _complete(backend, session)
        await owner._advance_child_session_wait(session)
        assert session.turn_seq == 1 and not session.codex_self_message_repair_consumed
        await _item(backend, session, _activity("child", "completed", "end"))
        start_turn = backend._start_turn
        monkeypatch.setattr(
            backend, "_start_turn", AsyncMock(side_effect=codex.JsonRpcResponseError("turn/start", -32600, "拒否"))
        )
        await owner._advance_child_session_wait(session)
        assert session.awaiting_auto_resume and session.codex_self_message_observed
        assert not session.codex_self_message_repair_consumed
        monkeypatch.setattr(backend, "_start_turn", start_turn)
        await owner._advance_child_session_wait(session)
        await owner._advance_child_session_wait(session)
        assert session.turn_seq == 2 and session.codex_self_message_repair_consumed
        assert len([r for r in client.requests if r[0] == "turn/start"]) == 2
