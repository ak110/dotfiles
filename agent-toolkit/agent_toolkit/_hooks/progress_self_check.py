"""長時間の作業で、進捗と進め方の点検を一定間隔でエージェント自身へ促す。

PostToolUseとUserPromptSubmitの両フックから呼ばれ、ClaudeとCodex（Antigravityはアダプター経由）の
メインと委譲先の全セッションを対象とする。委譲先のレビューや修正の往復が長時間続いても、
ユーザーが介入するまで誰も時間と進捗を見比べない事象を防ぐ。

状態はセッション状態ファイルの`progress_self_check`へ、呼出主体（メインは`main`、
in-processのサブエージェントはpayloadの`agent_id`）ごとに次の値をepoch秒で保持する。

- `started_at`: その主体を最初に観測した時刻。セッション開始時刻として経過時間の起点に使う
- `last_notified_at`: 通知が最終出力へ採用された最後の時刻。最初の観測では`started_at`と同じ値にする
- `reserved_at`: 期日に達して通知を生成した時刻。並列なフックが同じ通知を重ねないための予約に使う

最初の観測では記録だけを行い通知しない。前回の通知から`SELF_CHECK_INTERVAL_SECONDS`以上たった
観測で通知を1回返し、予約を記録する。`last_notified_at`は通知本文がホストへの最終出力へ採用された時点で
`notice.record_delivery`が記録する時刻から進める。通知を生成しただけの時点で期日を消費すると、
未採用・整形失敗・最終出力の作成失敗のときに通知が届かないまま次の期日まで遅れるためである。
予約は`RESERVATION_SECONDS`で失効し、hook処理の終了時に未採用のまま残った予約は`release_reservations`が解除する。
古い試行の配送記録は、より新しい確定時刻を巻き戻さない（確定時刻は最大値で扱う）。
実ユーザーの発話の有無や無操作期間の長さによらず、前回通知を起点に判定する。
フックは最初のツール実行か発話で初めて観測するため、`started_at`はセッション開始よりわずかに遅れる。
"""

from __future__ import annotations

import os
import shlex
import time

from agent_toolkit._common.session_state import update_state
from agent_toolkit._hooks import notice as _notice
from agent_toolkit._hooks import plugin_resources
from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter

SELF_CHECK_INTERVAL_SECONDS = 30 * 60
"""点検を通知する間隔（秒）。"""

STATE_KEY = "progress_self_check"
"""セッション状態で呼出主体ごとの時刻を保持するキー。"""

SOURCE = "progress-self-check"
"""通知の生成元として`atk-auto`要素の`source`属性へ入れる名前。"""

_llm_notice = _notice_formatter(SOURCE)

DELIVERED_KEY = "progress_self_check_delivered"
"""最終出力へ採用された通知の生成時刻を、呼出主体ごとに受け取るセッション状態のキー。"""

RESERVATION_SECONDS = 60
"""期日到達後の予約が並列claimを抑止する秒数。同じ時点に並列で動くフックの処理時間より十分長い値にする。"""

_STARTED_AT_KEY = "started_at"
_LAST_NOTIFIED_AT_KEY = "last_notified_at"
_RESERVED_AT_KEY = "reserved_at"

_reservations: list[tuple[str, str, float]] = []

_SELF_CHECK_NOTICE_BODY = "進捗点検を行え。手順は`{read_command}`で取得する`agent-conduct-details.md`「進捗点検」節に従え。"


def _format_elapsed(seconds: float) -> str:
    """経過秒数を「N分」か「N時間M分」の表記へ変える。"""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}分"
    hours, rest = divmod(minutes, 60)
    return f"{hours}時間{rest}分" if rest else f"{hours}時間"


def _number(value: object) -> float | None:
    """状態に保存した時刻を数値として返す。数値でなければ`None`を返す。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _delivered_at(state: dict, agent_id: str) -> float | None:
    """配送記録が示す最新の採用時刻を返す。"""
    delivered = state.get(DELIVERED_KEY)
    values = delivered.get(agent_id) if isinstance(delivered, dict) else None
    times = [number for value in values if (number := _number(_parse(value))) is not None] if isinstance(values, list) else []
    return max(times) if times else None


def _parse(value: object) -> object:
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return value


def claim_notice(payload: dict, *, now: float | None = None) -> str | None:
    """点検の期日に達していれば通知本文を返し、配送時に通知時刻を確定する予約をする。達していなければ`None`を返す。

    状態の読み書きは`update_state`の排他区間で行い、同じ主体の並列なフックが同じ通知を重ねて返さないようにする。
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    agent_id = resolve_hook_agent_id(payload)
    current_time = time.time() if now is None else now
    elapsed: float | None = None

    def _mutate(state: dict) -> dict | None:
        nonlocal elapsed
        entries = state.get(STATE_KEY)
        entries = dict(entries) if isinstance(entries, dict) else {}
        entry = entries.get(agent_id)
        entry = dict(entry) if isinstance(entry, dict) else {}
        started_at = _number(entry.get(_STARTED_AT_KEY))
        if started_at is None:
            entries[agent_id] = {_STARTED_AT_KEY: current_time, _LAST_NOTIFIED_AT_KEY: current_time}
            state[STATE_KEY] = entries
            return state
        candidates = [
            value for value in (_number(entry.get(_LAST_NOTIFIED_AT_KEY)), _delivered_at(state, agent_id)) if value is not None
        ]
        confirmed = max(candidates) if candidates else started_at
        delivered = state.get(DELIVERED_KEY)
        if isinstance(delivered, dict) and agent_id in delivered:
            # 採用記録を確定時刻へ畳み込み、記録が入力ごとに増えないようにする。
            state[DELIVERED_KEY] = {key: value for key, value in delivered.items() if key != agent_id}
            entry[_LAST_NOTIFIED_AT_KEY] = confirmed
        reserved_at = _number(entry.get(_RESERVED_AT_KEY))
        due = current_time - confirmed >= SELF_CHECK_INTERVAL_SECONDS
        reserved = reserved_at is not None and reserved_at > confirmed and current_time - reserved_at < RESERVATION_SECONDS
        if due and not reserved:
            elapsed = current_time - started_at
            entry[_RESERVED_AT_KEY] = current_time
        elif not (isinstance(delivered, dict) and agent_id in delivered):
            return None
        entries[agent_id] = entry
        state[STATE_KEY] = entries
        return state

    if not update_state(session_id, _mutate) or elapsed is None:
        return None
    # 整形や後続の出力が例外で終わっても終了時に予約を外せるよう、整形より先に解除の対象へ登録する。
    _reservations.append((session_id, agent_id, current_time))
    path = str(plugin_resources.plugin_root() / "share/agent-conduct-details.md")
    read_command = (
        "Get-Content -LiteralPath '" + path.replace("'", "''") + "' -Encoding utf8"
        if os.name == "nt"
        else "cat -- " + shlex.quote(path)
    )
    body = f"セッション開始から{_format_elapsed(elapsed)}たった。" + _SELF_CHECK_NOTICE_BODY.format(read_command=read_command)
    text = _llm_notice(body, tag="notice")
    if not text:
        return None
    _notice.stage_delivery(text, session_id, DELIVERED_KEY, agent_id, repr(current_time))
    return text


def release_reservations() -> None:
    """hook処理の終了時に、未採用のまま残った予約を解除する。

    採用された通知は配送記録で確定時刻が進むため、予約を解除しても同じ期日で再通知しない。
    未採用の通知は予約だけが残るため、解除して直後の正常な呼出しで改めて通知できるようにする。
    """
    pending = list(_reservations)
    _reservations.clear()
    for session_id, agent_id, reserved_at in pending:

        def _release(state: dict, agent_id: str = agent_id, reserved_at: float = reserved_at) -> dict | None:
            current = state.get(STATE_KEY)
            if not isinstance(current, dict):
                return None
            entry = current.get(agent_id)
            if not isinstance(entry, dict) or _number(entry.get(_RESERVED_AT_KEY)) != reserved_at:
                return None
            entries = dict(current)
            entries[agent_id] = {key: value for key, value in entry.items() if key != _RESERVED_AT_KEY}
            state[STATE_KEY] = entries
            return state

        update_state(session_id, _release)
