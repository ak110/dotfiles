"""長時間の作業で、取得済みの根拠と残工程の照合を主体ごとに一定間隔で促す。  pyfltr: allow-comment"""

from __future__ import annotations

import os
import time

from agent_toolkit._common import process_loop_session
from agent_toolkit._common.delegated_session import is_delegated
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.session_state import update_state
from agent_toolkit._hooks import notice as _notice
from agent_toolkit._hooks.agent_id import is_main_agent_context, resolve_hook_agent_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter

SELF_CHECK_INTERVAL_SECONDS = 30 * 60
STATE_KEY = "progress_self_check"
SOURCE = "progress-self-check"

_llm_notice = _notice_formatter(SOURCE)

_SELF_CHECK_NOTICE_BODY = (
    "通知の時刻は点検の結果ではない。"
    "`main-reporting.md`「進捗点検」に従って、取得済みの完成条件の根拠と残工程を照合してください。"
    "差がある時だけ既存の記録を更新し、未達と処置を報告してください。"
)
_DELEGATED_NOTICE_BODY = (
    "取得済みの完成条件の根拠と残工程を照合してください。"
    "差・未達があれば既存の引き継ぎ記録を更新し、委譲元の判断が必要な差は直上へ通知してください。"
)


def _format_elapsed(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}分"
    hours, rest = divmod(minutes, 60)
    return f"{hours}時間{rest}分" if rest else f"{hours}時間"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _new_wi_text(session_id: str, recipient: str) -> tuple[str, str | None]:
    from agent_toolkit._atk.wi.listing import progress_new_wis  # pylint: disable=import-outside-toplevel

    try:
        rows = progress_new_wis(session_id, track_merges=True)
    except ActionableError as error:
        return f"新着WIは未確認。{error.message}", None
    except (Exception, SystemExit) as error:  # pylint: disable=broad-exception-caught
        return (
            f"新着WIは未確認（{type(error).__name__}）。`atk wi list`で確かめてください。",
            None,
        )
    if rows is None:
        return "新着WIは未確認（開始時集合なし）。", None
    tracked = [row for row in rows if row.merge_pending == "processing"]
    text, snapshot = _notice.new_wi_preview(session_id, recipient, [row for row in rows if row.merge_pending != "processing"])
    if tracked:
        text += " 取り込み結果待ち（再割当不要）: " + "、".join(row.filename for row in tracked) + "。"
    return text, snapshot


def claim_notice(payload: dict, *, now: float | None = None, periodic_recheck: bool = False) -> str | None:
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    agent_id = resolve_hook_agent_id(payload)
    current_time = time.time() if now is None else now
    elapsed: float | None = None
    since_notice: float | None = None

    def update(state: dict) -> dict | None:
        nonlocal elapsed, since_notice
        entries = state.get(STATE_KEY)
        entries = dict(entries) if isinstance(entries, dict) else {}
        entry = entries.get(agent_id)
        entry = dict(entry) if isinstance(entry, dict) else {}
        started_at = _number(entry.get("started_at"))
        if started_at is None:
            entries[agent_id] = {"started_at": current_time, "last_notified_at": current_time}
            state[STATE_KEY] = entries
            return state
        previous = _number(entry.get("last_notified_at"))
        previous = started_at if previous is None else previous
        if current_time - previous < SELF_CHECK_INTERVAL_SECONDS - (60 if periodic_recheck else 0):
            return None
        elapsed = current_time - started_at
        if previous > started_at:
            since_notice = current_time - previous
        entries[agent_id] = {"started_at": started_at, "last_notified_at": current_time}
        state[STATE_KEY] = entries
        return state

    if not update_state(session_id, update) or elapsed is None:
        return None
    elapsed_text = (
        f"この主体の初回観測から{_format_elapsed(elapsed)}たった。"
        if elapsed >= 0
        else "初回観測の保存時刻が通知生成時刻より後にあるため、経過時間は不明。"
    )
    main_agent = is_main_agent_context(payload)
    instruction = _SELF_CHECK_NOTICE_BODY if main_agent and not is_delegated(os.environ) else _DELEGATED_NOTICE_BODY
    previous_text = f"前回の通知から{_format_elapsed(since_notice)}たった。" if since_notice is not None else ""
    new_wis, wi_snapshot = (
        _new_wi_text(session_id, agent_id)
        if main_agent and process_loop_session.is_identified_process_loop_session(session_id, os.environ)
        else ("", None)
    )
    text = _llm_notice(elapsed_text + previous_text + new_wis + instruction, tag="notice")
    if text and wi_snapshot is not None:
        _notice.stage_new_wi_preview(text, session_id, agent_id, wi_snapshot)
    return text or None
