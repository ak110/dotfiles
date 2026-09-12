"""TaskStopの停滞検知完了記録をタスク単位で管理する。"""

from __future__ import annotations

import time
from collections.abc import Iterable

from agent_toolkit._hooks.session_state import update_state

STATE_KEY = "stall_detection_completed_at_by_task"
READY_WINDOW_SECONDS = 300


def target_ids(tool_input: dict) -> set[str]:
    """TaskStop入力から現行と互換の停止対象識別子を返す。"""
    return {value for key in ("task_id", "shell_id") if isinstance((value := tool_input.get(key)), str) and value}


def record_completion(session_id: str, task_id: str, *, now: float | None = None) -> bool:
    """停滞検知を完了した`task_id`と時刻を記録する。"""
    recorded_at = time.time() if now is None else now

    def _record(state: dict) -> dict:
        records = state.get(STATE_KEY)
        records = dict(records) if isinstance(records, dict) else {}
        records[task_id] = recorded_at
        state[STATE_KEY] = records
        return state

    return update_state(session_id, _record)


def has_recent_completion(
    session_id: str,
    task_ids: Iterable[str],
    *,
    now: float | None = None,
) -> bool:
    """入力IDに一致する有効な停滞検知完了記録がある場合に真を返す。

    期限切れ要素は同じ排他更新内で除去する。記録はTaskStop成功後まで保持し、
    PreToolUseの失敗又は許可後のツール失敗で停止根拠を失わないようにする。
    """
    checked_at = time.time() if now is None else now
    targets = set(task_ids)
    matched = False

    def _check(state: dict) -> dict | None:
        nonlocal matched
        raw = state.get(STATE_KEY)
        if not isinstance(raw, dict):
            return None
        records = {
            task_id: recorded_at
            for task_id, recorded_at in raw.items()
            if isinstance(task_id, str)
            and isinstance(recorded_at, (int, float))
            and 0 <= checked_at - recorded_at <= READY_WINDOW_SECONDS
        }
        matched = bool(targets & records.keys())
        if records == raw:
            return None
        if records:
            state[STATE_KEY] = records
        else:
            state.pop(STATE_KEY, None)
        return state

    update_state(session_id, _check)
    return matched


def consume_completion(session_id: str, task_ids: Iterable[str]) -> bool:
    """TaskStopに成功した入力IDの停滞検知完了記録を削除する。"""
    targets = set(task_ids)

    def _consume(state: dict) -> dict | None:
        raw = state.get(STATE_KEY)
        if not isinstance(raw, dict):
            return None
        records = {task_id: value for task_id, value in raw.items() if task_id not in targets}
        if records == raw:
            return None
        if records:
            state[STATE_KEY] = records
        else:
            state.pop(STATE_KEY, None)
        return state

    return update_state(session_id, _consume)
