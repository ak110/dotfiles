"""最新モデル文脈の使用量を検証し、表示用の数値と書式を共有する。"""

from __future__ import annotations

import math

from agent_toolkit._agents_server import state


def valid_number(value: object) -> int | float | None:
    """有限かつ非負の数値だけを返し、未観測・不正値を未知として扱う。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if value >= 0 and math.isfinite(value) else None
    except OverflowError:
        return None


def fields(tokens: object, context_window: object) -> dict[str, int | float]:
    """既知の使用量と生のモデル上限だけを公開する。"""
    values = {"context_tokens": valid_number(tokens), "context_window": valid_number(context_window)}
    return {key: value for key, value in values.items() if value is not None}


def record_context_usage(session: state.SessionState, tokens: object, context_window: object) -> None:
    """観測を置換して表示へ通知し、モデル活動の時刻は維持する。"""
    session.context_tokens = valid_number(tokens)
    session.context_window = valid_number(context_window)
    state.notify_touch_listeners()


def format_tokens(tokens: object, context_window: object) -> str | None:
    """使用量を小数1桁のk、正の上限がある場合は整数の使用率で返す。"""
    count = valid_number(tokens)
    if count is None:
        return None
    text = f"{count / 1000:.1f}k"
    window = valid_number(context_window)
    if window is not None and window > 0:
        percentage = count / window * 100
        if math.isfinite(percentage):
            text += f"/{percentage:.0f}%"
    return text
