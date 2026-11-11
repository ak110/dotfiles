# pylint: disable=unused-import,useless-import-alias  # pytestのautouse fixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""termination_evidence_omissionの契約テスト。"""

import argparse
import contextlib
import io
import json
import os
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import agents_wait, manager, mcp_tools, status_file
from agent_toolkit._atk import run_script
from agent_toolkit._common import atomic_file, session_state, state_paths
from agent_toolkit._hooks import agents_server_observations, termination_evidence, termination_order_advisor
from agent_toolkit._testing import agents_server_support, git_repository
from agent_toolkit._testing.termination_evidence_support import (
    _RegisteredBackend,
    _tool,
    _WindowsAccessDenied,
    human_input,
    stop_payload,
    supply_commit,
)
from agent_toolkit._testing.termination_evidence_support import isolated_session as isolated_session

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


@pytest.mark.parametrize(
    "invalid",
    [
        "quote",
        "policy-outside",
        "target",
        "target-other",
        "another-repo",
        "work",
        "session",
        "agent",
        "runtime-agent",
        "input",
        "old-input",
        "generated",
    ],
)
def test_omission_cli_rejects_unrelated_evidence(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, invalid: str) -> None:
    """省略の引用・対象・会話・担当・入力が実体と異なる場合は、CLIから不足を免除できない。"""
    repository = git_repository.init_repository(tmp_path / "repo", files={"CLAUDE.md": "完了報告は指示まで省く。\n"})
    trace = tmp_path / "transcript.jsonl"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    human_input(tmp_path, "request", "成果を作成する")
    termination_evidence.observe_tool(
        json.dumps(
            {
                "session_id": "evidence-test",
                "cwd": str(repository),
                "tool_name": "Edit",
                "tool_use_id": "edit",
                "tool_input": {"file_path": str(repository / "CLAUDE.md")},
                "tool_response": {"exit_code": 0},
            }
        ),
        after=True,
    )
    document = {
        "session_id": "evidence-test",
        "action": "omit-completion",
        "work_id": "work-1",
        "input_id": "request",
        "reason": "適用規範に従う",
        "policy_file": str(repository / "CLAUDE.md"),
        "quote": "完了報告は指示まで省く。\n",
        "target_repo": str(repository),
    }
    if invalid == "quote":
        document["quote"] = "異なる本文"
    elif invalid == "policy-outside":
        policy = tmp_path / "CLAUDE.md"
        policy.write_text(document["quote"], encoding="utf-8")
        document["policy_file"] = str(policy)
    elif invalid == "target":
        document["target_repo"] = str(tmp_path / "missing")
    elif invalid == "target-other":
        other = git_repository.init_repository(tmp_path / "other", files={"CLAUDE.md": document["quote"]})
        document.update(target_repo=str(other), policy_file=str(other / "CLAUDE.md"))
    elif invalid == "another-repo":
        other = git_repository.init_repository(tmp_path / "other", files={"README.md": "別の成果\n"})
        termination_evidence.observe_tool(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "cwd": str(repository),
                    "tool_name": "Bash",
                    "tool_use_id": "other-commit",
                    "tool_input": {"command": f"git -C {other} commit -m 変更"},
                    "tool_response": {"exit_code": 0},
                }
            ),
            after=True,
        )
    elif invalid == "work":
        document["work_id"] = "work-missing"
    elif invalid == "session":
        document["session_id"] = "another-session"
    elif invalid == "agent":
        document["agent_id"] = "child"
    elif invalid == "runtime-agent":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    elif invalid == "old-input":
        human_input(tmp_path, "new-request", "別の依頼")
    elif invalid == "generated":
        termination_evidence.observe_user(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "prompt_id": "generated",
                    "prompt": '<atk-auto source="stop" kind="notice">継続</atk-auto>',
                }
            )
        )
        document["input_id"] = "generated"
    else:
        document["input_id"] = "missing-input"
    source = tmp_path / "omission.json"
    source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert (
            run_script.dispatch(
                argparse.Namespace(script_name="termination-evidence", script_args=["--decision-file", str(source)])
            )
            == 2
        )
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "成果を届けた。"))[0] == "notify"
    assert trace.is_file()


@pytest.mark.asyncio
async def test_started_session_without_projection_is_waited_by_same_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """表示保存に失敗した公開startのIDを同じ作業の待機対象とし、wait判断の受理から終了本文の回収まで到達する。"""
    state_root = tmp_path / "state"
    monkeypatch.setattr(state_paths, "state_dir", lambda: state_root)
    supply_commit(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    writer = status_file.StatusFileWriter(
        {},
        status_file.StatusFileIdentity("evidence-test", "root.json", None),
        state_root=state_root,
        aggregate_seconds=0,
    )
    async with manager.AgentsServerManager(writer) as current:
        agents_server_support.install_backend(current, "claude", _RegisteredBackend(current.sessions, "claude"))
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        writer.activate()
        writer.path.unlink(missing_ok=True)
        original_replace = os.replace

        def refuse_projection(source: Any, target: Any) -> None:
            if pathlib.Path(target) != writer.path:
                original_replace(source, target)
                return
            error = _WindowsAccessDenied(13, "アクセスが拒否されました", str(source), None, str(target))
            raise error

        monkeypatch.setattr(atomic_file.os, "replace", refuse_projection)
        start_input = {"mode": "delegate", "prompt": "調査する", "cwd": str(tmp_path), "model_type": "claude:test/medium"}
        try:
            started = await mcp_tools.start(
                mode="delegate", prompt=start_input["prompt"], cwd=str(tmp_path), model_type="claude:test/medium"
            )
            child = started["session_id"]
            assert started["persistence_warnings"] and not writer.path.exists()
            _tool(
                tmp_path,
                "start-call",
                "mcp__plugin_agent-toolkit_agents_server__start",
                start_input,
                {"structuredContent": started},
                codex=False,
            )
            work = session_state.read_state("evidence-test")[termination_evidence.STATE_KEY]["works"]["work-1"]
            assert work["async_targets"] == {child: "start-call"}

            termination_evidence.record_decision(
                {
                    "session_id": "evidence-test",
                    "action": "wait",
                    "work_id": "work-1",
                    "target_session_id": child,
                    "reason": "委譲結果を待つ",
                }
            )
            assert termination_order_advisor.evaluate(stop_payload(tmp_path, "待機中"))[0] == "approve"

            agents_server_support._complete(current.sessions[child], message="委譲先の報告")  # pylint: disable=protected-access
            with pytest.raises(status_file.ProjectionWriteError):
                writer.flush()
            capsys.readouterr()
            code = agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "evidence-test"}, state_root=state_root)
            rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
            assert code == 0
            assert [(row["session_id"], row["agent_message"]) for row in rows] == [(child, "委譲先の報告")]
            _tool(
                tmp_path,
                "wait-call",
                "Bash",
                {"command": "atk agents wait"},
                {"stdout": "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), "stderr": "", "exit_code": 0},
                codex=False,
            )
            assert not agents_server_observations.pending_session_ids(session_state.read_state("evidence-test"), "main")
            decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "結果を受け取った。"))
            assert decision == "notify" and "report-completion" in reason
        finally:
            monkeypatch.setattr(atomic_file.os, "replace", original_replace)
