# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""dispatch_warningsの契約テスト。"""

import json
import pathlib
import textwrap

import pytest

from agent_toolkit import hook
from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._testing.dispatch_support import _existing_work_contract as _existing_work_contract
from agent_toolkit._testing.pretooluse_support import (
    _additional_context,
    _plan_file_state_env,
    _run,
    _stderr_warn_offenders,
    _write_session_state,
)


@pytest.mark.parametrize("pending", [False, True])
def test_pending_background_output_read_distinguishes_progress_from_result(monkeypatch, tmp_path, pending):
    output = tmp_path / "background-output.log"
    output.write_text("途中の出力", encoding="utf-8")
    _write_session_state(tmp_path, "pending-output-read", {"background_task_ids": ["task-1"]})
    monkeypatch.setattr(
        pretooluse._background_task_outputs,
        "pending_task_output_paths",
        lambda *_args: {str(output)} if pending else set(),
    )
    result = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": f"cat -- {output}"},
            "session_id": "pending-output-read",
            "transcript_path": str(tmp_path / "transcript.jsonl"),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0 and not result.stderr
    context = _additional_context(result)
    assert ("途中経過は進捗の材料に限ってください" in context) is pending
    assert ("同じ出力ファイルを全量読み直して" in context) is pending


@pytest.mark.parametrize("module_name", sorted(hook._SUBCOMMANDS))  # noqa: SLF001  # pylint: disable=protected-access
def test_warn_notices_are_not_written_to_stderr(module_name: str) -> None:
    package_dir = pathlib.Path(pretooluse.__file__).parent
    if module_name == "pretooluse":
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(package_dir.glob("*.py")) if not path.name.endswith("_test.py")
        )
    else:
        source = (package_dir.parent / f"{module_name}.py").read_text(encoding="utf-8")
    offenders = _stderr_warn_offenders(source)
    if module_name == "pretooluse":
        assert offenders == []
    else:
        assert offenders == [], module_name


def test_stderr_warn_offenders_detects_indirect_binding() -> None:
    source = textwrap.dedent(
        """\
        import sys


        def _llm_notice(text, tag):
            return f"{tag}: {text}"


        def _compose_notice():
            return _warn_remote_change()


        def _warn_remote_change():
            return _llm_notice("body", tag="warn")


        def main():
            notice = _compose_notice()
            print(notice, file=sys.stderr)
            print("plain", file=sys.stderr)
        """
    )
    expected_lineno = source.splitlines().index("    print(notice, file=sys.stderr)") + 1
    assert _stderr_warn_offenders(source) == [expected_lineno]


def test_second_removable_warning_is_suppressed(tmp_path: pathlib.Path) -> None:
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "uv.lock"), "content": "version = 1\n"},
        "session_id": "dispatch-repeated-removable-warning",
    }
    environment = _plan_file_state_env(tmp_path)
    _write_session_state(
        tmp_path,
        "dispatch-repeated-removable-warning",
        {},
    )

    first = _run(payload, env_overrides=environment)
    second = _run(payload, env_overrides=environment)

    assert first.returncode == 0
    assert second.returncode == 0
    assert "uv add" in _additional_context(first)
    assert _additional_context(second) == ""
    assert second.stderr == ""


def test_irremovable_warning_does_not_advance_removable_repeat_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    def emit_controlled_warning(_tool_name: str, tool_input: dict, _cwd: str, _payload: dict) -> Decision:
        notice = pretooluse._llm_notice(  # noqa: SLF001  # pylint: disable=protected-access
            "controlled warning",
            tag=pretooluse._WARN_TAG,  # noqa: SLF001  # pylint: disable=protected-access
            fix="retry",
            removable_cause=bool(tool_input["removable"]),
        )
        return Decision(context=notice)

    monkeypatch.setattr(pretooluse, "_decide_edit_tool", emit_controlled_warning)

    def run(removable: bool) -> int:
        return pretooluse.main(
            json.dumps(
                {
                    "tool_name": "SyntheticEdit",
                    "tool_input": {"removable": removable},
                    "session_id": "dispatch-mixed-removability",
                }
            )
        )

    irremovable = run(False)
    first_removable = run(True)
    second_removable = run(True)

    captured = capsys.readouterr()
    assert irremovable == 0
    assert first_removable == 0
    assert second_removable == 0
    assert "この通知は同一セッションで2件目である" not in captured.out
    assert captured.out.count("controlled warning") == 1
    assert captured.err == ""


def test_masked_clock_warning_is_left_to_successful_display():
    tool = "mcp__plugin_agent-toolkit_agents_server__send_to_user"
    args = {"message": "時刻の表記例は15:2x。"}
    decision = pretooluse._decide({"session_id": "clock-display"}, tool, args, "clock-display", is_codex=False)
    assert decision.allow
    assert not decision.context
    assert not decision.notices


@pytest.mark.parametrize("input_kind", ["message", "body_ref"])
def test_send_advises_against_skipping_prevention_without_changing_input(monkeypatch, input_kind):
    tool = "mcp__plugin_agent-toolkit_agents_server__send_to_user"
    body = "既存の条文で足りるため、再発防止のWIは作らない。"
    args = {input_kind: body if input_kind == "message" else "fixture-body-ref"}
    monkeypatch.setattr(pretooluse, "load_body_reference", lambda *_args: body)
    payload = {"session_id": "send-prevention"}
    result = pretooluse._decide(payload, tool, args, "send-prevention", is_codex=False)
    assert result.allow and result.block is None
    assert '<atk-auto source="norm_excuse_gate" kind="warn">' in (result.context or "")
    assert result.updated_input == ({"message": body} if input_kind == "body_ref" else None)


def test_send_rejection_with_body_and_reference_does_not_emit_prevention_warning():
    tool = "mcp__plugin_agent-toolkit_agents_server__send_to_user"
    payload = {"session_id": "send-prevention"}
    args = {"message": "既存の条文で足りる。", "body_ref": "fixture-body-ref"}
    result = pretooluse._decide(payload, tool, args, "send-prevention", is_codex=False)
    assert result.block and not result.context


@pytest.mark.parametrize("tool", ["AskUserQuestion", "ExitPlanMode"])
@pytest.mark.parametrize("quoted", [False, True])
def test_user_facing_tools_warn_without_denying_or_changing_input(tool: str, quoted: bool) -> None:
    phrase = "既存の条文で足りる"
    text = f"「{phrase}」では再発防止にならない。" if quoted else f"{phrase}ため、再発防止のWIは作らない。"
    args = {"questions": [{"question": text}]} if tool == "AskUserQuestion" else {"plan": text}
    result = pretooluse._decide({}, tool, args, "", is_codex=False)
    assert result.block is None and result.updated_input is None
    assert ('<atk-auto source="norm_excuse_gate" kind="warn">' in (result.context or "")) is not quoted


@pytest.mark.parametrize("tool", ["AskUserQuestion", "ExitPlanMode", "mcp__plugin_agent-toolkit_agents_server__send_to_user"])
def test_user_facing_warning_distinguishes_independent_calls_from_retry(tmp_path, tool: str) -> None:
    def deliver(text: str, call: str) -> bool:
        match tool:
            case "AskUserQuestion":
                inputs = {"questions": [{"question": text}]}
            case "ExitPlanMode":
                inputs = {"plan": text}
            case _:
                inputs = {"message": text}
        payload = {
            "session_id": "independent-prevention-requests",
            "tool_name": tool,
            "tool_input": inputs,
            "tool_use_id": call,
        }
        result = _run(payload, env_overrides=_plan_file_state_env(tmp_path))
        assert result.returncode == 0 and not result.stderr
        output = json.loads(result.stdout or "{}").get("hookSpecificOutput", {})
        assert output.get("permissionDecision") != "deny" and not output.get("updatedInput")
        return '<atk-auto source="norm_excuse_gate" kind="warn">' in _additional_context(result)

    text = "既存の条文で足りるため対策しない。"
    assert deliver(text, "call-a")
    assert not deliver(text, "call-a")
    assert deliver(text, "call-b")
    assert not deliver("今回は実際に通る試行を記録しました。", "call-c")
    assert deliver(text, "call-d")
