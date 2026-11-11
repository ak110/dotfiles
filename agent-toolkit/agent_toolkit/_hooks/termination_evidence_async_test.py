# pylint: disable=unused-import,useless-import-alias  # pytestのautouse fixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""termination_evidence_asyncの契約テスト。"""

import argparse
import contextlib
import io
import json
import pathlib

import pytest

from agent_toolkit._agents_server import shared_layout
from agent_toolkit._atk import run_script
from agent_toolkit._common import session_state, state_paths
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._hooks import agents_server_observations, posttooluse, termination_evidence, termination_order_advisor
from agent_toolkit._testing.termination_evidence_support import isolated_session as isolated_session
from agent_toolkit._testing.termination_evidence_support import public_decision, stop_payload, supply_commit

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


def test_public_wait_decision_uses_cli_lock_after_observation_attempt(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """委譲開始とPostToolUseの観測更新を経て、CLIの待機所有権で公開wait判断を受理する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    supply_commit(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    start = {
        "session_id": "evidence-test",
        "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
        "tool_use_id": "start-test",
        "tool_input": {"prompt": "独立した調査"},
        "tool_response": {"structuredContent": {"session_id": "child-test", "status": "running"}},
    }
    termination_evidence.observe_tool(json.dumps(start), after=False)
    with contextlib.redirect_stdout(io.StringIO()):
        assert posttooluse.main(json.dumps(start)) == 0
    wait = {
        "session_id": "evidence-test",
        "tool_name": "Bash",
        "tool_use_id": "wait-test",
        "tool_input": {"command": "atk agents wait", "run_in_background": True},
        "tool_response": {"stdout": "継続中の待機", "stderr": "", "exit_code": 0},
    }
    with contextlib.redirect_stdout(io.StringIO()):
        assert posttooluse.main(json.dumps(wait)) == 0
    child = session_state.read_state("evidence-test")["agents_server_sessions"]["child-test"]
    assert child["pending_observation"] is False
    root = shared_layout.status_directory("evidence-test", tmp_path / "state")
    lock_path = root / "wait-locks" / "root.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    targets = shared_layout.wait_targets_directory("evidence-test", "root.json", tmp_path / "state")
    targets.mkdir(parents=True, exist_ok=True)
    (targets / "child-test.json").write_text('{"version":1,"session_id":"child-test"}', encoding="utf-8")
    document = tmp_path / "wait-decision.json"
    document.write_text(
        json.dumps(
            {
                "session_id": "evidence-test",
                "action": "wait",
                "work_id": "work-1",
                "target_session_id": "child-test",
                "reason": "調査結果を待つ",
            }
        ),
        encoding="utf-8",
    )
    with lock_path.open("a+b") as stream:
        acquire_lock(stream, blocking=False)
        try:
            assert agents_server_observations.actively_waited_session_ids(["child-test"]) == {"child-test"}
            with contextlib.redirect_stdout(io.StringIO()):
                assert (
                    run_script.dispatch(
                        argparse.Namespace(script_name="termination-evidence", script_args=["--decision-file", str(document)])
                    )
                    == 0
                )
            assert not termination_evidence.pending_work(json.loads(stop_payload(tmp_path, "待機中")))
        finally:
            release_lock(stream)
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "結果を受け取った。"))
    assert decision == "notify" and "report-completion" in reason


@pytest.mark.parametrize("kind", ["unrelated", "other-owner", "finished"])
def test_explicit_child_wait_rejection_is_not_uwi_guidance(tmp_path: pathlib.Path, kind: str) -> None:
    """作業不一致・所有者違い・待機解消を対象ごとの理由で拒否する。"""
    supply_commit(tmp_path)

    def register(state: dict) -> dict:
        work = state[termination_evidence.STATE_KEY]["works"]["work-1"]
        if kind != "unrelated":
            work["async_targets"]["child-x"] = "call-x"
        state["agents_server_sessions"] = {
            "child-x": {"owner_agent_id": "another" if kind == "other-owner" else "main", "pending_observation": False}
        }
        return state

    session_state.update_state("evidence-test", register)
    with pytest.raises(ValueError) as error:
        termination_evidence.record_decision(
            {
                "session_id": "evidence-test",
                "action": "wait",
                "work_id": "work-1",
                "target_session_id": "child-x",
                "reason": "対象の結果を待つ",
            }
        )
    assert "child-x" in str(error.value) and "UWI" not in str(error.value)
    if kind == "finished":
        status, output = public_decision(
            tmp_path,
            {
                "session_id": "evidence-test",
                "action": "wait",
                "work_id": "work-1",
                "target_session_id": "child-x",
                "reason": "対象の結果を待つ",
            },
        )
        assert status == 2
        assert "child-x" in output
        assert output.count("atk agents wait") == 1
        assert "次の操作:" in output
