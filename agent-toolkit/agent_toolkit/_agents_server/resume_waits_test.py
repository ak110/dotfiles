"""`_agents_server/resume_waits.py`の振る舞いを検証する。"""

from __future__ import annotations

import pytest

from agent_toolkit._agents_server import resume_waits, state


@pytest.mark.parametrize("keep_resume_chain", [False, True])
def test_finalize_pending_result_records_remaining_wait_targets_and_keeps_error(keep_resume_chain: bool) -> None:
    """保留結果の確定は残ったバックグラウンドタスクと孫sessionを既存の`error`へ併合し、継続の連鎖では記録しない。

    全ての確定の契機（期限到来、ストリーム終端、`kill`、`send_message`、孫sessionの監視）がこの共通処理を通る。
    既存の`error`を置き換えると、失敗の内容や先に記録した未観測の孫sessionが失われる。
    継続の連鎖（過負荷と利用上限の待機後の継続）は同じsessionで新しいturnを始めるため、確定した結果を公開しない。
    """
    session = state.SessionState("parent-1", "/tmp")
    session.pending_result = {
        "status": "completed",
        "agent_message": "待機中: task-1",
        "error": {"message": "既存の失敗", "unobservedSessions": ["child-0"]},
    }
    session.awaiting_auto_resume = True
    session.live_tasks["task-1"] = state.LiveTask("local_bash", "CIの完了待ち", "2026-10-06T05:59:06Z")
    session.live_child_session_ids.add("child-1")

    resume_waits.finalize_pending_result(
        session, touch=False, keep_resume_chain=keep_resume_chain, unobserved_sessions={"child-2"}
    )

    if keep_resume_chain:
        assert session.error == {"message": "既存の失敗", "unobservedSessions": ["child-0"]}
    else:
        assert session.error == {
            "message": "既存の失敗",
            "unobservedSessions": ["child-0", "child-1", "child-2"],
            "unfinishedBackgroundTasks": ["task-1"],
            "heldResultFinalized": True,
        }
