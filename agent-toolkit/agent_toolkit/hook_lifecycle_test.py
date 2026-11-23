"""hook_lifecycleの契約テスト。"""

import json
import os
import pathlib
import tempfile
from typing import Any, cast

import pytest

from agent_toolkit import hook as _hook
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import session_activity, session_registry, state_paths
from agent_toolkit._common.persisted_output_reference import CODEX_NOTICE_REFERENCE_PREFIX
from agent_toolkit._testing.hook_support import _call_hook_in_process


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.cpu_limit(
    4.25,
    reason=(
        "公開Pre/Stopを反復し、2sessionと両hostで状態分離・案内の1回の配送・Stop3回を確認する（Linux CI CPU合計最大1.987秒）。"
    ),
)
def test_repeated_calls_in_one_process_follow_input_and_session_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, codex: bool
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    empty_transcript = tmp_path / "empty.jsonl"
    empty_transcript.write_text("", encoding="utf-8")
    for session in ("repeat-session", "other-session"):
        directory = managed_temp.resolve_session_temp(session)
        (directory / "work-contract.md").write_text("目的: 操作反復の検証\n", encoding="utf-8")

    def pretooluse(session_id: str, command: str, expected_code: int = 0) -> str:
        return _call_hook_in_process(
            monkeypatch,
            "pretooluse",
            {
                "session_id": session_id,
                "transcript_path": str(empty_transcript if session_id == "repeat-session" else tmp_path / "other.jsonl"),
                "cwd": str(tmp_path),
                "tool_name": "Bash",
                "tool_input": {"command": command},
                **({"turn_id": "repeat-turn"} if codex else {}),
            },
            expected_code=expected_code,
        )

    context = pretooluse("repeat-session", "grep -rn needle .")
    message = json.loads(context)["hookSpecificOutput"]["additionalContext"]
    assert 'kind="block"' not in message
    assert "agent-basics:search" in message
    assert not pretooluse("repeat-session", "ls")
    blocked = json.loads(pretooluse("repeat-session", "grep -rn needle ."))["hookSpecificOutput"]
    assert blocked["permissionDecision"] == "deny"
    assert "起動を確認できない" in blocked["permissionDecisionReason"]
    _call_hook_in_process(
        monkeypatch,
        "posttooluse",
        {
            "hook_event_name": "PostToolUse",
            "session_id": "repeat-session",
            "cwd": str(tmp_path),
            "tool_name": "Skill",
            "tool_input": {"skill": "agent-basics:search"},
            **({"turn_id": "repeat-turn"} if codex else {}),
        },
    )
    assert not pretooluse("repeat-session", "grep -rn needle .")

    def response_context(output: str, identifier: str) -> dict:
        result = json.loads(output)
        body = result["hookSpecificOutput"]["additionalContext"]
        if codex and CODEX_NOTICE_REFERENCE_PREFIX in body:
            path = pathlib.Path(body.split(CODEX_NOTICE_REFERENCE_PREFIX, 1)[1].splitlines()[0])
            assert path.resolve().is_relative_to(managed_temp.resolve_session_temp(identifier).resolve())
            result["hookSpecificOutput"]["additionalContext"] = path.read_text(encoding="utf-8")
        return result

    assert response_context(pretooluse("other-session", "grep -rn needle ."), "other-session") == response_context(
        context, "repeat-session"
    )

    # 未配送の完了通知が残るtranscriptでは配送のためにターンを終え、残らないtranscriptでは何も返さない。
    # キューの記録の形は`_hooks/queued_notification_advisor_test.py`の生成と同じとする。
    notification = (
        "<task-notification>\n<task-id>repeat-task</task-id>\n"
        "<output-file>/tmp/repeat-task.output</output-file>\n<status>completed</status>\n</task-notification>"
    )
    queued = tmp_path / "queued.jsonl"
    queued.write_text(
        json.dumps({"type": "queue-operation", "operation": "enqueue", "content": notification}) + "\n", encoding="utf-8"
    )

    def stop(transcript: pathlib.Path) -> dict[str, Any]:
        output = _call_hook_in_process(monkeypatch, "stop", {"session_id": "stop-session", "transcript_path": str(transcript)})
        return cast(dict[str, Any], json.loads(output))

    assert stop(queued) == {"continue": False}

    assert stop(empty_transcript) == {}
    assert stop(queued) == {"continue": False}


def test_activity_lifecycle(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    identity = session_registry.ProcessIdentity.of(os.getpid())
    assert identity is not None
    host = session_activity.HostProcess("codex", identity)
    monkeypatch.setattr(session_activity, "host_process", lambda: host)
    monkeypatch.setattr(session_activity, "host_processes", lambda: ())
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    transcript = tmp_path / "activity.jsonl"
    transcript.write_text(
        json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n",
        encoding="utf-8",
    )
    payload: dict[str, object] = {"session_id": "activity-integration", "transcript_path": str(transcript), "turn_id": "t1"}
    response: dict[str, object] = {"decision": "block", "reason": "継続"}
    stop_module = _hook.importlib.import_module("agent_toolkit._hooks.stop")

    def emit_stop(_payload: str) -> int:
        print(json.dumps(response))
        return 0

    monkeypatch.setattr(stop_module, "main", emit_stop)

    def call(event: str, command: str = "session_activity", **extra: object) -> str:
        return _call_hook_in_process(monkeypatch, command, {**payload, "hook_event_name": event, **extra})

    assert not call("UserPromptSubmit")
    assert session_activity.scan_activity().has_activity
    assert json.loads(call("Stop", "stop")) == response
    assert session_activity.scan_activity().has_activity
    response.clear()
    assert json.loads(call("Stop", "stop")) == {}
    assert not session_activity.scan_activity().has_activity
    call("SubagentStart", agent_id="native-child")
    assert session_activity.scan_activity().has_activity
    call("SubagentStop", agent_id="native-child")
    assert not session_activity.scan_activity().has_activity
    call("UserPromptSubmit")
    assert session_activity.scan_activity().has_activity
    call("SessionEnd")
    assert not session_activity.scan_activity().has_activity
