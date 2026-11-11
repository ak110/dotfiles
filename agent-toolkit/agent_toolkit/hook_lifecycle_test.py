"""hook_lifecycleの契約テスト。"""

import json
import os
import pathlib
import shlex
import tempfile
from typing import Any, cast

import pytest

from agent_toolkit import hook as _hook
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import session_activity, session_registry, session_state, state_paths
from agent_toolkit._common.persisted_output_reference import CODEX_NOTICE_REFERENCE_PREFIX
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.hook_support import _call_hook_in_process
from agent_toolkit._testing.pretooluse_support import codex_delivered_payload, codex_execution_payload


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("unchanged_documents_resolution")
def test_repeated_calls_in_one_process_follow_input_and_session_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, codex: bool
) -> None:
    """依存を読み込んだ後の反復呼び出しでも、スキルの取得の判定と未配送通知の判定が入力と状態に従う。

    公開hookの起動、入力、資料の取得、配送記録、session間の分離、Stopの判定は呼び出しごとに実行する。
    """
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

    # 関係する初回操作に資料または読取の案内を届け、通常の操作を継続する。
    context = pretooluse("repeat-session", "grep -rn needle .")
    message = json.loads(context)["hookSpecificOutput"]["additionalContext"]
    assert 'kind="block"' not in message
    pending = pretooluse("repeat-session", "ls")
    assert not pending
    assert '<atk-auto source="read-delivery" kind="document">' not in message
    if not codex:
        assert "agent-basics:search" in message
    pretooluse("repeat-session", "grep -rn needle .")
    for _attempt in range(12):
        records = session_state.read_state("repeat-session")[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
        targets = [
            target
            for target in read_delivery.selected_targets(records, "main")
            if not read_delivery.target_ready(records, target, "main")
        ]
        if not targets:
            assert _attempt > 0
            break
        for target in targets:
            skill = read_delivery.skill_name(target.path)
            event: dict[str, object] = {
                "session_id": "repeat-session",
                "cwd": str(tmp_path),
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash" if codex else "Skill" if skill else "Read",
                "tool_input": {"command": f"cat -- {shlex.quote(str(target.path))}"}
                if codex
                else {"skill": skill}
                if skill
                else {"file_path": str(target.path)},
                "tool_response": {"stdout": target.path.read_text(encoding="utf-8"), "exit_code": 0},
                **({"turn_id": "repeat-turn"} if codex else {}),
            }
            reading = {key: value for key, value in event.items() if key not in {"hook_event_name", "tool_response"}}
            guidance = _call_hook_in_process(monkeypatch, "pretooluse", reading)
            if guidance:
                specific = json.loads(guidance)["hookSpecificOutput"]
                assert specific.get("permissionDecision") != "deny"
                assert "読込条件の候補" not in specific["additionalContext"]
                assert "未取得資料" not in specific["additionalContext"]
            if codex:
                event = codex_execution_payload(event, tmp_path, exit_code=0)
            _call_hook_in_process(monkeypatch, "posttooluse", event)
            if codex:
                codex_delivered_payload({**event, "transcript_path": str(empty_transcript)}, tmp_path)
                pretooluse("repeat-session", "ls")
            acquired = session_state.read_state("repeat-session")[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
            assert read_delivery.target_ready(acquired, target, "main")
        pretooluse("repeat-session", "ls")
    else:
        pytest.fail("成功した全文取得後も未取得資料が残った")
    assert not pretooluse("repeat-session", "ls")
    assert not pretooluse("repeat-session", "grep -rn needle .")

    def response_context(output: str, identifier: str) -> dict:
        result = json.loads(output)
        body = result["hookSpecificOutput"]["additionalContext"]
        if codex and CODEX_NOTICE_REFERENCE_PREFIX in body:
            path = pathlib.Path(body.split(CODEX_NOTICE_REFERENCE_PREFIX, 1)[1].splitlines()[0])
            assert path.resolve().is_relative_to(managed_temp.resolve_session_temp(identifier).resolve())
            result["hookSpecificOutput"]["additionalContext"] = path.read_text(encoding="utf-8")
        return result

    # 保存先はsessionごとに異なるため、保存した通知本文と他の応答項目を比較する。
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
    """公開hookの通常応答を保持しつつ、Stop継続とターン終了を活動取得へ渡す。"""
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
