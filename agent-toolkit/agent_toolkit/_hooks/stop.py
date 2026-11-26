"""Stopイベントの共通のエントリーポイントとして、判定を順に実行し単一の応答へ集約する。

各判定の例外は判定単位で隔離し、残りの判定を継続する。遮断理由内と通知内ではそれぞれ
判定順を保つ。遮断が1件以上ある場合は全ての遮断理由の後に通知を空行で連結して`reason`へ含め、
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
from typing import Any
lazy import asyncio

lazy from agent_toolkit._atk.agents_exit_session import function_hook_paths
lazy from agent_toolkit._common import completion_exit, function_hook_requests
lazy from agent_toolkit._common import delegated_session as _delegated_session
lazy from agent_toolkit._common import transcript as _transcript
lazy from agent_toolkit._common.function_hook_requests import cleanup, enqueue
lazy from agent_toolkit._common.periodic_recheck import PERIODIC_RECHECK_PROMPT
lazy from agent_toolkit._common.session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position
lazy from agent_toolkit._hooks import busy_loop_guard as _busy_loop_guard
lazy from agent_toolkit._hooks.background_tasks import active_non_teammate_tasks, pending_async_status
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.stop_session import parse_stop_session as _parse_stop_session

CHECK_MODULE_NAMES = (
    "delivered_repost_gate",
    "response_language_advisor",
    "busy_loop_guard",
    "run_skill_report_gate",
    "plan_save_advisor",
    "agents_server_session_advisor",
    "user_response_advisor",
    "permission_denial_notice",
    "hook_notice_response_gate",
    "bugfix_gate",
    "norm_excuse_gate",
    "termination_order_advisor",
    "process_loop_exit_gate",
    "queued_notification_advisor",
    "improvement_status_advisor",
)

CODEX_CHECK_MODULE_NAMES = (
    "response_language_advisor",
    "run_skill_report_gate",
    "norm_excuse_gate",
    "process_loop_exit_gate",
)


_TERMINATION_ISSUED = frozenset({"exit_requested", "terminating"})

_CONSECUTIVE_BLOCK_LIMIT = 7
_CONSECUTIVE_BLOCK_STATE_KEY = "stop_consecutive_block_count"


_RECHECK_COLLECT_WAIT_SECONDS = 20


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
        lines = _transcript.read_transcript_lines(transcript)
        if not lines or not any(line.strip() for line in lines):
            return
        for line in lines:
            if line.strip() and not isinstance(json.loads(line), dict):
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
            and state.get("call") in completion_exit.current_reports(_transcript.read_transcript_entries(source))
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


def evaluate(payload_text: str) -> dict[str, object]:
    resolved = _parse_stop_session(payload_text, lambda: None)
    session_id = ""
    payload: dict = {}
    if resolved is not None:
        session_id, payload = resolved
    _write_recheck_verdict(session_id, payload, may_collect=False)

    block_reasons: list[str] = []
    blocking_checks: list[str] = []
    notifications: list[str] = []
    user_messages: list[str] = []
    yield_for_delivery = False
    halt_request: functools.partial[tuple[str, str]] | None = None
    for module_name in CODEX_CHECK_MODULE_NAMES if is_codex_payload(payload) else CHECK_MODULE_NAMES:
        if module_name == "response_language_advisor" and "delivered_repost_gate" in blocking_checks:
            continue
        try:
            module = importlib.import_module(f"agent_toolkit._hooks.{module_name}")
            decision, body = (
                module.evaluate(payload_text, allow_delivery=not block_reasons)
                if module_name == "queued_notification_advisor"
                else module.evaluate(payload_text)
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
            user_messages.append(body)
        elif decision == "yield":
            yield_for_delivery = True
        elif decision == "halt":
            halt_request = functools.partial(_call_halt, module, session_id, int(body))

    def _finalize(result: dict[str, object]) -> dict[str, object]:
        if is_codex_payload(payload):
            return {key: value for key, value in result.items() if key in {"decision", "reason"}}
        _completion_verdict(
            session_id,
            payload,
            allowed=not block_reasons and not yield_for_delivery and halt_request is None,
        )
        _write_recheck_verdict(
            session_id,
            payload,
            may_collect=result.get("decision") != "block" and not yield_for_delivery and halt_request is None,
        )
        if user_messages:
            result["systemMessage"] = "\n\n".join(user_messages)
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
        try:
            status, message = halt_request()
        except Exception as exc:  # noqa: BLE001 -- 停止工程の故障で他の判定の結果を失わないため広範に捕捉
            append_stop_log(session_id, "halt_error", {"error": f"{type(exc).__name__}: {exc}"})
        else:
            user_messages.append(message)
            if status in _TERMINATION_ISSUED:
                append_stop_log(
                    session_id,
                    "approve_halt_terminating",
                    {"status": status, "suppressed_checks": ",".join(blocking_checks)},
                )
                update_state(session_id, lambda item: _set_consecutive_block_count(item, 0))
                return _finalize({})
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
        return _finalize(
            {"decision": "block", "reason": "\n\n".join([*block_reasons, *notifications, *_continuation_notice()])}
        )
    if session_id:
        update_state(session_id, lambda item: _set_consecutive_block_count(item, 0))
    result: dict[str, object] = {"continue": False} if yield_for_delivery else {}
    if notifications:
        result["hookSpecificOutput"] = {
            "hookEventName": "Stop",
            "additionalContext": "\n\n".join([*notifications, *_continuation_notice()]),
        }
    return _finalize(result)


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
