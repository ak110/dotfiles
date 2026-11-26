import json
import pathlib
import types

import pytest

from agent_toolkit._atk.wi import process_loop_control
from agent_toolkit._atk.wi import process_loop_log as loop_log
from agent_toolkit._common import session_state
from agent_toolkit._common.process_loop_session import REPORT_SAVED_AT_KEY
from agent_toolkit._hooks import background_tasks as _background_tasks
from agent_toolkit._hooks import stop
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._hooks.stop_test import _read_state, _replace_checks, _set_state_directory, _state_path
from agent_toolkit._testing.helpers import _write_transcript


def _run_busy_loop_turns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    termination_status: str,
    *,
    abort_path: pathlib.Path | None = None,
) -> tuple[dict[str, object], dict[str, int]]:
    from agent_toolkit._atk import agents_exit_session  # pylint: disable=import-outside-toplevel

    _set_state_directory(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "busy-loop")
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(_background_tasks, "pending_async_status", lambda *args, **kwargs: "complete")
    calls = {"terminate": 0}

    def terminate(*, session_id: str) -> tuple[str, object]:
        del session_id  # noqa
        calls["terminate"] += 1
        return termination_status, None

    monkeypatch.setattr(loop_log, "abort_path", lambda: abort_path or tmp_path / "process-wi-abort")
    monkeypatch.setattr(agents_exit_session, "request_termination", terminate)
    _replace_checks(
        monkeypatch,
        {
            "plan_save_advisor": ("approve", ""),
            "agents_server_session_advisor": ("approve", ""),
            "user_response_advisor": ("approve", ""),
            "termination_order_advisor": ("block", "別の遮断"),
            "queued_notification_advisor": ("approve", ""),
        },
    )
    entries: list[dict] = []
    result: dict[str, object] = {}
    for index in range(3):
        entries = [
            *entries,
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "…"}]}},
        ]
        directory = tmp_path / f"turn-{index}"
        directory.mkdir()
        transcript = _write_transcript(directory, entries)
        _transcript_scan._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access
        result = stop.evaluate(json.dumps({"session_id": "busy-loop", "transcript_path": str(transcript)}))
        if index < 2:
            assert calls["terminate"] == 0
    assert not loop_log.abort_path().exists()
    return result, calls


@pytest.mark.parametrize("termination_status", ["exit_requested", "terminating"])
def test_busy_loop_halt_suppresses_continuation_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, termination_status: str
) -> None:
    result, calls = _run_busy_loop_turns(monkeypatch, tmp_path, termination_status)

    assert calls == {"terminate": 1}
    assert "decision" not in result
    assert "reason" not in result
    assert "無進捗のターンが3回続いた" in str(result["systemMessage"])


@pytest.mark.parametrize("termination_status", ["changed", "unsupported"])
def test_busy_loop_halt_unissued_keeps_other_decisions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, termination_status: str
) -> None:
    result, calls = _run_busy_loop_turns(monkeypatch, tmp_path, termination_status)

    assert calls == {"terminate": 1}
    assert result["decision"] == "block"
    assert "別の遮断" in str(result["reason"])
    assert "セッションの終了要求は送っていない" in str(result["systemMessage"])


def _codex_stop_payload(session_id: str, transcript: pathlib.Path) -> str:
    return json.dumps({"session_id": session_id, "turn_id": "turn-1", "transcript_path": str(transcript)})


def test_codex_run_skill_session_is_blocked_until_report_is_saved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    from agent_toolkit._atk.wi import add as wi_add  # pylint: disable=import-outside-toplevel
    from agent_toolkit._common import mcp_environment  # pylint: disable=import-outside-toplevel

    _set_state_directory(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", "run-1")
    monkeypatch.setenv(mcp_environment.SESSION_START_INPUT_ENV, json.dumps({"source": "run-skill", "skill": "my-skill"}))
    _replace_checks(monkeypatch, {"response_language_advisor": ("approve", "")})
    saved = {"value": False}
    monkeypatch.setattr(wi_add, "has_synced_run_report", lambda *_args, **_kwargs: saved["value"])
    transcript = _write_transcript(tmp_path, [])

    blocked = stop.evaluate(_codex_stop_payload("codex-run-skill", transcript))
    saved["value"] = True
    approved = stop.evaluate(_codex_stop_payload("codex-run-skill", transcript))

    assert blocked["decision"] == "block"
    assert "報告用UWI" in str(blocked["reason"])
    assert not approved


_LIMIT_ENV_NAMES = (
    "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
    "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID",
    "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM",
    "AGENT_TOOLKIT_DELEGATED_SESSION",
    "AGENT_TOOLKIT_OWNER_SESSION",
    "AGENT_TOOLKIT_RUN_SKILL_ID",
)
_LOOP = {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1"}
_LIMIT_SESSION_ID = "loop-at-limit"


def _stop_at_block_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    env: dict[str, str],
    *,
    extra: dict[str, object] | None = None,
    count: int = 7,
    termination_status: str = "terminating",
) -> tuple[dict[str, object], list[str | None]]:
    from agent_toolkit._atk import agents_exit_session  # pylint: disable=import-outside-toplevel

    _set_state_directory(monkeypatch, tmp_path)
    for name in _LIMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    _replace_checks(
        monkeypatch, {"response_language_advisor": ("block", "言語"), "run_skill_report_gate": ("block", "報告不足")}
    )
    requests: list[str | None] = []

    def terminate(*, session_id: str | None = None) -> tuple[str, object]:
        requests.append(session_id)
        return termination_status, types.SimpleNamespace(host="codex", pid=4321)

    monkeypatch.setattr(agents_exit_session, "request_termination", terminate)
    _state_path(tmp_path, _LIMIT_SESSION_ID).write_text(json.dumps({"stop_consecutive_block_count": count}), encoding="utf-8")
    payload = {"session_id": _LIMIT_SESSION_ID, "stop_hook_active": True, **(extra or {})}
    return stop.evaluate(json.dumps(payload)), requests


@pytest.mark.parametrize(
    ("env", "extra"),
    [
        ({}, {}),
        ({}, {"turn_id": "t1"}),
        ({"AGENT_TOOLKIT_RUN_SKILL_ID": "run-1"}, {}),
        ({"AGENT_TOOLKIT_RUN_SKILL_ID": "run-1"}, {"turn_id": "t1"}),
        ({**_LOOP, "AGENT_TOOLKIT_DELEGATED_SESSION": "1"}, {}),
        ({**_LOOP, "AGENT_TOOLKIT_OWNER_SESSION": "owner"}, {"turn_id": "t1"}),
        ({**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": "other"}, {}),
        ({**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": _LIMIT_SESSION_ID}, {"agent_id": "sub-1"}),
        ({**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": _LIMIT_SESSION_ID}, {"agent_id": "sub-1", "turn_id": "t1"}),
        # 会話IDを確定できない環境印だけの会話。統括が入れ子で起動した`codex exec`がこれに当たる。  # pyfltr: allow-comment
        (_LOOP, {}),
        (_LOOP, {"turn_id": "t1"}),
        ({**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM": "unclaimed"}, {"turn_id": "t1"}),
    ],
)
def test_block_limit_never_terminates_non_coordinator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, env: dict[str, str], extra: dict[str, object]
) -> None:
    result, requests = _stop_at_block_limit(monkeypatch, tmp_path, env, extra=extra)

    assert not requests
    assert "decision" not in result


def test_block_limit_does_not_terminate_session_other_than_claimed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    from agent_toolkit._common import process_loop_session  # pylint: disable=import-outside-toplevel

    env = {**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM": "launch-1"}
    _set_state_directory(monkeypatch, tmp_path)
    process_loop_session.claim_process_loop_session("outer-coordinator", env)
    process_loop_session.claim_process_loop_session(_LIMIT_SESSION_ID, env)

    result, requests = _stop_at_block_limit(monkeypatch, tmp_path, env, extra={"turn_id": "t1"})

    assert not requests
    assert "decision" not in result


@pytest.mark.parametrize("extra", [{}, {"turn_id": "t1"}])
@pytest.mark.parametrize(
    ("termination_status", "issued"),
    [("exit_requested", True), ("terminating", True), ("unsupported", False)],
)
def test_block_limit_terminates_identified_coordinator_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    extra: dict[str, object],
    termination_status: str,
    issued: bool,
) -> None:
    env = {**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": _LIMIT_SESSION_ID}
    result, requests = _stop_at_block_limit(monkeypatch, tmp_path, env, extra=extra, termination_status=termination_status)

    assert requests == [_LIMIT_SESSION_ID]
    assert "decision" not in result
    if not extra:
        assert "遮断が7回続いた" in str(result["systemMessage"])
        assert ("セッションの終了要求は送っていない" in str(result["systemMessage"])) is not issued
    assert _read_state(tmp_path, _LIMIT_SESSION_ID)["stop_consecutive_block_count"] == 0


def test_block_limit_terminates_coordinator_identified_by_session_start_claim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    from agent_toolkit._common import process_loop_session  # pylint: disable=import-outside-toplevel

    env = {**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM": "launch-1"}
    _set_state_directory(monkeypatch, tmp_path)
    process_loop_session.claim_process_loop_session(_LIMIT_SESSION_ID, env)
    process_loop_session.claim_process_loop_session("nested-codex-exec", env)

    _, requests = _stop_at_block_limit(monkeypatch, tmp_path, env, extra={"turn_id": "t1"})

    assert requests == [_LIMIT_SESSION_ID]


@pytest.mark.parametrize(
    ("background_tasks", "terminated"),
    [
        ([{"type": "local_bash", "id": "b1", "description": "長い検証"}], False),
        ([{"type": "teammate", "id": "m1"}], True),
    ],
)
def test_block_limit_does_not_terminate_while_background_tasks_remain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, background_tasks: list[dict], terminated: bool
) -> None:
    env = {**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": _LIMIT_SESSION_ID}
    result, requests = _stop_at_block_limit(monkeypatch, tmp_path, env, extra={"background_tasks": background_tasks})

    assert requests == ([_LIMIT_SESSION_ID] if terminated else [])
    assert "decision" not in result
    assert _read_state(tmp_path, _LIMIT_SESSION_ID)["stop_consecutive_block_count"] == 0


def test_block_limit_below_does_not_terminate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    env = {**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": _LIMIT_SESSION_ID}
    result, requests = _stop_at_block_limit(monkeypatch, tmp_path, env, count=6)

    assert not requests
    assert result["decision"] == "block"


@pytest.mark.parametrize(
    "boundary",
    [
        "complete",
        "feedback",
        "plain",
        "failed-delivery",
        "saved-only",
        "ordinary-save",
        "wait",
        "interrupt",
        "abort",
        "resume",
        "compact",
        "exit",
        "dry-run",
        "other-uwi",
        "active-agent",
        "delegate",
        "codex",
        "codex-exit",
        "codex-ordinary",
        "codex-interrupt",
        "codex-exec",
        "codex-exec-exit",
        "codex-async-save",
        "codex-multi-save",
        "codex-stale-save",
        "codex-previous-log",
        "codex-previous-state",
        "codex-wrong-turn",
        "guidance-only",
        "codex-guidance",
    ],
)
def test_process_loop_report_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, boundary: str) -> None:
    # 実例の行12、9866～9867、9950～9959を匿名化して縮約した。  # pyfltr: allow-comment
    ordinary = boundary in {"ordinary-save", "other-uwi", "codex-ordinary"}
    question_type = "post-approval" if ordinary else "report"
    report_body = tmp_path / "report.md"
    marker = "" if ordinary or boundary in {"guidance-only", "codex-guidance"} else "completion_report: true\n"
    # 判定は保存済みのWIを読み、原稿は保存後に消えていても結果を変えない。  # pyfltr: allow-comment
    saved_entry = tmp_path / "notes" / "20000101-010101-001.md"
    saved_entry.parent.mkdir()
    saved_entry.write_text(f"---\ntype: uwi\n{marker}question_type: report\n---\n\n# 報告: 作業結果\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    command = (
        f"cd /workspace && /toolkit/scripts/atk wi add --type=uwi --question-type={question_type} --body-file {report_body}"
    )
    saved = "1件をinboxへ投入した" if ordinary else "1件を報告として保存した"
    if boundary == "dry-run":
        command += " --dry-run"
    records: list[dict[str, object]] = [
        {"type": "user", "message": {"content": '<atk-auto source="process-loop" kind="goal">巡回</atk-auto>'}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "id": "save", "input": {"command": command}}]},
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "save", "is_error": False, "content": ""}]},
            "toolUseResult": {
                "stdout": f"警告: 表記を確認する\n成功: {saved}\n  ~/notes/20000101-010101-001.md",
                "interrupted": False,
            },
        },
    ]
    report_name = "20000101-010101-002.md" if boundary == "other-uwi" else "20000101-010101-001.md"
    failed_delivery = boundary == "failed-delivery"
    records.extend(
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__plugin_agent-toolkit_agents_server__send_to_user",
                            "id": "deliver",
                            "input": {"message": f"報告用UWI: {report_name}\n以上で、このセッションの作業は全て終わった。"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "deliver", "is_error": failed_delivery}]},
                "toolUseResult": json.dumps({"ok": not failed_delivery}),
            },
        ]
    )
    if boundary in {"plain", "saved-only", "ordinary-save"}:
        del records[-2:]
    if boundary in {"plain", "ordinary-save"}:
        records.append(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": f"報告用UWI: {report_name}\n以上で、このセッションの作業は全て終わった。"}
                    ]
                },
            }
        )
    if boundary == "feedback":
        records.append({"type": "user", "isMeta": True, "message": {"content": "Stop hook feedback: 終了操作を実行せよ。"}})
    if boundary == "interrupt":
        records.append({"type": "user", "message": {"content": "[Request interrupted by user]"}})
    if boundary == "compact":
        records.extend(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "mcp__plugin_agent-toolkit_agents_server__compact_conversation",
                                "id": "compact",
                                "input": {"resume": True},
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "tool_use_id": "compact", "is_error": False}]},
                    "toolUseResult": '{"ok":true}',
                },
            ]
        )
    if boundary == "exit":
        records.extend(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Bash", "id": "exit", "input": {"command": "atk agents-exit-session"}}
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [{"type": "tool_result", "tool_use_id": "exit", "content": '{"exit_session_invoked":true}'}]
                    },
                },
            ]
        )
    records.append(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "待機中: child" if boundary == "wait" else "…"}]},
        }
    )
    if boundary.startswith("codex"):
        records = [{"type": "event_msg", "payload": {"type": "user_message", "message": "巡回"}}]
        commands = [("save", command, f"成功: {saved}\n  ~/notes/20000101-010101-001.md")]
        if boundary.endswith("-exit"):
            commands.append(("exit", "atk agents-exit-session", '{"exit_session_invoked":true}'))
        for call_id, cmd, stdout in commands:
            if boundary.startswith("codex-exec"):
                call = {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "input": f"text(await tools.exec_command({{cmd: {json.dumps(cmd)}, yield_time_ms: 1000}}));",
                }
                response = {
                    "type": "custom_tool_call_output",
                    "output": [
                        {"type": "input_text", "text": "Script completed\nWall time 0.1 seconds\nOutput:\n"},
                        {"type": "input_text", "text": json.dumps({"exit_code": 0, "output": stdout})},
                    ],
                }
            else:
                call = {"type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": cmd})}
                response = {"type": "function_call_output", "output": f"Process exited with code 0\nOutput:\n{stdout}"}
            records.extend({"type": "response_item", "payload": {**item, "call_id": call_id}} for item in (call, response))
    if boundary in {"codex-async-save", "codex-multi-save", "codex-stale-save"}:
        records = [
            {"type": "event_msg", "timestamp": "2026-11-23T00:00:00Z", "payload": {"type": "task_started", "turn_id": "turn"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "launch",
                    "input": f"text(await tools.exec_command({{cmd: {json.dumps(command)}}}));",
                },
            },
        ]
        success = json.dumps({"exit_code": 0, "output": f"成功: {saved}\n  ~/notes/20000101-010101-001.md"})
        if boundary == "codex-multi-save":
            records[1] = {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "launch",
                    "input": (
                        'text(await tools.exec_command({cmd: "true"}));'
                        f"text(await tools.exec_command({{cmd: {json.dumps(command)}}}));"
                        'text(await tools.exec_command({cmd: "true"}));'
                    ),
                },
            }
            output = [
                {"type": "input_text", "text": line}
                for line in ("Script completed\n", "先行結果", success, "別の結果", "最終行")
            ]
        else:
            records.extend(
                [
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "custom_tool_call_output",
                            "call_id": "launch",
                            "output": [
                                {"type": "input_text", "text": "Script completed\n"},
                                {"type": "input_text", "text": '{"session_id":123}'},
                            ],
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "custom_tool_call",
                            "name": "exec",
                            "call_id": "poll",
                            "input": 'text(await tools.write_stdin({session_id:123, chars:""}));',
                        },
                    },
                ]
            )
            output = [{"type": "input_text", "text": "Script completed\n"}, {"type": "input_text", "text": success}]
        records.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "launch" if boundary == "codex-multi-save" else "poll",
                    "output": output,
                },
            }
        )
        saved_at = 1795391999 if boundary == "codex-stale-save" else 1795392001
        assert session_state.update_state("coordinator", lambda state: {**state, REPORT_SAVED_AT_KEY: saved_at})
    if boundary in {"codex-previous-log", "codex-previous-state", "codex-wrong-turn"}:
        if boundary != "codex-previous-log":
            assert session_state.update_state("coordinator", lambda state: {**state, REPORT_SAVED_AT_KEY: 1795392001})
        if boundary == "codex-previous-state":
            records = [{"type": "event_msg", "timestamp": "2026-11-23T00:00:00Z", "payload": {"type": "user_message"}}]
        records.append(
            {
                "type": "event_msg",
                "timestamp": "2026-11-23T00:01:00Z",
                "payload": {
                    "type": "task_started",
                    "turn_id": "not-the-stop-turn" if boundary == "codex-wrong-turn" else "turn",
                },
            }
        )
    if boundary == "codex-interrupt":
        records.append({"type": "event_msg", "payload": {"type": "turn_aborted", "reason": "interrupted"}})
    path = tmp_path / "coordinator.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "coordinator")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    _replace_checks(
        monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES if name != "process_loop_exit_gate"}
    )

    abort = tmp_path / "abort"
    if boundary == "abort":
        abort.touch()
    monkeypatch.setattr(loop_log, "abort_path", lambda: abort)
    request = process_loop_control.ResumeRequest("request", "id", "coordinator") if boundary == "resume" else None
    monkeypatch.setattr(process_loop_control, "read_resume_request", lambda: request)
    payload: dict[str, object] = {"session_id": "coordinator", "transcript_path": str(path)}
    if boundary == "active-agent":
        payload["background_tasks"] = [{"id": "child", "type": "local_agent", "status": "running"}]
    if boundary == "delegate":
        payload["agent_id"] = "child"
    if boundary.startswith("codex"):
        payload["turn_id"] = "turn"
    result = stop.evaluate(json.dumps(payload))
    if boundary in {
        "complete",
        "feedback",
        "plain",
        "failed-delivery",
        "saved-only",
        "codex",
        "codex-exec",
        "codex-async-save",
        "codex-multi-save",
    }:
        assert result.get("decision") == "block"
        assert "atk agents-exit-session" in str(result.get("reason"))
    else:
        assert result.get("decision") != "block"
