"""`_agents_server/resume_waits.py`の振る舞いを検証する。"""

from __future__ import annotations

import pytest

from agent_toolkit._agents_server import resume_waits, state, turn_state


@pytest.mark.parametrize("keep_resume_chain", [False, True])
def test_finalize_pending_result_records_remaining_wait_targets_and_keeps_error(keep_resume_chain: bool) -> None:
    """保留結果の確定は残ったバックグラウンドタスクと孫sessionを既存の`error`へ併合し、継続の連鎖では記録しない。

    全ての確定の契機（期限到来、ストリーム終端、`kill`、`send_message`、孫sessionの監視）でこの共通処理を実行する。
    既存の`error`を置き換えると、失敗の内容や先に記録した未観測の孫sessionが失われる。
    継続の連鎖（過負荷と利用上限の待機後の継続）は同じsessionで新しいturnを始めるため、確定した結果を公開しない。
    """
    session = state.SessionState("parent-1", "/tmp")
    session.hold_result(
        turn_state.BackgroundWait(
            {
                "status": "completed",
                "agent_message": "待機中: task-1",
                "error": {"message": "既存の失敗", "unobservedSessions": ["child-0"]},
            },
            None,
        )
    )
    session.live_tasks["task-1"] = state.LiveTask("local_bash", "CIの完了待ち", "2026-10-06T05:59:06Z")
    session.live_child_session_ids.add("child-1")
    session.overload_resume_count = 2
    session.overload_first_at = "2026-10-06T05:58:06Z"
    session.usage_limit_wait_count = 3
    session.usage_limit_first_at = "2026-10-06T05:57:06Z"

    resume_waits.finalize_pending_result(
        session, touch=False, keep_resume_chain=keep_resume_chain, unobserved_sessions={"child-2"}
    )

    if keep_resume_chain:
        assert session.overload_resume_count == 2 and session.overload_first_at == "2026-10-06T05:58:06Z"
        assert session.usage_limit_wait_count == 3 and session.usage_limit_first_at == "2026-10-06T05:57:06Z"
        assert session.error == {"message": "既存の失敗", "unobservedSessions": ["child-0"]}
    else:
        assert session.overload_resume_count == 0 and session.overload_first_at is None
        assert session.usage_limit_wait_count == 0 and session.usage_limit_first_at is None
        assert session.error == {
            "message": "既存の失敗",
            "unobservedSessions": ["child-0", "child-1", "child-2"],
            "unfinishedBackgroundTasks": ["task-1"],
            "heldResultFinalized": True,
        }


@pytest.mark.parametrize("clear_overload", [True, False])
@pytest.mark.parametrize("hold_kind", ["overload", "usage_limit", "background"])
def test_clearing_wait_only_removes_the_matching_reason(clear_overload: bool, hold_kind: str) -> None:
    """一方の解除処理が別理由の保留結果を失わせず、対応する履歴だけを初期化する。"""
    result = {"status": "failed", "agent_message": "待機", "error": None}
    holds: dict[str, turn_state.ResultHold] = {
        "overload": turn_state.OverloadWait(result, 10.0),
        "usage_limit": turn_state.UsageLimitWait(result, 20.0),
        "background": turn_state.BackgroundWait(result, None),
    }
    hold = holds[hold_kind]
    session = state.SessionState("parent", "/work", hold=hold, overload_resume_count=2, usage_limit_wait_count=3)

    if clear_overload:
        resume_waits.clear_overload_resume(session)
    else:
        resume_waits.clear_usage_limit_wait(session)

    expected_clear = hold_kind == ("overload" if clear_overload else "usage_limit")
    assert session.hold is (None if expected_clear else hold)
    assert session.overload_resume_count == (0 if clear_overload else 2)
    assert session.usage_limit_wait_count == (3 if clear_overload else 0)
