"""Stopイベントの判定を順に実行し、単一の応答へ集約する共通入口。

各判定の例外は判定単位で隔離し、残りの判定を継続する。遮断理由内と通知内ではそれぞれ
判定順を保つ。遮断が1件以上ある場合は全ての遮断理由の後に通知を空行で連結して`reason`へ含め、
遮断が無い場合だけ通知を`hookSpecificOutput.additionalContext`へ集約する。

連続blockの上限は7回とし、Claude Codeが8回の連続blockでフックを上書きして
ターンを終える仕様の内側で、打ち切りの事実を記録して終了を許可する。

委譲先での実行可否: 各判定モジュールが個別に適用可否を判断するため、共通入口自体は除外せず実行できる。
"""

import importlib
import json
import sys

from agent_toolkit._hooks.session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._hooks.stop_gate import append_stop_log  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._hooks.stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position
    parse_stop_session as _parse_stop_session,
)

CHECK_MODULE_NAMES = (
    "autonomous_exit",
    "plan_save_advisor",
    "agents_server_session_advisor",
    "pending_question_advisor",
)

_CONSECUTIVE_BLOCK_LIMIT = 7
_CONSECUTIVE_BLOCK_STATE_KEY = "stop_consecutive_block_count"


def _set_consecutive_block_count(state: dict, count: int) -> dict:
    """連続block回数を指定値へ更新する。"""
    state[_CONSECUTIVE_BLOCK_STATE_KEY] = count
    return state


def evaluate(payload_text: str) -> dict[str, object]:
    """各Stop判定を実行し、集約したhook応答を返す。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    session_id = ""
    payload: dict = {}
    if resolved is not None:
        session_id, payload = resolved

    block_reasons: list[str] = []
    blocking_checks: list[str] = []
    notifications: list[str] = []
    for module_name in CHECK_MODULE_NAMES:
        try:
            module = importlib.import_module(f"agent_toolkit._hooks.{module_name}")
            decision, body = module.evaluate(payload_text)
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
                return {}
            update_state(session_id, lambda item: _set_consecutive_block_count(item, current_count + 1))
        return {"decision": "block", "reason": "\n\n".join([*block_reasons, *notifications])}
    if session_id:
        update_state(session_id, lambda item: _set_consecutive_block_count(item, 0))
    if notifications:
        return {
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": "\n\n".join(notifications),
            }
        }
    return {}


def _approve() -> None:
    """空のapprove応答を返す。"""
    print(json.dumps({}, ensure_ascii=False))


def main(payload_text: str) -> int:
    """Stop判定の集約結果を標準出力へ返す。"""
    print(json.dumps(evaluate(payload_text), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin.read()))
