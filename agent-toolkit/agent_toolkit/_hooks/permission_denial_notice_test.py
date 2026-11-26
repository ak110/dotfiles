import json
import pathlib

import pytest

from agent_toolkit._hooks.user_prompt_submit_test import _run_subcommand

_SOURCE = "permission_denial_notice"
_SEND = "mcp__plugin_agent-toolkit_agents_server__send_to_user"


def _hook(command: str, root: pathlib.Path, **payload: object) -> dict:
    result = _run_subcommand(command, {"session_id": "denial-fixture", "cwd": str(root), **payload}, state_dir=root)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout else {}


def _denial(root: pathlib.Path, **payload: object) -> None:
    assert not _hook(
        "posttooluse",
        root,
        hook_event_name="PermissionDenied",
        tool_name="REPL",
        tool_input={"code": "console.log('test')"},
        **payload,
    )


@pytest.mark.parametrize("tool_name", ["AskUserQuestion", _SEND])
def test_denied_repl_provides_approval_guidance_before_next_user_facing_call(tmp_path: pathlib.Path, tool_name: str) -> None:
    _denial(tmp_path, tool_use_id="repl_denied")
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": {}}
    output = _hook("pretooluse", tmp_path, **payload)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    guidance = output["permissionDecisionReason"]
    assert "permission-denial.md" in guidance and "共通判断" in guidance and "具体的な承認" in guidance
    assert "同じ操作を再試行せず" in guidance and "拒否の事実と偽陽性の根拠" in guidance
    assert "偽陽性として実行を承認する" in guidance
    assert "該当の操作を撤回する" in guidance
    assert "サブエージェント側でレビューを終えてから判断する" in guidance
    assert _SOURCE not in str(_hook("pretooluse", tmp_path, **payload))
    assert _SOURCE not in str(_hook("stop", tmp_path, hook_event_name="Stop"))


def test_denial_warning_then_missing_response_blocks_question_once(tmp_path: pathlib.Path) -> None:
    _denial(tmp_path)
    bash = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "pwd"}}
    warning = _hook("pretooluse", tmp_path, **bash)["hookSpecificOutput"]
    assert warning.get("permissionDecision") != "deny"
    assert f'source="{_SOURCE}" kind="warn"' in warning["additionalContext"]
    assert _SOURCE not in str(_hook("pretooluse", tmp_path, **bash))
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "attachment",
                "uuid": "first-warning",
                "attachment": {
                    "type": "hook_additional_context",
                    "content": [warning["additionalContext"]],
                    "hookEvent": "PreToolUse",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    question = {
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": {},
        "transcript_path": str(transcript),
    }
    blocked = _hook("pretooluse", tmp_path, **question)["hookSpecificOutput"]
    assert blocked["permissionDecision"] == "deny"
    assert "hook_notice_response_gate" in blocked["permissionDecisionReason"]
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n"
            + json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "hook通知への対応: 委譲元へ報告する。"}]},
                },
                ensure_ascii=False,
            )
        )
    assert "hook_notice_response_gate" not in str(_hook("pretooluse", tmp_path, **question))


@pytest.mark.parametrize("delegated", [False, True])
def test_stop_handles_denial_once_and_ignores_ordinary_output(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, delegated: bool
) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1" if delegated else "0")
    _hook(
        "posttooluse",
        tmp_path,
        hook_event_name="PostToolUse",
        tool_name="Bash",
        tool_input={"command": "pwd"},
        tool_response="Destructive or Unwanted Actions",
    )
    assert _SOURCE not in str(_hook("stop", tmp_path, hook_event_name="Stop"))
    _denial(tmp_path)
    stopped = _hook("stop", tmp_path, hook_event_name="Stop")
    assert stopped["decision"] == "block" and _SOURCE in stopped["reason"]
    if delegated:
        assert "委譲元へ報告してください" in stopped["reason"] and "AskUserQuestion" not in stopped["reason"]
    else:
        assert "AskUserQuestion" in stopped["reason"]
    assert _SOURCE not in str(_hook("stop", tmp_path, hook_event_name="Stop", stop_hook_active=True))


def test_subagent_denial_is_not_consumed_by_main(tmp_path: pathlib.Path) -> None:
    _denial(tmp_path, agent_id="worker")
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {}}
    assert _SOURCE not in str(_hook("pretooluse", tmp_path, **payload))
    warning = _hook("pretooluse", tmp_path, agent_id="worker", **payload)["hookSpecificOutput"]["additionalContext"]
    assert "委譲元へ報告してください" in warning and "AskUserQuestion" not in warning
