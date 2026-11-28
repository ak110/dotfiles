"""Stopイベントの共通のエントリーポイントとして、判定を順に実行し単一の応答へ集約する。 pyfltr: allow-comment

各判定の例外は判定単位で隔離し、残りの判定を継続する。報告の遮断は是正・警告への対応・送達の順に案内し、他の遮断理由内と通知内では判定順を保つ。遮断が1件以上ある場合は全ての遮断理由の後に通知を空行で連結して`reason`へ含め、
遮断が無い場合だけ通知を`hookSpecificOutput.additionalContext`へ集約する。

判定が`notify_user`を返した本文はエンドユーザー向けの`systemMessage`へ集約し、遮断の有無によらず同じ応答へ添える。

判定が`halt`を返した場合は、全判定の後にその判定モジュールの`halt`で終了に関わる副作用を実行する。
終了要求が成立した（`exit_requested`または`terminating`）入力では、他の判定の遮断・通知・配送への復帰を返さず、
停止の事実だけを`systemMessage`で伝える。終了要求が成立しなかった入力では、他の判定の結果を通常どおり集約する。

連続blockの上限は7回とする。Claude Codeは8回の連続blockでフックを上書きしてターンを終えるため、
その手前の7回で打ち切りの事実を記録して終了を許可する。
process-loopの統括の最上位では、打ち切った対話CLIが入力待ちで残らないよう、
無進捗の停止と同じ終了要求（`busy_loop_guard.terminate`）でセッションを終える。
終了要求は会話IDで統括と確定できる場合（`busy_loop_guard.is_target_session`）に限る。
未終了のバックグラウンドタスクが残る場合は、終了要求を送らず許可だけを返す。タスクの完了通知で再開した後のStopが改めて判定する。

委譲先での実行可否: 各判定モジュールが個別に適用可否を判断するため、エントリーポイント自体は除外せず実行できる。
未配送通知を次ターンへ返す実機観測と再検証は、`docs/development/audit-records.md`の
「agent-toolkit/agent_toolkit/_hooks/stop.py：未配送通知を次ターンへ返す：2026年10月10日」にある。
"""

import functools
import importlib
import json
import os
import sys
import time
from typing import Any
lazy import asyncio

from agent_toolkit._common.agents_server_tools import MCP_NAMESPACES
from agent_toolkit._hooks import timing
lazy from agent_toolkit._atk.agents_exit_session import function_hook_paths
lazy from agent_toolkit._atk.config_resolution import resolve_mutable_setting
lazy from agent_toolkit._common import completion_exit, function_hook_requests
lazy from agent_toolkit._common import delegated_session as _delegated_session
lazy from agent_toolkit._common.function_hook_requests import cleanup, enqueue
lazy from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, opening_tag
lazy from agent_toolkit._common.periodic_recheck import (
    LEGACY_PERIODIC_RECHECK_MARKER,
    PERIODIC_RECHECK_MARKER,
    PERIODIC_RECHECK_PROMPT,
)
lazy from agent_toolkit._common.process_loop_session import is_process_loop_session
lazy from agent_toolkit._common.send_to_user import is_completion
lazy from agent_toolkit._common.session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position
lazy from agent_toolkit._hooks import agents_server_observations, delivered_repost_gate
lazy from agent_toolkit._hooks import busy_loop_guard as _busy_loop_guard
lazy from agent_toolkit._hooks.background_tasks import active_non_teammate_tasks, pending_async_status
lazy from agent_toolkit._hooks.background_tasks_notifications import queued_task_notification_contents
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.stop_session import parse_stop_session as _parse_stop_session
lazy from agent_toolkit._hooks.transcript_scan import read_stop_entries, read_stop_transcript, stop_transcript_scope
lazy from agent_toolkit._hooks.user_response_advisor import _human_prompt

CHECK_MODULE_NAMES = (
    "delivered_repost_gate",
    "response_language_advisor",
    "busy_loop_guard",
    "run_skill_report_gate",
    "agents_server_session_advisor",
    "user_response_advisor",
    "permission_denial_notice",
    "process_loop_exit_gate",
    "queued_notification_advisor",
    "improvement_status_advisor",
    "norm_excuse_gate",
)

CODEX_CHECK_MODULE_NAMES = (
    "response_language_advisor",
    "run_skill_report_gate",
    "process_loop_exit_gate",
    "norm_excuse_gate",
)


_TERMINATION_ISSUED = frozenset({"exit_requested", "terminating"})

_CONSECUTIVE_BLOCK_LIMIT = 7
_CONSECUTIVE_BLOCK_STATE_KEY = "stop_consecutive_block_count"


_RECHECK_COLLECT_WAIT_SECONDS = 20
_RUNTIME_TOOLS = frozenset(prefix + "apply_runtime_changes" for prefix in MCP_NAMESPACES)
_RUNTIME_RESULT_MARKER = opening_tag(AUTO_INSERTED_ELEMENT, {"source": "runtime-changes", "kind": "notice"})


def _set_consecutive_block_count(state: dict, count: int) -> dict:
    state[_CONSECUTIVE_BLOCK_STATE_KEY] = count
    return state


def _write_recheck_verdict(session_id: str, payload: dict, *, may_collect: bool) -> None:
    """同じ会話のmodへStopの非同期判定を渡す。サブエージェントとCodexは対象外。"""
    if not session_id or is_codex_payload(payload) or payload.get("agent_id"):
        return
    cleanup(session_id)
    tasks = payload.get("background_tasks")
    if isinstance(tasks, list) and all(
        isinstance(task, dict) and all(isinstance(task.get(key), str) for key in ("id", "type", "status")) for task in tasks
    ):
        enqueue(session_id, "background_snapshot", {"ids": [task["id"] for task in tasks]})
    paths = function_hook_paths(session_id)
    if paths is None:
        return
    marker, request = paths
    path = request.with_name(f"recheck-{session_id}.txt")
    try:
        if marker.read_text(encoding="utf-8") != "ready":
            return
        # 先に前回の判定を無効にする。読取失敗や判定例外から全終了を推定しない。
        path.write_text("unknown", encoding="utf-8")
        transcript = payload.get("transcript_path")
        if not may_collect or not isinstance(transcript, str) or not transcript:
            return
        snapshot = read_stop_transcript(transcript)
        if snapshot is None:
            return
        for position, line in enumerate(snapshot.lines):
            if position not in snapshot.entries_by_line and line.strip() and not isinstance(json.loads(line), dict):
                return
        if not snapshot.entries_by_line:
            return
        status = pending_async_status(
            transcript,
            session_id,
            background_tasks=payload.get("background_tasks"),
            session_state=read_state(session_id),
        )
        path.write_text(status, encoding="utf-8")
    except FileNotFoundError:
        return
    except Exception as exc:  # noqa: BLE001 -- 回収判定の故障を他のStop判定から隔離する
        append_stop_log(session_id, "periodic_recheck_error", {"error": f"{type(exc).__name__}: {exc}"})
        return
    if status == "complete" and _holds_common_recheck_task(payload.get("session_crons")):
        _collect_recheck_before_idle(session_id)


def _completion_verdict(session_id: str, payload: dict, *, allowed: bool) -> None:
    path = completion_exit.state_path(os.environ.get(completion_exit.LAUNCH_ENV, ""))
    if path is None or not session_id or payload.get("agent_id") or is_codex_payload(payload):
        return
    state = completion_exit.read_state(path)
    if state.get("session") != session_id or state.get("status") not in ("requested", "approved"):
        return
    try:
        source = payload.get("transcript_path")
        if (
            allowed
            and state.get("status") == "requested"
            and isinstance(source, str)
            and state.get("call") in completion_exit.current_reports(read_stop_entries(source))
            and pending_async_status(
                source, session_id, background_tasks=payload.get("background_tasks"), session_state=read_state(session_id)
            )
            == "complete"
        ):
            completion_exit.write_state(path, {**state, "status": "approved"})
        else:
            path.unlink(missing_ok=True)
    except (OSError, ValueError) as error:
        append_stop_log(session_id, "completion_exit_error", {"error": str(error)})


def _holds_common_recheck_task(crons: object) -> bool:
    return isinstance(crons, list) and any(
        isinstance(cron, dict) and cron.get("prompt") == PERIODIC_RECHECK_PROMPT for cron in crons
    )


def _collect_recheck_before_idle(session_id: str) -> None:
    """全待機の終了を判定したStopの中で、modに共通promptの定期再確認taskを回収させる。

    Claude Codeはターンの実行中に発火時刻を迎えたcronを保留し、ターンの終了直後のidleで発火させる。
    ターンの終了後（`turn.complete`）の回収はこの発火に間に合わず、確認する対象の無いターンが1回始まる。
    Stopが終わるまでターンはidleにならないため、Stopの中で回収を終えればこの発火は起きない。
    削除できなかった場合は、`turn.complete`の回収が再評価して警告を届ける。
    """
    try:
        asyncio.run(
            function_hook_requests.request(session_id, "periodic_recheck_collect", {}, timeout=_RECHECK_COLLECT_WAIT_SECONDS)
        )
    except (OSError, ValueError) as exc:
        append_stop_log(session_id, "periodic_recheck_collect_error", {"error": f"{type(exc).__name__}: {exc}"})


def _runtime_reserved(entries: list[dict]) -> bool:
    result_index = next(
        (
            index
            for index in range(len(entries) - 1, -1, -1)
            if (text := delivered_repost_gate.turn_prompt(entries[index])) and text.lstrip().startswith(_RUNTIME_RESULT_MARKER)
        ),
        -1,
    )
    calls: set[str] = set()
    for entry, block in completion_exit.conversation_tool_blocks(entries[result_index + 1 :]):
        if (
            block.get("type") == "tool_use"
            and isinstance(name := block.get("name"), str)
            and name in _RUNTIME_TOOLS
            and isinstance(call := block.get("id"), str)
        ):
            calls.add(call)
        elif (
            block.get("type") == "tool_result"
            and isinstance(call := block.get("tool_use_id"), str)
            and call in calls
            and completion_exit.ui_receipt(entry, block) is not None
        ):
            return True
    return False


def _intermission(payload: dict, session_id: str, delivery: tuple[list[dict], bool | None]) -> bool:
    """報告なしで区切り、待機や予約済み反映の後に再開する根拠を返す。 pyfltr: allow-comment"""
    last = payload.get("last_assistant_message")
    if isinstance(last, str) and last.strip() not in ("", "…"):
        return False
    entries, _ = delivery
    turn_start = 0
    prompt = ""
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if delivered_repost_gate.turn_boundary(entry):
            turn_start = index + 1
            text = delivered_repost_gate.turn_prompt(entry)
            if text is not None and _human_prompt(entry, text) is None:
                prompt = text.lstrip()
            break
    turn = entries[turn_start:]
    if completion_exit.reports(turn) or any(
        is_completion(text, None) for _, _, text in delivered_repost_gate.delivered_messages(turn)
    ):
        return False
    if _runtime_reserved(entries):
        return bool(prompt) and delivery[1] is None
    if delivery[1] is not None:
        return False
    if queued_task_notification_contents(entries) and prompt:
        return True
    if not prompt.startswith((PERIODIC_RECHECK_MARKER, LEGACY_PERIODIC_RECHECK_MARKER, "<task-notification>")):
        return False
    source = payload.get("transcript_path")
    if not isinstance(source, str):
        return False
    state = read_state(session_id)
    if (
        pending_async_status(source, session_id, background_tasks=payload.get("background_tasks"), session_state=state)
        == "pending-background"
    ):
        return True
    pending = agents_server_observations.pending_session_ids(state, "main")
    retained = agents_server_observations.retained_running_session_ids(state, "main")
    return bool(agents_server_observations.own_waited_session_ids([*pending, *retained]))


def evaluate(payload_text: str) -> dict[str, object]:
    stages = timing.Stages()
    with stages.measure("stop_session"):
        resolved = _parse_stop_session(payload_text, lambda: None)
    session_id = ""
    payload: dict = {}
    if resolved is not None:
        session_id, payload = resolved
    with stop_transcript_scope(payload.get("transcript_path")):
        return _evaluate(payload_text, session_id, payload, stages)


def _evaluate(payload_text: str, session_id: str, payload: dict, stages: timing.Stages) -> dict[str, object]:
    codex = is_codex_payload(payload)
    with stages.measure("recheck_reset"):
        _write_recheck_verdict(session_id, payload, may_collect=False)

    source = payload.get("transcript_path")
    delivery = None
    quiet_intermission = False
    if isinstance(source, str) and source and not (codex or is_antigravity_payload(payload)):
        try:
            with stages.measure("current_turn_delivery"):
                delivery = delivered_repost_gate.current_turn_delivery(source)
            with stages.measure("intermission"):
                quiet_intermission = _intermission(payload, session_id, delivery)
        except Exception as exc:  # noqa: BLE001 -- 共通証拠の取得失敗で個別の終了判定を失わない
            append_stop_log(session_id, "check_error", {"check": "intermission", "error": f"{type(exc).__name__}: {exc}"})
    entries = delivery[0] if delivery is not None else []
    options: dict[str, dict[str, Any]] = {
        "delivered_repost_gate": {"quiet_intermission": quiet_intermission, "delivery": delivery},
        "agents_server_session_advisor": {"entries": entries},
        "norm_excuse_gate": {"entries": entries},
    }

    block_reasons: list[str] = []
    blocking_checks: list[str] = []
    notifications: list[str] = []
    user_messages: list[str] = []
    prevention_warning = ""
    yield_for_delivery = False
    halt_request: functools.partial[tuple[str, str]] | None = None
    for module_name in CODEX_CHECK_MODULE_NAMES if codex else CHECK_MODULE_NAMES:
        if module_name == "response_language_advisor" and "delivered_repost_gate" in blocking_checks:
            continue
        try:
            with stages.measure(module_name):
                module = importlib.import_module(f"agent_toolkit._hooks.{module_name}")
                decision, body = (
                    module.evaluate(payload_text, allow_delivery=not block_reasons)
                    if module_name == "queued_notification_advisor"
                    else module.evaluate(payload_text, **options.get(module_name, {}))
                )
        except Exception as exc:  # noqa: BLE001 -- 1判定の故障で他の終了判定を失わないため広範に捕捉
            append_stop_log(session_id, "check_error", {"check": module_name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if decision == "block":
            block_reasons.append(body)
            blocking_checks.append(module_name)
        elif decision == "notify" and body:
            notifications.append(body)
        elif decision == "notify_user" and body:
            if module_name == "norm_excuse_gate":
                prevention_warning = body
            elif not codex:
                user_messages.append(body)
        elif decision == "yield":
            yield_for_delivery = True
        elif decision == "halt":
            halt_request = functools.partial(_call_halt, module, session_id, int(body))

    finalize_started = time.monotonic()

    def _finalize(result: dict[str, object], *, terminating: bool = False) -> dict[str, object]:
        stages.rows.append(("aggregate_result", time.monotonic() - finalize_started, "ok"))
        if codex:
            result = {key: value for key, value in result.items() if key in {"decision", "reason"}}
            if prevention_warning and not terminating:
                result["systemMessage"] = prevention_warning
        else:
            with stages.measure("completion_verdict"):
                _completion_verdict(
                    session_id,
                    payload,
                    allowed=not block_reasons and not yield_for_delivery and halt_request is None,
                )
            with stages.measure("recheck_verdict"):
                _write_recheck_verdict(
                    session_id,
                    payload,
                    may_collect=result.get("decision") != "block" and not yield_for_delivery and halt_request is None,
                )
            if prevention_warning and not terminating:
                user_messages.append(prevention_warning)
            if user_messages:
                result["systemMessage"] = "\n\n".join(user_messages)
            if (
                session_id
                and not terminating
                and not is_antigravity_payload(payload)
                and result.get("decision") != "block"
                and result.get("continue") is not False
                and "hookSpecificOutput" not in result
                and _waits_for_input(session_id, payload)
            ):
                result["terminalSequence"] = "\a"
        timing.record_stages(payload, "stop", stages.rows)
        return result

    block_count = _consecutive_block_count(session_id, payload) if block_reasons and session_id else 0
    limit_reached = block_count >= _CONSECUTIVE_BLOCK_LIMIT
    remaining_tasks = active_non_teammate_tasks(payload.get("background_tasks")) if limit_reached else []
    if (
        limit_reached
        and halt_request is None
        and not remaining_tasks
        and _busy_loop_guard.is_target_session(session_id, payload)
    ):
        halt_request = functools.partial(
            _busy_loop_guard.terminate,
            session_id,
            f"Stop hookによる終了の遮断が{_CONSECUTIVE_BLOCK_LIMIT}回続いた。",
            "halt_block_limit",
            {"limit": _CONSECUTIVE_BLOCK_LIMIT, "blocking_checks": ",".join(blocking_checks)},
        )
    if halt_request is not None and session_id and (limit_reached or "response_language_advisor" not in blocking_checks):
        stages.rows.append(("halt_preparation", time.monotonic() - finalize_started, "ok"))
        try:
            with stages.measure("halt"):
                status, message = halt_request()
        except Exception as exc:  # noqa: BLE001 -- 停止工程の故障で他の判定の結果を失わないため広範に捕捉
            append_stop_log(session_id, "halt_error", {"error": f"{type(exc).__name__}: {exc}"})
            finalize_started = time.monotonic()
        else:
            finalize_started = time.monotonic()
            user_messages.append(message)
            if status in _TERMINATION_ISSUED:
                append_stop_log(
                    session_id,
                    "approve_halt_terminating",
                    {"status": status, "suppressed_checks": ",".join(blocking_checks)},
                )
                update_state(session_id, lambda item: _set_consecutive_block_count(item, 0))
                return _finalize({}, terminating=True)
    if block_reasons:
        if session_id:
            if limit_reached:
                detail = {
                    "limit": _CONSECUTIVE_BLOCK_LIMIT,
                    "blocking_checks": blocking_checks,
                    "background_tasks": len(remaining_tasks),
                }
                append_stop_log(
                    session_id,
                    "approve_block_limit_reached",
                    {"details": json.dumps(detail, ensure_ascii=False)},
                )
                update_state(session_id, lambda item: _set_consecutive_block_count(item, 0))
                return _finalize({})
            update_state(session_id, lambda item: _set_consecutive_block_count(item, block_count + 1))
        reports = dict(zip(blocking_checks, block_reasons, strict=True))
        ordered = [reports.pop("delivered_repost_gate")] if "delivered_repost_gate" in reports else []
        reason = "\n\n".join([*ordered, *reports.values(), *notifications, *_continuation_notice()])
        return _finalize({"decision": "block", "reason": reason})
    if session_id:
        update_state(session_id, lambda item: _set_consecutive_block_count(item, 0))
    result: dict[str, object] = {"continue": False} if yield_for_delivery else {}
    if notifications:
        result["hookSpecificOutput"] = {
            "hookEventName": "Stop",
            "additionalContext": "\n\n".join([*notifications, *_continuation_notice()]),
        }
    return _finalize(result)


def _waits_for_input(session_id: str, payload: dict) -> bool:
    try:
        if resolve_mutable_setting("claude_stop_bell", read_only=True) != "true":
            return False
        if is_process_loop_session(session_id, os.environ):
            return False
        source = payload.get("transcript_path")
        return (
            pending_async_status(
                source if isinstance(source, str) else "",
                session_id,
                background_tasks=payload.get("background_tasks"),
                session_state=read_state(session_id),
            )
            == "complete"
        )
    except (OSError, ValueError) as error:
        append_stop_log(session_id, "stop_bell_error", {"error": str(error)})
        return False


def _call_halt(module: Any, session_id: str, count: int) -> tuple[str, str]:
    return module.halt(session_id, count)


def _consecutive_block_count(session_id: str, payload: dict) -> int:
    if payload.get("stop_hook_active") is not True:
        return 0
    count = read_state(session_id).get(_CONSECUTIVE_BLOCK_STATE_KEY, 0)
    return count if isinstance(count, int) and not isinstance(count, bool) else 0


def _continuation_notice() -> tuple[str, ...]:
    """継続を返すときに、agents_serverの委譲先へ委譲元が受け取る本文の範囲を1回だけ伝える。

    継続を起こす判定は`CHECK_MODULE_NAMES`とCodex用の組に分かれるため、各判定ではなく集約側で添える。
    """
    return (_delegated_session.FINAL_RESPONSE_NOTICE,) if _delegated_session.is_delegated(os.environ) else ()


def _approve() -> None:
    print(json.dumps({}, ensure_ascii=False))


def main(payload_text: str) -> int:
    print(json.dumps(evaluate(payload_text), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin.read()))
