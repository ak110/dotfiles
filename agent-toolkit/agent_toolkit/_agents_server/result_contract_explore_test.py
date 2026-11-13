"""不足を申告した探索の一度の再確認と、正当な差し戻しの公開を確かめる。"""

from unittest.mock import AsyncMock, Mock

import pytest

from agent_toolkit._agents_server import cli_turn, manager, result_contract, state


@pytest.mark.parametrize("classification", ["owner_input", "permission_denied", "inaccessible_input"])
@pytest.mark.parametrize("fault", ["none", "effort", "delivery"])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_explore_rechecks_preconditions_once(monkeypatch, tmp_path, classification, fault):
    session = state.SessionState("explore", str(tmp_path), launch_kind="explore", engine="codex", model="test", effort="low")
    session.effort_levels = ("low", "medium", "high")
    message = f"観測済み成果\n続行できない区分: {classification}\n続行できない理由: 未取得資料がある"
    cli_turn.finalize_turn(session, {"status": "completed", "agent_message": message, "error": None})
    assert session.result_contract_recovery_pending and not session.result_available
    prompts = []

    async def reply(current, prompt):
        prompts.append(prompt)
        if fault == "delivery":
            return {"delivery": "reply_failed"}
        state.begin_reply(current, preserve_waits=True)
        return {"delivery": "reply_started"}

    async def apply(_session, _effort):
        if fault == "effort":
            raise OSError("effortを変更できない")

    backend = Mock(send_message=AsyncMock(side_effect=reply))
    async with manager.AgentsServerManager() as owner:
        monkeypatch.setattr(owner, "_backend", Mock(return_value=backend))
        monkeypatch.setattr(owner, "_apply_effort", apply)
        # 所有監視の一度の遷移を、外部backendを代用した状態で観測する。
        await owner._advance_child_session_wait(session)  # noqa: SLF001  # pylint: disable=protected-access
    assert len(prompts) == 1
    assert "形式違反と判定したものではない" in prompts[0]
    assert "試した手段と再開条件" in prompts[0] and "権限拒否・認可不足の回避は求めない" in prompts[0]
    assert session.effort == ("low" if fault == "effort" else "medium")
    if fault == "delivery":
        assert session.status == "failed" and session.agent_message == message
        assert session.error["nextAction"]
    else:
        cli_turn.finalize_turn(session, {"status": "completed", "agent_message": message, "error": None})
        assert session.result_available and session.status == "completed"
        assert session.agent_message == message and not session.result_contract_recovery_pending
        if fault == "effort":
            assert session.error["effortRecovery"]["nextAction"]
            assert "effortを変更できない" in session.error["effortRecovery"]["error"]


@pytest.mark.parametrize("message", ["状態: completed\n結果: 調査済み", "待機中: child"])
def test_explore_success_and_wait_need_no_precondition_recheck(tmp_path, message):
    session = state.SessionState("explore", str(tmp_path), launch_kind="explore")
    assert not result_contract.hold_invalid_result(session, {"status": "completed", "agent_message": message})
