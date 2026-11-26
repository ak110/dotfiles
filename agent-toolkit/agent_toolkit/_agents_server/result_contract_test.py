# backendが結果を確定する処理を直接検証し、外部CLIの起動を避ける。
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import pathlib
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_toolkit._agents_server import (
    antigravity,
    claude,
    cli_turn,
    codex,
    launch_prompts,
    launch_requests,
    manager,
    mcp_tools,
    opencode,
    result_contract,
    state,
)
from agent_toolkit._common import session_registry, state_paths, task_documents
from agent_toolkit._testing.agents_server_support import ResultMessage


def _task_session(tmp_path: pathlib.Path) -> state.SessionState:
    plugin = tmp_path / "agent-toolkit"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "agent-toolkit"}', encoding="utf-8")
    roles = task_documents.role_document_directory(plugin)
    roles.mkdir(parents=True)
    document = roles / "exec.subagent.md"
    document.write_text("# 担当\n", encoding="utf-8")
    return state.SessionState("task", str(tmp_path), prompt=task_documents.task_prompt_header(document))


@pytest.mark.parametrize("legacy", [False, True])
def test_reader_fit_result_reads_front_loaded_and_legacy_targets(tmp_path: pathlib.Path, legacy: bool) -> None:
    session = _task_session(tmp_path)
    document = task_documents.launched_task_document(session.prompt)
    assert document is not None
    role = document.with_name("reader-fit-review.subagent.md")
    role.write_text(
        "---\nmode: explore\nmodel_type: review\nwrites_worktree: false\nrequired_inputs: [成果物]\n---\n",
        encoding="utf-8",
    )
    target = str(tmp_path / "actual.md")
    inputs = f"\n入力:\n成果物: {target}"
    references = "# 手順\n入力:\n成果物: /example.md\n"
    session.prompt = task_documents.task_prompt_header(role) + (
        "\n" + references + inputs
        if legacy
        else "\n役割: reader-fit-review" + inputs + task_documents.TASK_PROMPT_REFERENCES + references
    )
    message = (
        f"状態: completed\n- 成果物: {target}\n  完了状態: completed\n  比較資料: 全文\n"
        "  判定: 指摘なし\n  根拠: 本文を照合済み"
    )
    assert result_contract.invalid_result_reason(session, {"status": "completed", "agent_message": message}) is None
    reason = result_contract.invalid_result_reason(
        session, {"status": "completed", "agent_message": message.replace(target, "/example.md")}
    )
    assert reason is not None and target in reason


def _reader_fit_session(tmp_path: pathlib.Path) -> tuple[state.SessionState, str]:
    target = tmp_path / "対象.md"
    target.write_text("# 対象\n本文\n", encoding="utf-8")
    role = pathlib.Path(__file__).resolve().parents[2] / "skills/atk-mode/roles/reader-fit-review.subagent.md"
    _, prompt, kind, _ = launch_requests.task_document_request(
        str(role),
        {
            "成果物": str(target),
            "種別": "エンドユーザー向け文書",
            "読者像": "画面設計を読む開発者",
            "修正範囲": "新規の全文",
            "比較資料": f'[{{"比較元本文": "新規", "今回差分": "全文", "現行本文": "{target}"}}]',
        },
    )
    return state.SessionState("reader-fit", str(tmp_path), prompt=prompt, launch_kind=kind), str(target)


def _reader_fit_reason(session: state.SessionState, message: str) -> str | None:
    return result_contract.invalid_result_reason(session, {"status": "completed", "agent_message": message})


def test_reader_fit_accepts_multiline_reason_returned_on_2026_11_13(tmp_path: pathlib.Path) -> None:
    session, target = _reader_fit_session(tmp_path)
    message = (
        f"状態: completed\n未解決の指摘数: 0\n対象別の判定と証拠:\n- 成果物: {target}\n"
        "  完了状態: completed\n  比較資料: 新規・全文\n  判定: 指摘なし\n  根拠:\n"
        "  - 画面の各状態の記述を実装の分岐と照合した\n"
        "  - 用語は画面の表示文言と一致する\n"
        "    - 逐語引用1: 「送信中は入力欄を無効にする」\n"
        "    - 逐語引用2: 「エラー時は再送ボタンを表示する」\n"
        "  - 未確認の範囲は無い\n新しい要件: なし\n"
    )
    assert _reader_fit_reason(session, message) is None
    empty = message.split("  根拠:\n", maxsplit=1)[0] + "  根拠:\n新しい要件: なし\n"
    reason = _reader_fit_reason(session, empty)

    assert reason is not None and "根拠" in reason and "字下げ" in reason and "同じ行" in reason


@pytest.mark.parametrize(
    "ending", ["状態: completed", "続行できない区分: inaccessible_input\n続行できない理由: 対象に到達できない"]
)
def test_explore_final_line_is_accepted(tmp_path: pathlib.Path, ending: str) -> None:
    session = state.SessionState("explore", str(tmp_path), launch_kind="explore")
    result = {"status": "completed", "agent_message": f"観測結果\n{ending}\n", "error": None}
    assert result_contract.hold_invalid_result(session, result) is ending.startswith("続行できない区分:")


@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_explore_common_result_contract_is_published_without_recovery(tmp_path: pathlib.Path) -> None:
    """共通契約の先頭状態行を持つ返却を、本文と終了状態を保って公開する。"""
    session = state.SessionState("explore", str(tmp_path), launch_kind="explore")
    message = "状態: completed\n結果: 割当範囲の調査を完了\n根拠: 確認した本文と観測結果"

    cli_turn.finalize_turn(session, {"status": "completed", "agent_message": message, "error": None})

    assert session.result_available
    assert session.status == "completed" and session.agent_message == message
    assert not session.result_contract_recovery_pending and not session.awaiting_auto_resume


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
    assert details["nextAction"] == result_contract.RECOVERY_NEXT_ACTION


@pytest.mark.parametrize("message", ["続行できない理由:", "続行できない理由:   "])
def test_explore_empty_reason_is_held(tmp_path: pathlib.Path, message: str) -> None:
    session = state.SessionState("explore", str(tmp_path), launch_kind="explore")
    assert result_contract.hold_invalid_result(session, {"status": "completed", "agent_message": message, "error": None})


@pytest.mark.parametrize(
    ("message", "valid"),
    [
        ("状態: completed\n結果: 調査済み\n気付いた改善点: 対象の改善案", True),
        ("結果: 調査済み\n状態: completed", True),
        ("状態: completed  \n結果: 調査済み", True),
        ("状態: complete  \n結果: 調査済み", False),
        (" 状態: completed  \n結果: 調査済み", False),
        ("結果: 調査済み\n状態: completed  \n根拠: 本文中の状態行", False),
        ("結果: 読了部分\n続行できない区分: owner_input\n続行できない理由: 入力が必要\n気付いた改善点: 改善案", True),
        ("結果: 読了部分\n  続行できない区分: external_wait\n  続行できない理由: 外部完了待ち\n根拠: 観測済み", True),
        ("続行できない理由: 入力が必要", False),
        ("続行できない区分: unknown\n続行できない理由: 入力が必要\n気付いた改善点: 改善案", False),
        ("続行できない区分: owner_input\n 続行できない理由: 入力が必要", False),
        ("続行できない区分: owner_input\n続行できない理由:   ", False),
        ("続行できない区分: owner_input\n続行できない理由: 入力が必要\n続行できない理由: 別入力も必要", False),
        ("状態: completed  \n続行できない区分: unknown  \n続行できない理由: 入力が必要", False),
        ("待機中: session-1", True),
        ("結果: 調査済み", False),
    ],
)
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_freeform_explore_publishes_common_contract(tmp_path: pathlib.Path, message: str, valid: bool) -> None:
    """自由本文の調査結果を公開する境界で、全理由と追加報告を含む共通契約を検査する。"""
    session = state.SessionState("explore", str(tmp_path), launch_kind="explore")

    if valid and "続行できない理由:" in message:
        session.result_contract_recovery_consumed = True
    cli_turn.finalize_turn(session, {"status": "completed", "agent_message": message, "error": None})
    assert session.result_available is valid
    assert session.result_contract_recovery_pending is not valid
    if valid:
        assert session.agent_message == message and session.status == "completed"
    else:
        assert session.pending_result is not None and session.pending_result["agent_message"] == message
        session.result_contract_recovery_consumed = True
        state.begin_reply(session)
        cli_turn.finalize_turn(session, {"status": "completed", "agent_message": message, "error": None})
        assert session.result_available and session.status == "failed"
        assert session.agent_message == message
        assert session.error is not None and "resultContract" in session.error


@pytest.mark.parametrize(
    "message",
    [
        "状態: completed  \n結果: 完了\n根拠: 観測済み",
        "続行できない区分: owner_input\n続行できない理由: 委譲元の入力が必要",
        "  続行できない区分: permission_denied\n  続行できない理由: 権限が必要",
        "待機中: session-1",
    ],
)
def test_valid_result_is_not_held(tmp_path: pathlib.Path, message: str) -> None:
    """正常結果と待機はそのまま、不足申告は一度再確認して公開する。"""
    session = _task_session(tmp_path)
    result = {"status": "completed", "agent_message": message, "error": None}

    if "続行できない理由:" in message:
        assert result_contract.hold_invalid_result(session, result)
        session.result_contract_recovery_consumed = True
    assert not result_contract.hold_invalid_result(session, result)
    assert result["error"] is None


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("続行できない理由: 入力が必要", "1行目の続行できない理由の直前に同じインデントの続行できない区分がありません"),
        ("続行できない区分: unknown\n続行できない理由: 入力が必要", "1行目の続行できない区分が閉集合外です: unknown"),
        (
            " 続行できない区分: owner_input\n続行できない理由: 入力が必要",
            "2行目の続行できない理由の直前に同じインデントの続行できない区分がありません",
        ),
        ("続行できない区分: owner_input\n続行できない理由:   ", "2行目の続行できない理由が空です"),
        (
            "待機中: session-1\n続行できない理由: 入力が必要",
            "2行目の続行できない理由の直前に同じインデントの続行できない区分がありません",
        ),
    ],
)
def test_invalid_result_is_held_once(tmp_path: pathlib.Path, message: str, reason: str) -> None:
    session = _task_session(tmp_path)
    result: dict[str, Any] = {"status": "completed", "agent_message": message, "error": None}

    assert result_contract.hold_invalid_result(session, result)
    assert session.awaiting_auto_resume and session.pending_result == result
    session.result_contract_recovery_consumed = True

    assert not result_contract.hold_invalid_result(session, result)
    assert result["error"] == {
        "resultContract": reason,
        "nextAction": result_contract.RECOVERY_NEXT_ACTION,
    }
    assert result["agent_message"] == message
    assert not result_contract.hold_invalid_result(session, result)
    assert "同じsessionへ" in result_contract.RECOVERY_NEXT_ACTION
    assert "連続する2行" in result_contract.RECOVERY_NEXT_ACTION
    assert "agents_server.send_messageのsession_id" in result_contract.RECOVERY_NEXT_ACTION
    assert "prompt" in result_contract.RECOVERY_NEXT_ACTION
    assert "送信できない場合" in result_contract.RECOVERY_NEXT_ACTION
    assert "委譲元へ報告" in result_contract.RECOVERY_NEXT_ACTION


def test_multiple_reasons_report_the_first_contract_violation(tmp_path: pathlib.Path) -> None:
    session = _task_session(tmp_path)
    result = {
        "status": "completed",
        "agent_message": "続行できない区分: owner_input\n続行できない理由: 入力が必要\n続行できない理由: 承認が必要",
        "error": None,
    }

    assert result_contract.hold_invalid_result(session, result)
    assert session.result_contract_recovery_reason is not None


@pytest.mark.parametrize("launch_kind", ["delegate", "shell", "write"])
@pytest.mark.parametrize(
    ("message", "valid"),
    [
        ("正常な自由本文", False),
        ("続行できない理由: 入力が必要", False),
        ("状態: completed", True),
        ("続行できない区分: owner_input\n続行できない理由: 入力が必要", True),
        ("待機中: child", True),
        ("```\n状態: completed\n```", False),
    ],
)
def test_freeform_result_contract(tmp_path: pathlib.Path, launch_kind: state.LaunchKind, message: str, valid: bool) -> None:
    session = state.SessionState("delegate", str(tmp_path), prompt="自由本文", launch_kind=launch_kind)
    held = result_contract.hold_invalid_result(session, {"status": "completed", "agent_message": message, "error": None})
    assert held is (not valid and launch_kind != "shell")


@pytest.mark.parametrize("message", [None, "> 状態: completed", "~~~md\n状態: completed\n~~~"])
def test_task_result_does_not_accept_missing_body_or_examples(tmp_path: pathlib.Path, message: str | None) -> None:
    assert result_contract.hold_invalid_result(
        _task_session(tmp_path), {"status": "completed", "agent_message": message, "error": None}
    )


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_non_completed_result_preserves_existing_recovery(tmp_path: pathlib.Path, status: str) -> None:
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
    session = _task_session(tmp_path)
    session.launch_kind = launch_kind
    session.result_contract_recovery_consumed = recovered
    session.agent_message = message
    fails = recovered and held
    recheck = not held and "続行できない理由:" in message
    held = (held or recheck) and not recovered
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
            await backend._consume_result_message(session, ResultMessage(message))  # noqa: SLF001
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
@pytest.mark.parametrize("explore", [False, True])
@pytest.mark.parametrize("role", [False, True])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_manager_recovers_once_and_preserves_delivery_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    delivery_failure: bool,
    explore: bool,
    role: bool,
) -> None:
    """同じsessionへ1回だけ継続し、成功後の最終結果と配送失敗時の本文を保持する。"""
    session = _task_session(tmp_path)
    children = {
        "live_child_session_ids": {"child-running"},
        "terminal_child_session_ids": {"child-completed"},
        "codex_live_subagent_thread_ids": {"thread-running"},
        "codex_terminal_subagent_thread_ids": {"thread-completed"},
    }
    for name, identifiers in children.items():
        getattr(session, name).update(identifiers)
    if explore:
        session.launch_kind = "explore"
        if not role:
            session.prompt = "調査せよ"
    message = "未読が残る" if explore and not role else "続行できない理由: 分割読取が残る"
    assert result_contract.hold_invalid_result(session, {"status": "completed", "agent_message": message, "error": None})

    async def reply(current: state.SessionState, prompt: str) -> dict[str, str]:
        assert current is session and "診断:" in prompt
        if explore and not role:
            assert "未読・未走査" in prompt and "状態: completed" in prompt
            assert "続行できない区分:" in prompt and "同じインデント" in prompt
            assert "成果と追加報告" in prompt and "全体成功行を付けない" in prompt
        else:
            assert "未読・未走査" not in prompt
        state.begin_reply(current, preserve_waits=True)
        return {"delivery": "reply_started"}

    sender = AsyncMock(side_effect=RuntimeError("配送拒否") if delivery_failure else reply)
    backend = Mock(send_message=sender)
    async with manager.AgentsServerManager() as owner:
        monkeypatch.setattr(owner, "_backend", Mock(return_value=backend))
        await owner._advance_child_session_wait(session)  # noqa: SLF001
        assert session.result_contract_recovery_consumed
        assert sender.await_count == 1
        assert {name: getattr(session, name) for name in children} == children
        if delivery_failure:
            assert session.agent_message == message and session.status == "failed"
            assert "配送拒否" in str(session.error) and "resultContract" in str(session.error)
            assert session.error is not None
            assert session.error["nextAction"] == result_contract.RECOVERY_NEXT_ACTION
        else:
            final = "結果: 完了\n状態: completed" if explore and not role else "状態: completed\n結果: 完了"
            cli_turn.finalize_turn(session, {"status": "completed", "agent_message": final, "error": None})
            assert session.result_available and session.agent_message == final
        await owner._advance_child_session_wait(session)  # noqa: SLF001
        assert sender.await_count == 1
        assert {name: getattr(session, name) for name in children} == children


@pytest.mark.parametrize("backend_type", [antigravity.AntigravityManager, opencode.OpenCodeManager])
@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_cli_role_model_change_rejects_public_reply_but_keeps_result_recovery(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, backend_type: type[cli_turn.CliTurnManager], stale: bool
) -> None:
    session = _task_session(tmp_path)
    session.engine = backend_type.ENGINE
    session.model_type = "orchestrate" if stale else "execute"
    session.explicit_role_model = False
    session.model = "provider/configured" if session.engine == "opencode" else "configured"
    session.effort = "medium"
    separator = "@" if session.engine == "opencode" else "/"
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", f"{session.engine}:{session.model}{separator}{session.effort}")

    async def start(*_args: Any, **kwargs: Any) -> state.SessionState:
        session.turn_seq = kwargs["turn_seq"]
        cli_turn.finalize_turn(session, {"status": "completed", "agent_message": "状態: completed", "error": None})
        return session

    started = AsyncMock(side_effect=start)
    async with manager.AgentsServerManager() as owner:
        backend = backend_type()
        owner.sessions[session.session_id] = session
        monkeypatch.setattr(owner, "_backend", Mock(return_value=backend))
        monkeypatch.setattr(mcp_tools, "_MANAGER", owner)
        monkeypatch.setattr(backend, "_start_turn", started)
        message = "続行できない理由: 入力が必要"
        assert result_contract.hold_invalid_result(session, {"status": "completed", "agent_message": message, "error": None})

        await owner._advance_child_session_wait(session)

        assert started.await_count == 1
        assert session.result_contract_recovery_consumed
        recovery_prompt = started.await_args_list[0].args[0]
        started.reset_mock()
        if stale:
            with pytest.raises(ToolError, match="handoff_from"):
                await mcp_tools.mcp.call_tool(
                    "send_message", {"session_id": session.session_id, "prompt": recovery_prompt, "result_recovery": True}
                )
            started.assert_not_awaited()
        else:
            await mcp_tools.mcp.call_tool("send_message", {"session_id": session.session_id, "prompt": "別の依頼"})
            started.assert_awaited_once()


@pytest.mark.parametrize("backend_type", [antigravity.AntigravityManager, opencode.OpenCodeManager])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.parametrize("outcome", ["completed", "late_error", "kill", "start_error"])
async def test_cli_result_recovery_is_reserved_before_waiting_for_delivery(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, backend_type: type[cli_turn.CliTurnManager], outcome: str
) -> None:
    session = _task_session(tmp_path)
    session.engine = backend_type.ENGINE
    session.model_type = "orchestrate"
    session.explicit_role_model = False
    preparing = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()
    final = "状態: completed\n結果: 修復済み"

    async def start(*_args: Any, **kwargs: Any) -> state.SessionState:
        if outcome == "start_error":
            raise RuntimeError("回復ターンの起動に失敗")
        session.turn_seq = kwargs["turn_seq"]
        cli_turn.finalize_turn(session, {"status": "completed", "agent_message": final, "error": None})
        return session

    started = AsyncMock(side_effect=start)
    async with manager.AgentsServerManager() as owner:
        backend = backend_type()
        owner.sessions[session.session_id] = session
        monkeypatch.setattr(owner, "_backend", Mock(return_value=backend))
        monkeypatch.setattr(backend, "_start_turn", started)
        send = owner._send_with_resources
        preparations = []

        async def blocked_send(*args: Any, **kwargs: Any) -> dict[str, Any]:
            preparations.append(session.turn_seq)
            preparing.set()
            await release.wait()
            response = await send(*args, **kwargs)
            if outcome == "late_error":
                raise RuntimeError("後処理の応答を失った")
            return response

        monkeypatch.setattr(owner, "_send_with_resources", blocked_send)
        assert result_contract.hold_invalid_result(
            session, {"status": "completed", "agent_message": "続行できない理由: 入力が必要", "error": None}
        )

        async def observe_again() -> None:
            second_entered.set()
            await owner._advance_child_session_wait(session)

        async with asyncio.TaskGroup() as group:
            group.create_task(owner._advance_child_session_wait(session))
            await preparing.wait()
            group.create_task(observe_again())
            await second_entered.wait()
            if outcome == "kill":
                killed = await owner.kill(session.session_id)
                assert killed["status"] == "interrupted"
            release.set()

        if outcome == "kill":
            started.assert_not_awaited()
            assert session.status == "interrupted" and session.error is None
        elif outcome == "start_error":
            started.assert_awaited_once()
            assert session.status == "failed"
            assert session.error is not None and "回復ターンの起動に失敗" in session.error["message"]
        else:
            started.assert_awaited_once()
            assert session.status == "completed"
            assert session.agent_message == final and session.error is None
        assert preparations == [0]


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
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session = _task_session(tmp_path)
    session.publish_registry = True
    session.status = "completed"
    session.record_turn_completed()
    session.result_contract_recovery_consumed = True
    session.touch()
    await session.drain_registration()

    session_registry.publish(session.session_id, state=session_registry.PublicationState.RESULT_COMPLETED, cwd=session.cwd)
    info = session_registry.resolve(session.session_id).resume_info
    assert info is not None and info.result_contract_recovery_consumed
    async with manager.AgentsServerManager() as owner:
        restored = await owner._restore_registry_session(session.session_id)  # noqa: SLF001
    assert restored is not None and restored.result_contract_recovery_consumed


def _role_session(name: str, **fields: Any) -> state.SessionState:
    document = task_documents.find_role_document(launch_prompts.PLUGIN_ROOT, name)
    assert document is not None, name
    return state.SessionState("role", str(document.parent), prompt=task_documents.task_prompt_header(document), **fields)


@pytest.mark.parametrize(
    ("name", "message", "valid"),
    [
        ("add-wi.subagent.md", "調査は未完了のため、completedとは返せない。原因分析が残っている。", False),
        ("add-wi.subagent.md", "状態: completed\n結果: 投入済み", True),
        ("add-wi.subagent.md", "待機中: 01a1d000", True),
        ("add-wi.subagent.md", "結果: 調査済み\n続行できない区分: owner_input\n続行できない理由: 対象の確定が必要", True),
        ("exec.subagent.md", "計画検査完了\n計画ファイル: なし\n統合区分: マージあり\nAWI: a.md", False),
        (
            "exec.subagent.md",
            "計画検査完了\n計画ファイル: /plan.md\n統合区分: マージなし\nAWI: a.md\n"
            "計画ファイル: なし\n統合区分: マージあり\nAWI: b.md",
            False,
        ),
        (
            "exec.subagent.md",
            "計画検査完了\n計画ファイル: /plan.md\n統合区分: マージなし\nAWI: a.md\n"
            "計画ファイル: なし\n統合区分: マージなし\nAWI: b.md",
            True,
        ),
        ("exec.subagent.md", "計画作成完了\n計画ファイル: /plan.md", True),
        ("exec.subagent.md", "計画なし準備完了\n統合区分: マージあり\nAWI: a.md", True),
        ("exec.subagent.md", "統合準備完了\n統合区分: マージあり", True),
        ("exec.subagent.md", "実装完了\n検証結果: 全件成功", True),
        ("exec.subagent.md", "対応完了（再レビュー不要）", True),
        ("exec.subagent.md", "実装は途中で、統合完了までは進めていない", False),
        ("usability-review.subagent.md", "判定: 合格\n根拠: 主要シナリオを操作した", False),
    ],
)
def test_role_task_requires_fixed_line_or_classified_reason(name: str, message: str, valid: bool) -> None:
    """役割taskは状態行・役割固有の固定行・待機・区分付きの続行不能だけを完了として公開する。"""
    session = _role_session(name)
    reason = result_contract.invalid_result_reason(session, {"status": "completed", "agent_message": message})
    assert (reason is None) is valid


@pytest.mark.parametrize(("later_result", "valid"), [("", False), ("\n\n統合準備完了", True)])
def test_generic_success_does_not_finalize_exec_plan_notification(later_result: str, valid: bool) -> None:
    session = _role_session("exec.subagent.md")
    message = "状態: completed\n計画検査完了\n計画ファイル: なし\n統合区分: マージあり\nAWI: a.md"
    reason = result_contract.invalid_result_reason(session, {"status": "completed", "agent_message": message + later_result})
    assert (reason is None) is valid


@pytest.mark.parametrize(
    ("launch_kind", "effort", "expected", "lookup_fails"),
    [
        ("delegate", "low", "low", False),
        ("delegate", "max", "max", False),
        ("explore", "low", "low", False),
        ("delegate", "low", "low", True),
    ],
)
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_role_free_text_recovery_preserves_effort_once(
    monkeypatch: pytest.MonkeyPatch,
    launch_kind: str,
    effort: str,
    expected: str,
    lookup_fails: bool,
) -> None:
    """自由文の早期返却は同じeffortで一度継続し、再違反を診断付きで公開する。

    最上段・軽量起動・段の取得失敗ではeffortを変えずに回復入力だけを送る。
    """
    if lookup_fails:

        def fail(*_: object) -> tuple[str, ...]:
            raise OSError("段の一覧を取得できない")

        monkeypatch.setattr("agent_toolkit._common.effort.cli_levels", fail)
    session = _role_session(
        "defect-investigation.subagent.md", engine="claude", model="opus", effort=effort, launch_kind=launch_kind
    )
    message = "調査は未完了…completedとは返せない"
    assert result_contract.hold_invalid_result(session, {"status": "completed", "agent_message": message, "error": None})
    prompts: list[str] = []

    async def reply(current: state.SessionState, prompt: str) -> dict[str, str]:
        prompts.append(prompt)
        state.begin_reply(current, preserve_waits=True)
        return {"delivery": "reply_started"}

    backend = Mock(send_message=AsyncMock(side_effect=reply), set_effort=AsyncMock())
    async with manager.AgentsServerManager() as owner:
        monkeypatch.setattr(owner, "_backend", Mock(return_value=backend))
        await owner._advance_child_session_wait(session)  # noqa: SLF001
    assert len(prompts) == 1 and "診断:" in prompts[0]
    assert session.effort == expected
    backend.set_effort.assert_not_awaited()
    assert not session.effort_history
    assert "effortを" not in prompts[0]
    result: dict[str, Any] = {"status": "completed", "agent_message": message, "error": None}
    assert not result_contract.hold_invalid_result(session, result)
    error = result["error"]
    assert isinstance(error, dict)
    details = dict(error)
    assert details["resultContract"] and details["nextAction"] == result_contract.RECOVERY_NEXT_ACTION


@pytest.mark.parametrize(
    "role,value,valid",
    [
        ("exec.subagent.md", "状態: 未完了", False),
        ("exec.subagent.md", "状態: unfinished", False),
        ("exec.subagent.md", "状態: completed extra", False),
        ("add-wi.subagent.md", "状態: drafted", True),
        ("repository-process.subagent.md", "状態: incomplete", True),
        ("repository-process.subagent.md", "状態: pending", True),
    ],
)
def test_role_state_values_match_declaration(role, value, valid):
    session = _role_session(role)
    reason = result_contract.invalid_result_reason(session, {"status": "completed", "agent_message": value})
    assert (reason is None) is valid


@pytest.mark.asyncio
@pytest.mark.parametrize("external", [False, True])
async def test_task_rechecks_block_once_and_accepts_second_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, external: bool
) -> None:
    session = _task_session(tmp_path)
    message = "続行できない区分: owner_input\n続行できない理由: 委譲元の入力が必要"
    assert result_contract.hold_invalid_result(session, {"status": "completed", "agent_message": message})

    async def reply(current: state.SessionState, prompt: str) -> dict[str, str]:
        assert current is session and "担当内で補える" in prompt
        state.begin_reply(current, preserve_waits=True)
        return {"delivery": "reply_started"}

    backend = Mock(send_message=AsyncMock(side_effect=reply))
    async with manager.AgentsServerManager() as owner:
        monkeypatch.setattr(owner, "_backend", Mock(return_value=backend))
        await owner._advance_child_session_wait(session)  # noqa: SLF001
    backend.send_message.assert_awaited_once()
    result: dict[str, Any] = {"status": "completed", "agent_message": message if external else "状態: completed"}
    assert not result_contract.hold_invalid_result(session, result)
    assert result["status"] == "completed" and "error" not in result


@pytest.mark.parametrize("kind", ["delegate", "write"])
def test_invalid_freeform_fails_after_one_recovery(kind: state.LaunchKind, tmp_path: pathlib.Path) -> None:
    """自由本文の再違反は、元の本文を保ち診断付きの失敗にする。"""
    session = state.SessionState("free", str(tmp_path), prompt="自由本文", launch_kind=kind)
    message = "まだ実施していない"
    result: dict[str, Any] = {"status": "completed", "agent_message": message}
    assert result_contract.hold_invalid_result(session, result)
    session.result_contract_recovery_consumed = True
    assert not result_contract.hold_invalid_result(session, result)
    assert result["status"] == "failed" and result["agent_message"] == message
    assert result["error"]["resultContract"]


@pytest.mark.parametrize(
    ("comparison", "accessibility", "valid"),
    [
        ("同じ製品の一覧と設定画面を比較した | 外見: 共通部品と配色が一致した", True, True),
        ("同じ製品の一覧と設定画面を比較した | 外見: 共通部品と配色が一致した", False, False),
        ("同じ製品の一覧と設定画面を比較した", True, False),
        ("同じ製品の一覧と設定画面を比較した | 外見: なし", True, False),
        ("", True, False),
        ("外部類例との比較は未確認", True, False),
    ],
)
def test_usability_pass_requires_each_whole_screen_observation(comparison: str, accessibility: bool, valid: bool) -> None:
    session = _role_session("usability-review.subagent.md")
    message = (
        "判定: 合格\n"
        f"類例の比較: {comparison}\n"
        "情報設計: 既定のホストを上部に置き、左の分類から設定を切り替える\n"
        "行の揃い: 広幅・狭幅で状態の位置を比較した\n"
        "冗長表示: 一覧の値がない行に固定文言が出ない\n"
        "入力負担: 期限を日時欄から選べる\n"
        "modern-web-guidanceのガイドと適用結果: フォームのラベルとエラー通知を実画面で確認した"
    )
    if accessibility:
        message += "\n可読性・アクセシビリティ: 最小14px、主要文字は7:1、TabとEnterで保存、ボタンは44px角"
    reason = result_contract.invalid_result_reason(session, {"status": "completed", "agent_message": message})
    assert (reason is None) is valid
    if reason:
        assert ("類例の比較" if accessibility else "可読性・アクセシビリティ") in reason
