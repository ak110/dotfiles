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

委譲先での実行可否: 各判定モジュールが個別に適用可否を判断するため、エントリーポイント自体は除外せず実行できる。
未配送通知を次ターンへ返す実機観測と再検証は、`docs/development/audit-records.md`の
「agent-toolkit/agent_toolkit/_hooks/stop.py：未配送通知を次ターンへ返す：2026年10月10日」にある。
"""

import importlib
import json
import sys

lazy from agent_toolkit._atk.agents_exit_session import function_hook_paths
lazy from agent_toolkit._common import transcript as _transcript

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common.session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position
lazy from agent_toolkit._hooks.background_tasks import is_pending_async_work
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.stop_session import parse_stop_session as _parse_stop_session

CHECK_MODULE_NAMES = (
    "response_language_advisor",
    "busy_loop_guard",
    "autonomous_exit",
    "plan_save_advisor",
    "agents_server_session_advisor",
    "pending_question_advisor",
    "user_response_advisor",
    "termination_order_advisor",
    "queued_notification_advisor",
)

# Codexでは説明言語と起動証拠を判定し、起動済みの成果作業へ終了時の点検を一度通知する。
# 完遂・送達の意味判断はメインが担う。Claude Codeのtranscriptに依存する判定は適用しない。
CODEX_CHECK_MODULE_NAMES = ("response_language_advisor", "termination_order_advisor")

# 終了要求が成立し、同じStopで継続を求めてはならない`halt`の状態。
_TERMINATION_ISSUED = frozenset({"exit_requested", "terminating"})

_CONSECUTIVE_BLOCK_LIMIT = 7
_CONSECUTIVE_BLOCK_STATE_KEY = "stop_consecutive_block_count"


def _set_consecutive_block_count(state: dict, count: int) -> dict:
    """連続block回数を指定値へ更新する。"""
    state[_CONSECUTIVE_BLOCK_STATE_KEY] = count
    return state


def _write_recheck_verdict(session_id: str, payload: dict, *, may_collect: bool) -> None:
    """同じ会話のmodへStopの非同期判定を渡す。サブエージェントとCodexは対象外。"""
    if not session_id or is_codex_payload(payload) or payload.get("agent_id"):
        return
    paths = function_hook_paths(session_id)
    if paths is None:
        return
    marker, request = paths
    path = request.with_name(f"recheck-{session_id}.txt")
    try:
        if marker.read_text(encoding="utf-8") != "ready":
            return
        # 先に前回の判定を無効にする。読取失敗や判定例外から全終端を推定しない。
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
        pending = is_pending_async_work(
            transcript,
            session_id,
            background_tasks=payload.get("background_tasks"),
            session_state=read_state(session_id),
        )
        path.write_text("pending" if pending else "complete", encoding="utf-8")
    except FileNotFoundError:
        return
    except Exception as exc:  # noqa: BLE001 -- 回収判定の故障を他のStop判定から隔離する
        print(f"[stop/periodic-recheck] 判定受渡し失敗: {type(exc).__name__}: {exc}", file=sys.stderr)


def evaluate(payload_text: str) -> dict[str, object]:
    """各Stop判定を実行し、集約したhook応答を返す。"""
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
    halt_request: tuple[object, int] | None = None
    for module_name in CODEX_CHECK_MODULE_NAMES if is_codex_payload(payload) else CHECK_MODULE_NAMES:
        try:
            module = importlib.import_module(f"agent_toolkit._hooks.{module_name}")
            decision, body = (
                module.evaluate(payload_text, allow_delivery=not block_reasons)
                if module_name == "queued_notification_advisor"
                else module.evaluate(payload_text)
            )
        except Exception as exc:  # noqa: BLE001 -- 1判定の故障で他の終了判定を失わないため広範に捕捉
            print(
                f"[stop/{module_name}] 想定外エラー: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue
        if decision == "block":
            block_reasons.append(body)
            blocking_checks.append(module_name)
        elif decision == "notify":
            notifications.append(body)
        elif decision == "notify_user":
            user_messages.append(body)
        elif decision == "yield":
            yield_for_delivery = True
        elif decision == "halt":
            halt_request = (module, int(body))

    def _finalize(result: dict[str, object]) -> dict[str, object]:
        """ユーザー宛ての本文がある場合に`systemMessage`を添えて返す。"""
        if is_codex_payload(payload):
            return {key: value for key, value in result.items() if key in {"decision", "reason"}}
        _write_recheck_verdict(
            session_id,
            payload,
            may_collect=result.get("decision") != "block" and not yield_for_delivery and halt_request is None,
        )
        if user_messages:
            result["systemMessage"] = "\n\n".join(user_messages)
        return result

    if halt_request is not None and session_id and "response_language_advisor" not in blocking_checks:
        module, count = halt_request
        try:
            status, message = module.halt(session_id, count)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 -- 停止工程の故障で他の判定の結果を失わないため広範に捕捉
            print(f"[stop/halt] 想定外エラー: {type(exc).__name__}: {exc}", file=sys.stderr)
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
            state = read_state(session_id)
            current_count = state.get(_CONSECUTIVE_BLOCK_STATE_KEY, 0)
            if not isinstance(current_count, int) or isinstance(current_count, bool):
                current_count = 0
            if payload.get("stop_hook_active") is not True:
                current_count = 0
            if current_count >= _CONSECUTIVE_BLOCK_LIMIT:
                autonomous_exit_invoked = state.get("autonomous_exit_invoked") is True
                detail = {
                    "limit": _CONSECUTIVE_BLOCK_LIMIT,
                    "blocking_checks": blocking_checks,
                    "autonomous_exit_invoked": autonomous_exit_invoked,
                }
                append_stop_log(
                    session_id,
                    "approve_block_limit_reached",
                    {"details": json.dumps(detail, ensure_ascii=False)},
                )
                update_state(session_id, lambda item: _set_consecutive_block_count(item, 0))
                return _finalize({})
            update_state(session_id, lambda item: _set_consecutive_block_count(item, current_count + 1))
        return _finalize({"decision": "block", "reason": "\n\n".join([*block_reasons, *notifications])})
    if session_id:
        update_state(session_id, lambda item: _set_consecutive_block_count(item, 0))
    result: dict[str, object] = {"continue": False} if yield_for_delivery else {}
    if notifications:
        result["hookSpecificOutput"] = {
            "hookEventName": "Stop",
            "additionalContext": "\n\n".join(notifications),
        }
    return _finalize(result)


def _approve() -> None:
    """空のapprove応答を返す。"""
    print(json.dumps({}, ensure_ascii=False))


def main(payload_text: str) -> int:
    """Stop判定の集約結果を標準出力へ返す。"""
    print(json.dumps(evaluate(payload_text), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin.read()))
