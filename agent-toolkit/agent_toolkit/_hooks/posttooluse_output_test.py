# pylint: disable=protected-access
"""posttooluse_outputの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import posttooluse
from agent_toolkit._hooks.pretooluse import dispatch
from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.hook_output_contract import validate_hook_output
from agent_toolkit._testing.posttooluse_support import (
    _assert_tool_specific_posttooluse_registration,
    _registered_posttooluse_names,
    _run,
    _shell_payload,
)


def test_hooks_json_routes_tool_specific_processing_to_posttooluse() -> None:
    """ツール別処理を持つ現行Claude入力群が登録から到達する。"""
    _assert_tool_specific_posttooluse_registration(_registered_posttooluse_names())


def test_posttooluse_registration_rejects_missing_webfetch() -> None:
    with pytest.raises(AssertionError):
        _assert_tool_specific_posttooluse_registration(_registered_posttooluse_names() - {"WebFetch", "*"})


@pytest.mark.parametrize("tool_name", ["Bash", "PowerShell"])
def test_persisted_shell_output_excerpt_is_replaced_by_unread_notice(tmp_path: pathlib.Path, tool_name: str) -> None:
    excerpt = "元の出力の先頭" * 100
    saved = str(tmp_path / "tool-results" / "b1.txt")
    payload = _shell_payload(
        tool_name, {"stdout": excerpt, "stderr": "警告", "persistedOutputPath": saved, "persistedOutputSize": 50000}
    )

    result = _run(payload, state_dir=tmp_path)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert validate_hook_output("PostToolUse", json.loads(result.stdout)) == []
    updated = output["updatedToolOutput"]
    assert excerpt not in updated["stdout"]
    assert saved in updated["stdout"] and "50000" in updated["stdout"]
    assert "全量取得" in updated["stdout"]
    assert "allow_large" not in updated["stdout"] and "file_path" not in updated["stdout"]
    assert {key: value for key, value in updated.items() if key != "stdout"} == {
        key: value for key, value in payload["tool_response"].items() if key != "stdout"
    }


@pytest.mark.parametrize("complete", [False, True])
def test_report_cannot_use_unread_persisted_output_as_evidence(tmp_path, capsys, complete):
    session = f"output-evidence-{complete}"
    source = tmp_path / "saved.txt"
    body = "先頭\n残り\n"
    source.write_text(body, encoding="utf-8")
    payload = {
        "session_id": session,
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "printf trial"},
        "tool_response": {"persistedOutputPath": str(source)},
    }
    assert posttooluse.main(json.dumps(payload)) == 0
    capsys.readouterr()
    send = {"session_id": session, "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_to_user"}

    def report(message: str) -> str:
        assert dispatch.main(json.dumps({**send, "tool_input": {"message": message}})) == 0
        return capsys.readouterr().out

    assert "未取得の退避出力" not in report(f"根拠: `{source}`は未確認。")
    assert "未取得の退避出力" in report(f"完了: `{source}`で対象がなかった。")
    read = {
        **payload,
        "tool_name": "Read",
        "tool_input": {"file_path": str(source)},
        "tool_response": {"file": {"content": body if complete else "先頭"}},
    }
    assert posttooluse.main(json.dumps(read)) == 0
    capsys.readouterr()
    assert ("未取得の退避出力" not in report(f"完了: `{source}`で対象がなかった（再確認）。")) is complete


def test_unread_evidence_block_passes_once_on_resend(tmp_path, capsys):
    source = tmp_path / "saved.txt"
    source.write_text("先頭\n残り\n", encoding="utf-8")
    payload = {
        "session_id": "output-evidence-resend",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "printf trial"},
        "tool_response": {"persistedOutputPath": str(source)},
    }
    assert posttooluse.main(json.dumps(payload)) == 0
    capsys.readouterr()
    send = {
        "session_id": "output-evidence-resend",
        "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_to_user",
        "tool_input": {"message": f"完了: `{source}`で対象がなかった。"},
    }
    outputs = []
    for _ in range(3):
        assert dispatch.main(json.dumps(send)) == 0
        outputs.append(capsys.readouterr().out)
    assert ["未取得の退避出力" in output for output in outputs] == [True, False, True]


def test_stacked_send_blocks_pass_together_on_one_resend(tmp_path, capsys):
    from agent_toolkit._hooks import termination_evidence  # pylint: disable=import-outside-toplevel

    trace = tmp_path / "trace.jsonl"
    trace.write_text("", encoding="utf-8")
    source = tmp_path / "saved.txt"
    source.write_text("先頭\n残り\n", encoding="utf-8")
    base = {"session_id": "stacked-resend", "transcript_path": str(trace), "cwd": str(tmp_path)}
    start = {
        **base,
        "tool_name": "Skill",
        "tool_use_id": "start",
        "tool_input": {"skill": "agent-toolkit:atk-mode", "args": "process-wi"},
    }
    termination_evidence.observe_tool(json.dumps(start), after=False)
    termination_evidence.observe_tool(json.dumps({**start, "tool_response": "起動"}), after=True)
    shell = {
        **base,
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "printf trial"},
        "tool_response": {"persistedOutputPath": str(source)},
    }
    assert posttooluse.main(json.dumps(shell)) == 0
    capsys.readouterr()
    send = {
        **base,
        "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_to_user",
        "tool_input": {"message": f"## 作業完了報告\n完了: `{source}`で対象がなかった。"},
    }
    outputs = []
    for _ in range(2):
        assert dispatch.main(json.dumps(send)) == 0
        outputs.append(capsys.readouterr().out)
    assert "未取得の退避出力" in outputs[0] and "作業完了報告の手順書" in outputs[0]
    second = json.loads(outputs[1]) if outputs[1].strip() else {}
    assert second.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


@pytest.mark.parametrize("envelope", ["ending-row", "child-body"])
def test_review_update_requires_documents_from_wait_envelope(tmp_path, capsys, envelope):
    root = tmp_path / "agent-basics"
    skill = root / "skills" / "review-standards"
    (skill / "references").mkdir(parents=True)
    (root / "plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    subject = skill / "references" / "reviewee.md"
    subject.write_text("レビューの本文\n", encoding="utf-8")
    required = ["agent-basics:review-standards/SKILL.md", "agent-basics:review-standards/references/reviewee.md"]
    end = f"終了行: session_id=proof label=test-review status=completed read_requirements={json.dumps(required)}"
    if envelope == "child-body":
        end = f"本文開始: session_id=proof\n{end}\n本文終了: session_id=proof"
    base = {"session_id": f"wait-review-{envelope}", "cwd": str(tmp_path)}

    def observe(name, inputs, result):
        payload = {**base, "hook_event_name": "PostToolUse", "tool_name": name, "tool_input": inputs, "tool_response": result}
        assert posttooluse.main(json.dumps(payload)) == 0
        capsys.readouterr()

    def update():
        assert (
            dispatch.main(
                json.dumps(
                    {
                        **base,
                        "tool_name": "Bash",
                        "tool_input": {"command": "atk review-table respond table.tsv --row-id 1"},
                    }
                )
            )
            == 0
        )
        return capsys.readouterr().out

    observe("Bash", {"command": "atk agents wait"}, {"stdout": end})
    assert ("レビュー返却が指定した資料の取得を確認できない" in update()) is (envelope == "ending-row")
    observe("Skill", {"skill": "agent-basics:review-standards"}, "起動")
    observe("Read", {"file_path": str(subject)}, {"file": {"content": "レビューの本文\n"}})
    assert "レビュー返却が指定した資料の取得を確認できない" not in update()


@pytest.mark.parametrize(
    ("tool_name", "response", "event"),
    [
        pytest.param("Bash", {"stdout": "短い出力"}, "PostToolUse", id="not-persisted"),
        pytest.param("Bash", {"stdout": "x", "persistedOutputPath": ""}, "PostToolUse", id="empty-path"),
        pytest.param("Read", {"stdout": "x", "persistedOutputPath": "/tmp/x.txt"}, "PostToolUse", id="other-tool"),
        pytest.param("Bash", {"stdout": "x", "persistedOutputPath": "/tmp/x.txt"}, "PostToolUseFailure", id="failure"),
    ],
)
def test_shell_output_without_persisted_path_is_left_unchanged(
    tmp_path: pathlib.Path, tool_name: str, response: dict, event: str
) -> None:
    """退避していない出力、他のツールおよび失敗の結果では、ツール結果を置き換えない。"""
    payload = {**_shell_payload(tool_name, response), "hook_event_name": event}

    result = _run(payload, state_dir=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "updatedToolOutput" not in result.stdout


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure", "PermissionDenied"])
@pytest.mark.parametrize("id_field", ["task_id", "shell_id"])
def test_registered_task_stop_consumes_only_successful_target(tmp_path: pathlib.Path, event: str, id_field: str) -> None:
    """登録の選択から停止対象の消費までを検証し、失敗・拒否と別対象を保持する。"""
    session_id = "task-stop-consume"
    assert session_state.update_state(
        session_id,
        lambda _previous: {"stall_detection_completed_at_by_task": {"task-1": 1.0, "task-2": 2.0}},
    )
    registered = _registered_posttooluse_names(event)
    if "*" in registered or "TaskStop" in registered:
        result = _run(
            {"hook_event_name": event, "session_id": session_id, "tool_name": "TaskStop", "tool_input": {id_field: "task-1"}},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
    expected = {"task-2": 2.0} if event == "PostToolUse" else {"task-1": 1.0, "task-2": 2.0}
    assert _read_state(tmp_path, session_id)["stall_detection_completed_at_by_task"] == expected


@pytest.mark.parametrize("tool_name", ["Agent", "Task"])
def test_agent_async_launch_records_agent_id(tmp_path: pathlib.Path, tool_name: str) -> None:
    session_id = f"agent-async-{tool_name.lower()}"
    result = _run(
        {
            "session_id": session_id,
            "tool_name": tool_name,
            "tool_input": {"subagent_type": "general-purpose", "prompt": "調査する", "run_in_background": True},
            "tool_response": {"isAsync": True, "status": "async_launched", "agentId": "a8d4542d5607ca48d"},
        },
        state_dir=tmp_path,
    )
    assert result.returncode == 0
    assert _read_state(tmp_path, session_id).get("background_task_ids") == ["a8d4542d5607ca48d"]


def test_agent_foreground_completion_records_nothing(tmp_path: pathlib.Path) -> None:
    session_id = "agent-foreground"
    result = _run(
        {
            "session_id": session_id,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "general-purpose", "prompt": "調査する"},
            "tool_response": {"status": "completed", "agentId": "a8d4542d5607ca48d", "content": []},
        },
        state_dir=tmp_path,
    )
    assert result.returncode == 0
    assert "background_task_ids" not in _read_state(tmp_path, session_id)


@pytest.mark.parametrize("agent_id", [None, ""], ids=["missing", "empty"])
def test_agent_launch_without_agent_id_records_nothing(tmp_path: pathlib.Path, agent_id: str | None) -> None:
    session_id = f"agent-no-id-{agent_id is None}"
    response: dict[str, object] = {"isAsync": True, "status": "async_launched"}
    if agent_id is not None:
        response["agentId"] = agent_id
    result = _run(
        {
            "session_id": session_id,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "general-purpose", "prompt": "調査する"},
            "tool_response": response,
        },
        state_dir=tmp_path,
    )
    assert result.returncode == 0
    assert "background_task_ids" not in _read_state(tmp_path, session_id)


def test_record_failure_preserves_tool_result_recovery(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise OSError("結果の記録に失敗")

    monkeypatch.setattr(posttooluse.termination_evidence, "observe_tool", fail)
    assert (
        posttooluse.main(
            json.dumps(
                {"session_id": "failure-evidence", "tool_name": "Read", "tool_input": {}, "tool_response": "取得した本文"}
            )
        )
        == 0
    )
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "結果の記録に失敗" in context
    assert "ツール結果" in context and "委譲元またはユーザー" in context
