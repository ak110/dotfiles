"""シェル実行の全体区間を観測し、長い完了が続いた区間の見直しを促す。

Codexのwall_time_secondsは各pollの待機時間なので全体時間へ使わない。
Claudeのduration_msは公式HooksのPostToolUse/Failure inputが定める実行時間を使う。
背景通知までの区間は通知配送の遅延も含む観測経過時間である。
"""

from __future__ import annotations

import math
import re
import time

from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS
from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import formatter, informational

STATE_KEY = "command_timing"
_notice = formatter("command-timing")
_NOTIFICATIONS = re.compile(r"<task-notification>.*?</task-notification>", re.DOTALL)


def start(payload: dict) -> None:
    """実行前の時刻を同じ主体・tool_use_idへ一度だけ記録する。"""
    session = payload.get("session_id")
    identifier = payload.get("tool_use_id")
    if payload.get("tool_name") not in ATK_SHELL_TOOLS or not isinstance(session, str) or not session:
        return
    if not isinstance(identifier, str) or not identifier:
        return
    actor = resolve_hook_agent_id(payload)
    now = time.time()

    def record(state: dict) -> dict:
        actors = state.setdefault(STATE_KEY, {})
        current = actors.setdefault(actor, {"calls": {}, "recent": [], "kind": "", "notified": False})
        calls = current["calls"]
        calls.setdefault(identifier, {"started": now})
        return state

    update_state(session, record)


def observe(payload: dict, *, background_task_id: str | None = None) -> list[str]:
    """完了した元実行だけを集計し、通知本文を既存のイベント集約へ返す。"""
    if payload.get("tool_name") not in ATK_SHELL_TOOLS:
        return []
    identifier = payload.get("tool_use_id")
    session = payload.get("session_id")
    if not isinstance(session, str) or not session or not isinstance(identifier, str) or not identifier:
        return []
    response = payload.get("tool_response")
    if background_task_id:
        actor = resolve_hook_agent_id(payload)

        def retain(state: dict) -> dict | None:
            call = state.get(STATE_KEY, {}).get(actor, {}).get("calls", {}).get(identifier)
            if not isinstance(call, dict) or call.get("done"):
                return None
            call["task_id"] = background_task_id
            return state

        update_state(session, retain)
        return []
    if isinstance(response, dict) and response.get("session_id") is not None and response.get("exit_code") is None:
        return []
    milliseconds = payload.get("duration_ms") if not is_codex_payload(payload) else None
    duration = None
    if isinstance(milliseconds, (float, int)) and not isinstance(milliseconds, bool):
        try:
            seconds = float(milliseconds) / 1000
        except OverflowError:
            seconds = math.nan
        if math.isfinite(seconds) and seconds >= 0:
            duration = seconds
    return _complete(session, resolve_hook_agent_id(payload), identifier, duration)


def observe_notifications(payload: dict) -> list[str]:
    """Claudeの背景完了通知を、起動時に保持した所有記録へ対応付ける。"""
    session = payload.get("session_id")
    prompt = payload.get("prompt")
    if not isinstance(session, str) or not session or not isinstance(prompt, str) or is_codex_payload(payload):
        return []
    notices: list[str] = []
    for notification in _NOTIFICATIONS.findall(prompt):
        status = re.search(r"<status>([^<]+)</status>", notification)
        task = re.search(r"<task-id>([^<]+)</task-id>", notification)
        if not status or status[1] not in {"completed", "failed", "cancelled", "killed"} or not task:
            continue
        # task-idと元のtool_use_idの対応は起動応答からだけ得る。
        owned = [
            (actor, identifier)
            for actor, current in read_state(session).get(STATE_KEY, {}).items()
            for identifier, call in current.get("calls", {}).items()
            if call.get("task_id") == task[1] and not call.get("done")
        ]
        for actor, identifier in owned:
            notices.extend(_complete(session, actor, identifier, None))
    return notices


def _complete(session: str, actor: str, identifier: str, duration: float | None) -> list[str]:
    now = time.time()
    result: list[tuple[str, float | None, list[float]]] = []

    def finish(state: dict) -> dict | None:
        current = state.setdefault(STATE_KEY, {}).setdefault(actor, {"calls": {}, "recent": [], "kind": "", "notified": False})
        calls = current["calls"]
        call = calls.setdefault(identifier, {})
        if call.get("done"):
            return None
        started = call.get("started")
        seconds = duration if isinstance(started, (float, int)) else None
        kind = "実行時間"
        if seconds is None:
            seconds = now - started if isinstance(started, (float, int)) and now >= started else None
            kind = "観測経過時間（権限待ち等を含み得る）"
        call.clear()
        call["done"] = True
        slow = seconds is not None and seconds >= 60
        if current["kind"] != kind or not slow:
            current.update({"kind": kind, "recent": [], "notified": False})
        if slow:
            current["recent"] = [*current["recent"], seconds][-3:]
        review = []
        if len(current["recent"]) == 3 and not current["notified"]:
            current["notified"] = True
            review = list(current["recent"])
        result.append((kind, seconds, review))
        return state

    update_state(session, finish)
    notices: list[str] = []
    for kind, seconds, review in result:
        measured = f"{kind}: {seconds:.1f}秒" if seconds is not None else "コマンド時間: 未計測（開始時刻を対応付けられない）"
        # 実行ごとの測定値は操作を求めないため、ユーザーの画面へは写さない。
        notices.append(informational(_notice(measured, tag="notice", subject=f"{actor}:{identifier}")))
        if review:
            notices.append(
                _notice(
                    f"同じ計測種別で60秒以上の完了が3件続いた。{kind}: "
                    + "、".join(f"{value:.1f}秒" for value in review)
                    + f"。合計{sum(review):.1f}秒。",
                    tag="warn",
                    removable_cause=False,
                    subject=f"{actor}:{identifier}:review",
                    fix=("`agent-toolkit/share/agent-conduct-details.md`「同種の長時間の完了が続いた場合」に従え。"),
                )
            )
    return notices
