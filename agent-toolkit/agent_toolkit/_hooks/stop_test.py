"""Stopイベントの共通のエントリーポイントの判定順、例外隔離および応答集約を検証する。"""

import importlib
import json
import pathlib
import types

import pytest

from agent_toolkit._atk.wi import process_loop_log as loop_log
from agent_toolkit._common import session_state
from agent_toolkit._common import transcript as _transcript
from agent_toolkit._common.periodic_recheck import PERIODIC_RECHECK_PROMPT
from agent_toolkit._hooks import background_tasks as _background_tasks
from agent_toolkit._hooks import stop
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._testing.helpers import _write_transcript

_HOOKS_PATH = pathlib.Path(__file__).resolve().parents[2] / "hooks" / "hooks.json"


def _replace_checks(monkeypatch: pytest.MonkeyPatch, results: dict[str, tuple[str, str]]) -> None:
    """各判定の`evaluate`を指定結果へ置き換える。"""
    for module_name, result in results.items():
        module = importlib.import_module(f"agent_toolkit._hooks.{module_name}")
        monkeypatch.setattr(module, "evaluate", lambda _payload, result=result, **kwargs: result)


def _state_path(directory: pathlib.Path, session_id: str) -> pathlib.Path:
    """テスト用のセッション状態ファイルを返す。"""
    del directory
    path = session_state.state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_state(directory: pathlib.Path, session_id: str) -> dict:
    """テスト用のセッション状態を返す。"""
    return json.loads(_state_path(directory, session_id).read_text(encoding="utf-8"))


def _set_state_directory(monkeypatch: pytest.MonkeyPatch, directory: pathlib.Path) -> None:
    """Stop判定ログの一時ディレクトリを固定する。通常状態はテストごとの状態領域を使う。"""
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
    """通知配送のための終了でも、他の判定が返したwarn本文を応答に残す。"""
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
    """終了イベントの支援処理の故障だけで、モデルの継続を強制しない。"""
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
        # 回収はStopの応答より前、判定を書いた後に終える。
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
    """agents_serverの委譲先へ継続を返すときだけ、最後の応答へ完了報告の全文を書き直す指示を1回添える。

    委譲元が受け取るのは最後の応答だけのため、継続後の追記だけでは先の完了報告が失われる。
    """
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
    # 継続本文の中に、委譲元が最後の応答だけを受け取る旨と全文の書き直しの指示が、判定の数によらず1回だけ入る。
    assert text.count("最後の応答の本文だけ") == expected_count
    assert text.count("完了報告の全文を書き直せ") == expected_count
    if decision == "approve":
        assert not result
    elif decision == "notify":
        assert "decision" not in result
    else:
        assert result["decision"] == "block"


def test_suppressed_notifications_do_not_emit_empty_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """共通noticeが抑止した本文は、空の通知応答へ変換しない。"""
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    _replace_checks(monkeypatch, {"termination_order_advisor": ("notify", "")})
    assert not stop.evaluate("{}")


def test_block_below_limit_increments_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """上限未満のblockは遮断を維持し、連続回数を増やす。"""
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
    """上限到達時はapproveし、回数と遮断判定を記録する。"""
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
    """再入でない入力は既存値にかかわらず連続回数を1から始める。"""
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
    """遮断が無い入力は連続回数を0へ戻す。"""
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
    """他の遮断が無い未配送通知は、追加の警告応答を生まずホストへ戻す。"""
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


def _run_busy_loop_turns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    termination_status: str,
    *,
    abort_path: pathlib.Path | None = None,
) -> tuple[dict[str, object], dict[str, int]]:
    """常駐ループのセッションで無進捗ターンを閾値まで重ね、最後のStop応答と停止工程の呼出し回数を返す。

    無進捗停止と報告不足判定は実物を使い、終了要求の送出だけを指定した状態の結果へ置き換える。
    報告不足判定は`atk agents-exit-session`の起動記録が無いため、停止が無ければ継続を求める。
    """
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
    """無進捗停止が終了要求を成立させたStopは、報告不足判定や他の判定の継続要求を返さない。"""
    result, calls = _run_busy_loop_turns(monkeypatch, tmp_path, termination_status)

    assert calls == {"terminate": 1}
    assert "decision" not in result
    assert "reason" not in result
    assert "無進捗のターンが3回続いた" in str(result["systemMessage"])


@pytest.mark.parametrize("termination_status", ["changed", "unsupported"])
def test_busy_loop_halt_unissued_keeps_other_decisions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, termination_status: str
) -> None:
    """終了要求を送れなかったStopは、他の判定の継続要求と未発行を述べる通知をそのまま返す。"""
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
    """Codexのrun-skillのセッションは報告用UWIの保存を確認するまで遮断し、保存後は終了を許す。"""
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
    """連続blockが`count`回の状態でClaude・Codexの両方で遮断するStopを評価し、応答と終了要求の宛先を返す。"""
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
        # 会話IDを確定できない環境印だけの会話。統括が入れ子で起動した`codex exec`がこれに当たる。
        (_LOOP, {}),
        (_LOOP, {"turn_id": "t1"}),
        ({**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM": "unclaimed"}, {"turn_id": "t1"}),
    ],
)
def test_block_limit_never_terminates_non_coordinator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, env: dict[str, str], extra: dict[str, object]
) -> None:
    """統括と会話IDで確定できないセッションは、遮断が上限に達しても終了を要求されず、終了を許可される。"""
    result, requests = _stop_at_block_limit(monkeypatch, tmp_path, env, extra=extra)

    assert not requests
    assert "decision" not in result


def test_block_limit_does_not_terminate_session_other_than_claimed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """最初のSessionStartが確保した統括と別の会話（統括が入れ子で起動したCLI）は、上限でも終了を要求されない。"""
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
    """会話IDで確定した統括の遮断が上限に達したら、1回だけ終了を要求して入力待ちに残さず終える。"""
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
    """会話IDを渡せない新規起動のCodex・agyの統括も、最初のSessionStartが確保した会話IDで確定して終了を要求する。"""
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
    """未終了のバックグラウンドタスクが残る統括は、上限でも終了を要求せず許可だけを返し、完了通知の再開を待つ。"""
    env = {**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": _LIMIT_SESSION_ID}
    result, requests = _stop_at_block_limit(monkeypatch, tmp_path, env, extra={"background_tasks": background_tasks})

    assert requests == ([_LIMIT_SESSION_ID] if terminated else [])
    assert "decision" not in result
    assert _read_state(tmp_path, _LIMIT_SESSION_ID)["stop_consecutive_block_count"] == 0


def test_block_limit_below_does_not_terminate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """上限未満の遮断では統括へ終了を要求しない。"""
    env = {**_LOOP, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": _LIMIT_SESSION_ID}
    result, requests = _stop_at_block_limit(monkeypatch, tmp_path, env, count=6)

    assert not requests
    assert result["decision"] == "block"
