"""Stopイベントの共通のエントリーポイントの判定順、例外隔離および応答集約を検証する。"""

import importlib
import json
import pathlib

import pytest

from agent_toolkit._common import completion_exit, session_state
from agent_toolkit._common import transcript as _transcript
from agent_toolkit._common.periodic_recheck import PERIODIC_RECHECK_PROMPT
from agent_toolkit._hooks import background_tasks as _background_tasks
from agent_toolkit._hooks import stop
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._testing.helpers import _write_transcript, repl_send_to_user_entries

_HOOKS_PATH = pathlib.Path(__file__).resolve().parents[2] / "hooks" / "hooks.json"


def _replace_checks(monkeypatch: pytest.MonkeyPatch, results: dict[str, tuple[str, str]]) -> None:
    for module_name, result in results.items():
        module = importlib.import_module(f"agent_toolkit._hooks.{module_name}")
        monkeypatch.setattr(module, "evaluate", lambda _payload, result=result, **kwargs: result)


def _state_path(directory: pathlib.Path, session_id: str) -> pathlib.Path:
    del directory
    path = session_state.state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_state(directory: pathlib.Path, session_id: str) -> dict:
    return json.loads(_state_path(directory, session_id).read_text(encoding="utf-8"))


def _set_state_directory(monkeypatch: pytest.MonkeyPatch, directory: pathlib.Path) -> None:
    monkeypatch.setenv("TMPDIR", str(directory))
    monkeypatch.setenv("TEMP", str(directory))
    monkeypatch.setenv("TMP", str(directory))


def test_blocks_and_notifications_are_aggregated_in_check_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _replace_checks(
        monkeypatch,
        {
            "run_skill_report_gate": ("block", "報告不足"),
            "plan_save_advisor": ("approve", ""),
            "agents_server_session_advisor": ("notify", "未観測session"),
            "termination_order_advisor": ("block", "別の遮断"),
        },
    )

    assert stop.evaluate("{}") == {
        "decision": "block",
        "reason": "報告不足\n\n別の遮断\n\n未観測session",
    }


def test_notifications_are_aggregated_without_block(monkeypatch: pytest.MonkeyPatch) -> None:
    _replace_checks(
        monkeypatch,
        {
            "run_skill_report_gate": ("approve", ""),
            "plan_save_advisor": ("approve", ""),
            "agents_server_session_advisor": ("notify", "通知1"),
            "termination_order_advisor": ("notify", "通知2"),
        },
    )

    assert stop.evaluate("{}") == {
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": "通知1\n\n通知2",
        }
    }


def test_yield_keeps_other_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    _replace_checks(
        monkeypatch, {"termination_order_advisor": ("notify", "自編集のwarn"), "queued_notification_advisor": ("yield", "")}
    )
    assert stop.evaluate("{}") == {
        "continue": False,
        "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "自編集のwarn"},
    }


def test_all_approve_returns_empty_object(monkeypatch: pytest.MonkeyPatch) -> None:
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})

    assert not stop.evaluate("{}")


@pytest.mark.parametrize("stage", ["periodic-recheck", "halt"])
def test_internal_stop_failure_keeps_approval_and_existing_log(tmp_path, monkeypatch, capsys, stage):
    _set_state_directory(monkeypatch, tmp_path)
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})

    def fail(*_args, **_kwargs):
        raise OSError("終了処理の故障注入")

    if stage == "periodic-recheck":
        marker = tmp_path / "marker"
        marker.mkdir()
        monkeypatch.setattr(stop, "function_hook_paths", lambda *_args, **_kwargs: (marker, tmp_path / "request"))
        expected = "periodic_recheck_error"
    else:
        _replace_checks(monkeypatch, {"run_skill_report_gate": ("halt", "3")})
        module = importlib.import_module("agent_toolkit._hooks.run_skill_report_gate")
        monkeypatch.setattr(module, "halt", fail, raising=False)
        expected = "halt_error"
    assert not stop.evaluate(json.dumps({"session_id": "stop-internal"}))
    captured = capsys.readouterr()
    assert not captured.out and not captured.err
    log = (tmp_path / "claude-agent-toolkit-stop-stop-internal.log").read_text(encoding="utf-8")
    assert expected in log


def _recheck_stop(monkeypatch, tmp_path, *, crons, background_tasks=(), decision=("approve", "")):
    """modの読込済みの会話で、全待機の終了か継続中かを判定するStopを1回実行し、modへの回収要求を返す。"""
    _set_state_directory(monkeypatch, tmp_path)
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    _replace_checks(monkeypatch, {"run_skill_report_gate": decision})
    marker = tmp_path / "marker-recheck.txt"
    marker.write_text("ready", encoding="utf-8")
    monkeypatch.setattr(stop, "function_hook_paths", lambda _session: (marker, tmp_path / "request-recheck.txt"))
    requests: list[str] = []

    async def request(_session_id, operation, _inputs, *, timeout):
        assert timeout > 0

        assert (tmp_path / "recheck-recheck.txt").read_text(encoding="utf-8") == "complete"
        requests.append(operation)
        return {"ok": True, "reason": ""}

    monkeypatch.setattr(stop.function_hook_requests, "request", request)
    _background_tasks._PENDING_ASYNC_WORK_CACHE.clear()  # pylint: disable=protected-access
    _transcript_scan._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access
    transcript = _write_transcript(tmp_path, [{"type": "assistant", "message": {"stop_reason": "end_turn", "content": []}}])
    payload = {
        "session_id": "recheck",
        "transcript_path": str(transcript),
        "background_tasks": list(background_tasks),
        "session_crons": crons,
    }
    stop.evaluate(json.dumps(payload))
    return requests


_COMMON_CRON = {"id": "c1", "schedule": "*/30 * * * *", "recurring": True, "prompt": PERIODIC_RECHECK_PROMPT}


def test_all_waits_done_collects_common_task_before_turn_ends(monkeypatch, tmp_path):
    """全待機の終了を判定したStopの中で回収を終え、実行中に発火時刻を迎えた発火分がidleで届かないようにする。"""
    assert _recheck_stop(monkeypatch, tmp_path, crons=[_COMMON_CRON]) == ["periodic_recheck_collect"]


@pytest.mark.parametrize(
    ("crons", "background_tasks", "decision"),
    [
        ([], (), ("approve", "")),
        (
            [{**_COMMON_CRON, "prompt": PERIODIC_RECHECK_PROMPT.replace("</atk-auto>", "期限監視\n</atk-auto>")}],
            (),
            ("approve", ""),
        ),
        ([_COMMON_CRON], ({"id": "b1", "type": "local_bash", "status": "running"},), ("approve", "")),
        ([_COMMON_CRON], (), ("block", "継続")),
    ],
    ids=["no-task", "obligation-task", "pending-background", "continued-turn"],
)
def test_stop_keeps_task_without_collect_request(monkeypatch, tmp_path, crons, background_tasks, decision):
    """共通promptのtaskが無い、待機が残る、ターンが続く場合はmodを待たない。"""
    assert not _recheck_stop(monkeypatch, tmp_path, crons=crons, background_tasks=background_tasks, decision=decision)


def test_collect_timeout_keeps_stop_approval(monkeypatch, tmp_path):
    """modが期限内に応答しなくてもStopの判定を変えず、診断を常時ログへ残す。"""
    _set_state_directory(monkeypatch, tmp_path)
    marker = tmp_path / "marker-recheck.txt"
    marker.write_text("ready", encoding="utf-8")
    monkeypatch.setattr(stop, "function_hook_paths", lambda _session: (marker, tmp_path / "request-recheck.txt"))
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})

    async def request(*_args, **_kwargs):
        raise TimeoutError("会話内modの確認応答が期限内にない")

    monkeypatch.setattr(stop.function_hook_requests, "request", request)
    _background_tasks._PENDING_ASYNC_WORK_CACHE.clear()  # pylint: disable=protected-access
    transcript = _write_transcript(tmp_path, [{"type": "assistant", "message": {"stop_reason": "end_turn", "content": []}}])
    payload = {
        "session_id": "recheck",
        "transcript_path": str(transcript),
        "background_tasks": [],
        "session_crons": [_COMMON_CRON],
    }
    assert not stop.evaluate(json.dumps(payload))
    log = (tmp_path / "claude-agent-toolkit-stop-recheck.log").read_text(encoding="utf-8")
    assert "periodic_recheck_collect_error" in log


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("decision", ["notify", "block", "codex-block", "approve"])
def test_continuation_tells_delegate_to_rewrite_full_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, delegated: bool, decision: str
) -> None:
    _set_state_directory(monkeypatch, tmp_path)
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    payload: dict[str, str] = {"session_id": f"continuation-{decision}-{delegated}"}
    if decision == "codex-block":
        # Codexは非遮断通知を配送できず、継続は遮断の`reason`だけで委譲先へ届く。
        payload["turn_id"] = "turn"
        _replace_checks(monkeypatch, {"response_language_advisor": ("block", "回答を書き直せ")})
    elif decision != "approve":
        _replace_checks(
            monkeypatch,
            {"agents_server_session_advisor": (decision, "未観測session"), "bugfix_gate": ("notify", "別の通知")},
        )

    result = stop.evaluate(json.dumps(payload))

    text = json.dumps(result, ensure_ascii=False)
    expected_count = 1 if delegated and decision != "approve" else 0

    assert text.count("最後の応答の本文だけ") == expected_count
    assert text.count("完了報告の全文を書き直せ") == expected_count
    if decision == "approve":
        assert not result
    elif decision == "notify":
        assert "decision" not in result
    else:
        assert result["decision"] == "block"


def test_suppressed_notifications_do_not_emit_empty_context(monkeypatch: pytest.MonkeyPatch) -> None:
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    _replace_checks(monkeypatch, {"termination_order_advisor": ("notify", "")})
    assert not stop.evaluate("{}")


def test_block_below_limit_increments_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    _set_state_directory(monkeypatch, tmp_path)
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    _replace_checks(monkeypatch, {"run_skill_report_gate": ("block", "報告不足")})
    session_id = "below-limit"
    _state_path(tmp_path, session_id).write_text(
        json.dumps({"stop_consecutive_block_count": 6}),
        encoding="utf-8",
    )

    result = stop.evaluate(json.dumps({"session_id": session_id, "stop_hook_active": True}))

    assert result["decision"] == "block"
    assert _read_state(tmp_path, session_id)["stop_consecutive_block_count"] == 7


def test_block_limit_approves_resets_count_and_logs_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    _set_state_directory(monkeypatch, tmp_path)
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    _replace_checks(monkeypatch, {"run_skill_report_gate": ("block", "報告不足")})
    session_id = "at-limit"
    _state_path(tmp_path, session_id).write_text(
        json.dumps({"stop_consecutive_block_count": 7}),
        encoding="utf-8",
    )

    assert not stop.evaluate(json.dumps({"session_id": session_id, "stop_hook_active": True}))
    assert _read_state(tmp_path, session_id)["stop_consecutive_block_count"] == 0
    log_text = (tmp_path / f"claude-agent-toolkit-stop-{session_id}.log").read_text(encoding="utf-8")
    assert "decision=approve_block_limit_reached" in log_text
    assert '"blocking_checks": ["run_skill_report_gate"]' in log_text


def test_first_block_starts_count_at_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    _set_state_directory(monkeypatch, tmp_path)
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    _replace_checks(monkeypatch, {"termination_order_advisor": ("block", "別の遮断")})
    session_id = "first-block"
    _state_path(tmp_path, session_id).write_text(
        json.dumps({"stop_consecutive_block_count": 5}),
        encoding="utf-8",
    )

    result = stop.evaluate(json.dumps({"session_id": session_id, "stop_hook_active": False}))

    assert result["decision"] == "block"
    assert _read_state(tmp_path, session_id)["stop_consecutive_block_count"] == 1


def test_approve_resets_consecutive_block_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    _set_state_directory(monkeypatch, tmp_path)
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    session_id = "approve-reset"
    _state_path(tmp_path, session_id).write_text(
        json.dumps({"stop_consecutive_block_count": 4}),
        encoding="utf-8",
    )

    assert not stop.evaluate(json.dumps({"session_id": session_id, "stop_hook_active": True}))
    assert _read_state(tmp_path, session_id)["stop_consecutive_block_count"] == 0


def test_exception_isolated_per_check(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path) -> None:
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    run_skill_report_gate = importlib.import_module("agent_toolkit._hooks.run_skill_report_gate")

    def raise_error(_payload: str) -> tuple[str, str]:
        raise RuntimeError("故障")

    monkeypatch.setattr(run_skill_report_gate, "evaluate", raise_error)

    _set_state_directory(monkeypatch, tmp_path)
    assert not stop.evaluate('{"session_id":"check-failure"}')
    assert not capsys.readouterr().err
    assert "故障" in (tmp_path / "claude-agent-toolkit-stop-check-failure.log").read_text(encoding="utf-8")


def test_stop_evaluations_scan_transcript_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    plan_save_advisor = importlib.import_module("agent_toolkit._hooks.plan_save_advisor")
    monkeypatch.setattr(plan_save_advisor, "is_process_loop_session", lambda *_args: False)
    monkeypatch.setattr(plan_save_advisor, "working_plans_root", lambda: tmp_path / "plans")
    transcript = _write_transcript(tmp_path, [])
    payload = json.dumps(
        {
            "session_id": "scan-once",
            "transcript_path": str(transcript),
            "background_tasks": [],
        }
    )
    original = _transcript.read_transcript_entries  # pylint: disable=protected-access
    calls = 0
    waits = 0

    def finish_transcript(_path: str) -> None:
        nonlocal waits
        waits += 1
        _write_transcript(tmp_path, [{"type": "assistant", "message": {"stop_reason": "end_turn", "content": []}}])

    def count_reads(path: str) -> list[dict]:
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(_transcript_scan, "wait_for_end_turn", finish_transcript)
    monkeypatch.setattr(_transcript, "read_transcript_entries", count_reads)
    _background_tasks._PENDING_ASYNC_WORK_CACHE.clear()  # pylint: disable=protected-access
    _transcript_scan._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access

    assert "decision" not in stop.evaluate(payload)
    assert waits == 1
    assert calls == 1


@pytest.mark.parametrize(
    ("stop_hook_active", "delegated"),
    [(False, False), (True, False), (False, True)],
    ids=["main", "stop-hook-active", "delegated"],
)
@pytest.mark.parametrize("block", [False, True])
def test_queued_notifications_yield_without_overriding_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    stop_hook_active: bool,
    delegated: bool,
    block: bool,
) -> None:
    _set_state_directory(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", raising=False)
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    else:
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    plan_save_advisor = importlib.import_module("agent_toolkit._hooks.plan_save_advisor")
    monkeypatch.setattr(plan_save_advisor, "working_plans_root", lambda: tmp_path / "plans")
    _replace_checks(monkeypatch, {"user_response_advisor": ("notify_user", "ユーザー宛て通知")})
    if block:
        _replace_checks(monkeypatch, {"termination_order_advisor": ("block", "別の遮断")})
    _background_tasks._PENDING_ASYNC_WORK_CACHE.clear()  # pylint: disable=protected-access
    _transcript_scan._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access
    output_file = str(tmp_path / "tasks" / "b6n4gipz5.output")
    notification = (
        f"<task-notification>\n<task-id>b6n4gipz5</task-id>\n<output-file>{output_file}</output-file>\n"
        "<status>completed</status>\n</task-notification>"
    )
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "…"}]}},
            {"type": "queue-operation", "operation": "enqueue", "content": notification},
        ],
    )
    payload = json.dumps(
        {
            "session_id": "queued-stop",
            "transcript_path": str(transcript),
            "stop_hook_active": stop_hook_active,
            "background_tasks": [],
        }
    )

    result = stop.evaluate(payload)

    assert result["systemMessage"] == "ユーザー宛て通知"
    if block:
        assert result["decision"] == "block"
        reason = result["reason"]
        assert isinstance(reason, str)
        assert "別の遮断" in reason
        assert output_file in reason
        assert "continue" not in result
    else:
        assert result == {"continue": False, "systemMessage": "ユーザー宛て通知"}
        state = _read_state(tmp_path, "queued-stop")
        assert not state.get("queued_notification_notified_ids")
        assert not state.get("warn_notice_counts")


@pytest.mark.parametrize(
    ("case", "approved"),
    [("clear", True), ("blocked", False), ("background", False), ("unrelated", False), ("repl", True), ("repl-error", False)],
)
def test_stop_completion_exit_verdict(case, approved, monkeypatch, tmp_path):
    _set_state_directory(monkeypatch, tmp_path)
    launch = "a" * 32
    monkeypatch.setenv(completion_exit.LAUNCH_ENV, launch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    if case == "blocked":
        _replace_checks(monkeypatch, {"run_skill_report_gate": ("block", "作業が残っている")})
    state = completion_exit.state_path(launch, root=tmp_path)
    assert state is not None
    state.parent.mkdir()
    completion_exit.write_state(state, {"session": "stop-completion", "call": "call-a", "status": "requested"})
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-a",
                            "name": "mcp__plugin_agent-toolkit_agents_server__send_to_user",
                            "input": {"kind": "completion", "message": "完了しました。"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-a" if case != "unrelated" else "call-b",
                            "content": '{"ok":true}',
                        }
                    ]
                },
            },
        ],
    )
    if case.startswith("repl"):
        transcript = _write_transcript(
            tmp_path,
            repl_send_to_user_entries("call-a", {"kind": "completion", "message": "完了しました。"}, ok=case == "repl"),
        )
    stop.evaluate(
        json.dumps(
            {
                "session_id": "stop-completion",
                "transcript_path": str(transcript),
                "background_tasks": [{"id": "held", "type": "local_bash", "status": "running"}] if case == "background" else [],
            }
        )
    )
    assert completion_exit.read_state(state).get("status") == ("approved" if approved else None)


@pytest.mark.parametrize(("ok", "expected"), [(True, "完了しました。"), (False, None)])
def test_completion_report_text_accepts_repl_delivery(ok, expected, tmp_path):
    session = "repl-report"
    record = tmp_path / "projects" / "repo" / f"{session}.jsonl"
    record.parent.mkdir(parents=True)
    entries = repl_send_to_user_entries("call-a", {"kind": "completion", "message": "完了しました。"}, ok=ok)
    record.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")

    state = {"session": session, "call": "call-a", "status": "exiting"}
    assert completion_exit.report_text(state, root=tmp_path) == expected
