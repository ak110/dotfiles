"""task返却の続行不能区分検査を確認する。"""

# backendが結果を確定する処理を直接検証し、外部CLIの起動を避ける。
# pylint: disable=protected-access

from __future__ import annotations

import pathlib
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent_toolkit._agents_server import antigravity, claude, cli_turn, codex, manager, opencode, result_contract, state
from agent_toolkit._common import session_registry, state_paths, task_documents
from agent_toolkit._testing.agents_server_support import ResultMessage


def _task_session(tmp_path: pathlib.Path) -> state.SessionState:
    share = tmp_path / "agent-toolkit" / "share"
    share.mkdir(parents=True)
    (share.parent / ".claude-plugin").mkdir()
    (share.parent / ".claude-plugin" / "plugin.json").write_text('{"name": "agent-toolkit"}', encoding="utf-8")
    document = share / "exec.subagent.md"
    document.write_text("# 担当\n", encoding="utf-8")
    return state.SessionState("task", str(tmp_path), prompt=task_documents.task_prompt_header(document))


@pytest.mark.parametrize("ending", ["状態: completed", "続行できない理由: 対象に到達できない"])
def test_explore_final_line_is_accepted(tmp_path: pathlib.Path, ending: str) -> None:
    """自由本文のexploreは正当な最終行をそのまま公開する。"""
    session = state.SessionState("explore", str(tmp_path), launch_kind="explore")
    result = {"status": "completed", "agent_message": f"観測結果\n{ending}\n", "error": None}
    assert not result_contract.hold_invalid_result(session, result)


def test_explore_missing_final_line_fails_after_one_recovery(tmp_path: pathlib.Path) -> None:
    """固定行のない返却は1回保留し、再発時に本文を保った失敗にする。"""
    session = state.SessionState("explore", str(tmp_path), launch_kind="explore")
    result: dict[str, Any] = {"status": "completed", "agent_message": "未読が残る", "error": None}
    assert result_contract.hold_invalid_result(session, result)
    session.result_contract_recovery_consumed = True
    assert not result_contract.hold_invalid_result(session, result)
    assert result["status"] == "failed"
    assert result["agent_message"] == "未読が残る"
    error = result["error"]
    assert isinstance(error, dict)
    details = dict(error)
    assert "返却が完了を示しません" in details["resultContract"]
    assert "小さく分け" in details["nextAction"]


@pytest.mark.parametrize("message", ["続行できない理由:", "続行できない理由:   "])
def test_explore_empty_reason_is_held(tmp_path: pathlib.Path, message: str) -> None:
    """未読範囲・理由・再開入力を示す行には非空の値を求め、意味は判定しない。"""
    session = state.SessionState("explore", str(tmp_path), launch_kind="explore")
    assert result_contract.hold_invalid_result(session, {"status": "completed", "agent_message": message, "error": None})


@pytest.mark.parametrize(
    "message",
    [
        "状態: completed\n結果: 完了\n根拠: 観測済み",
        "続行できない区分: owner_input\n続行できない理由: 委譲元の入力が必要",
        "  続行できない区分: permission_denied\n  続行できない理由: 権限が必要",
        "待機中: session-1",
    ],
)
def test_valid_result_is_not_held(tmp_path: pathlib.Path, message: str) -> None:
    """正常結果、有効区分、待機表明は公開する。"""
    session = _task_session(tmp_path)

    assert not result_contract.hold_invalid_result(session, {"status": "completed", "agent_message": message, "error": None})


@pytest.mark.parametrize(
    "message",
    [
        "続行できない理由: 入力が必要",
        "続行できない区分: unknown\n続行できない理由: 入力が必要",
        " 続行できない区分: owner_input\n続行できない理由: 入力が必要",
        "待機中: session-1\n続行できない理由: 入力が必要",
    ],
)
def test_invalid_result_is_held_once(tmp_path: pathlib.Path, message: str) -> None:
    """理由の直前に同インデントの有効区分が無ければ保留する。"""
    session = _task_session(tmp_path)
    result: dict[str, Any] = {"status": "completed", "agent_message": message, "error": None}

    assert result_contract.hold_invalid_result(session, result)
    assert session.awaiting_auto_resume and session.pending_result == result
    session.result_contract_recovery_consumed = True

    assert not result_contract.hold_invalid_result(session, result)
    assert result["error"] == {
        "resultContract": result_contract.invalid_result_reason(session, result),
        "nextAction": result_contract.RECOVERY_NEXT_ACTION,
    }
    assert "同じsessionへ" in result_contract.RECOVERY_NEXT_ACTION
    assert "連続する2行" in result_contract.RECOVERY_NEXT_ACTION
    assert "agents_server.send_messageのsession_id" in result_contract.RECOVERY_NEXT_ACTION
    assert "prompt" in result_contract.RECOVERY_NEXT_ACTION
    assert "送信できない場合" in result_contract.RECOVERY_NEXT_ACTION
    assert "委譲元へ報告" in result_contract.RECOVERY_NEXT_ACTION


def test_multiple_reasons_report_the_first_contract_violation(tmp_path: pathlib.Path) -> None:
    """複数理由を含む返却でも、違反があれば保留して診断する。"""
    session = _task_session(tmp_path)
    result = {
        "status": "completed",
        "agent_message": "続行できない区分: owner_input\n続行できない理由: 入力が必要\n続行できない理由: 承認が必要",
        "error": None,
    }

    assert result_contract.hold_invalid_result(session, result)
    assert session.result_contract_recovery_reason is not None


def test_non_task_result_is_not_held(tmp_path: pathlib.Path) -> None:
    """自由本文とagent-toolkit外の文書は検査対象にしない。"""
    session = state.SessionState("delegate", str(tmp_path), prompt="自由本文")

    assert not result_contract.hold_invalid_result(
        session, {"status": "completed", "agent_message": "続行できない理由: 入力が必要", "error": None}
    )


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_non_completed_result_preserves_existing_recovery(tmp_path: pathlib.Path, status: str) -> None:
    """利用上限・過負荷・取消が使う非正常結果は形式回復で保留しない。"""
    session = _task_session(tmp_path)

    assert not result_contract.hold_invalid_result(
        session, {"status": status, "agent_message": "続行できない理由: 入力が必要", "error": {"type": "usage_limit"}}
    )
    assert session.pending_result is None and not session.awaiting_auto_resume


@pytest.mark.parametrize("engine", ["codex", "claude", "agy", "opencode"])
@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "write", "shell"])
@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize(
    ("message", "held"),
    [
        ("状態: completed\n結果: 完了\n根拠: 検証済み", False),
        ("結果: 完了\n状態: completed", False),
        ("続行できない区分: owner_input\n続行できない理由: 入力が必要", False),
        ("続行できない理由: 入力が必要", True),
        ("続行できない区分: unknown\n続行できない理由: 入力が必要", True),
    ],
)
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_backend_terminal_boundary_checks_task_result(
    tmp_path: pathlib.Path, engine: str, launch_kind: state.LaunchKind, message: str, held: bool, recovered: bool
) -> None:
    """各backendが結果を確定するとき、通常・軽量taskを同じ公開前検査へ渡す。"""
    session = _task_session(tmp_path)
    session.launch_kind = launch_kind
    session.result_contract_recovery_consumed = recovered
    session.agent_message = message
    fails = recovered and held and launch_kind == "explore"
    held = held and not recovered
    if engine == "codex":
        async with codex.AppServerManager() as backend:
            backend.sessions[session.session_id] = session
            await backend._handle_notification(  # noqa: SLF001
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": session.session_id,
                        "turn": {"id": "turn", "status": "completed", "error": None},
                    },
                }
            )
    elif engine == "claude":
        async with claude.ClaudeServerManager() as backend:
            await backend._consume_result_message(session, ResultMessage(message), False)  # noqa: SLF001
    elif engine == "agy":
        async with antigravity.AntigravityManager() as backend:
            await backend.process_event(session, {"event": "result", "result": {"status": "SUCCESS", "response": message}})
    else:
        async with opencode.OpenCodeManager() as backend:
            backend.finish_process(session, 0, "", False)
    assert session.result_contract_recovery_pending is held
    assert session.result_available is not held
    if held:
        assert session.pending_result is not None
        assert session.pending_result["agent_message"] == message
    else:
        assert session.agent_message == message
        if fails:
            assert session.status == "failed"
            assert session.error is not None and "resultContract" in session.error


@pytest.mark.parametrize("delivery_failure", [False, True])
@pytest.mark.parametrize("background_consumed", [False, True])
@pytest.mark.parametrize("explore", [False, True])
@pytest.mark.parametrize("role", [False, True])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_manager_recovers_once_and_preserves_delivery_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    delivery_failure: bool,
    background_consumed: bool,
    explore: bool,
    role: bool,
) -> None:
    """同じsessionへ1回だけ継続し、成功後の最終結果と配送失敗時の本文を保持する。"""
    session = _task_session(tmp_path)
    if explore:
        session.launch_kind = "explore"
        if not role:
            session.prompt = "調査せよ"
    session.auto_resume_consumed = background_consumed
    message = "未読が残る" if explore and not role else "続行できない理由: 分割読取が残る"
    assert result_contract.hold_invalid_result(session, {"status": "completed", "agent_message": message, "error": None})

    async def reply(current: state.SessionState, prompt: str) -> dict[str, str]:
        assert current is session and "診断:" in prompt
        if explore and not role:
            assert "未読・未走査" in prompt and "状態: completed" in prompt
        else:
            assert "未読・未走査" not in prompt
        state.begin_reply(current)
        return {"delivery": "reply_started"}

    sender = AsyncMock(side_effect=RuntimeError("配送拒否") if delivery_failure else reply)
    backend = Mock(send_message=sender)
    async with manager.AgentsServerManager() as owner:
        monkeypatch.setattr(owner, "_backend", Mock(return_value=backend))
        await owner._advance_child_session_wait(session)  # noqa: SLF001
        assert session.result_contract_recovery_consumed
        assert session.auto_resume_consumed is background_consumed
        assert sender.await_count == 1
        if delivery_failure:
            assert session.agent_message == message and session.status == "failed"
            assert "配送拒否" in str(session.error) and "resultContract" in str(session.error)
            assert session.error is not None
            assert session.error["nextAction"] == result_contract.RECOVERY_NEXT_ACTION
        else:
            final = "結果: 完了\n状態: completed" if explore and not role else "結果: 完了"
            cli_turn.finalize_turn(session, {"status": "completed", "agent_message": final, "error": None})
            assert session.result_available and session.agent_message == final
        await owner._advance_child_session_wait(session)  # noqa: SLF001
        assert sender.await_count == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_antigravity_contract_hold_keeps_result_with_pending_child(tmp_path: pathlib.Path) -> None:
    """孫session待機が併存しても、形式回復へ渡す結果を旧本文で上書きしない。"""
    session = _task_session(tmp_path)
    session.agent_message = "古い進捗"
    session.live_child_session_ids.add("child")
    message = "続行できない理由: 入力が必要"
    async with antigravity.AntigravityManager() as backend:
        await backend.process_event(session, {"event": "result", "result": {"status": "SUCCESS", "response": message}})
    assert session.result_contract_recovery_pending
    assert session.pending_result is not None and session.pending_result["agent_message"] == message


@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_consumption_survives_registry_publication_and_new_owner(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成側の状態をdiskへ保存し、別所有者の登録簿復元でも1回上限を保持する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session = _task_session(tmp_path)
    session.publish_registry = True
    session.status = "completed"
    session.turn_completed = True
    session.result_contract_recovery_consumed = True
    session.touch()
    await session.drain_registration()
    # 別の更新主体が項目を省略しても消費済みから未消費へは戻さない。
    session_registry.publish(session.session_id, terminal=True, cwd=session.cwd)
    info = session_registry.resolve(session.session_id).resume_info
    assert info is not None and info.result_contract_recovery_consumed
    async with manager.AgentsServerManager() as owner:
        restored = await owner._restore_registry_session(session.session_id)  # noqa: SLF001
    assert restored is not None and restored.result_contract_recovery_consumed
