from __future__ import annotations

import math

from agent_toolkit._agents_server import state


def valid_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if value >= 0 and math.isfinite(value) else None
    except OverflowError:
        return None


def fields(tokens: object, context_window: object) -> dict[str, int | float]:
    values = {"context_tokens": valid_number(tokens), "context_window": valid_number(context_window)}
    return {key: value for key, value in values.items() if value is not None}


def record_context_usage(session: state.SessionState, tokens: object, context_window: object) -> None:
    session.context_tokens = valid_number(tokens)
    session.context_window = valid_number(context_window)
    state.notify_touch_listeners(session)


def format_tokens(tokens: object, context_window: object) -> str | None:
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
