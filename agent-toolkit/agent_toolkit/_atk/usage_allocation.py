"""観測した自然回復までの余裕に応じて候補のengine順を選ぶ。"""

from __future__ import annotations

import json
import math
import re
import time

from agent_toolkit._common.usage_observation import QuotaWindow, rate_key


def _applies(window: QuotaWindow, candidates: list[tuple[str, str, str]], role: str) -> bool:
    if window.scope in {"all", role}:
        return True
    if window.scope.startswith("model:"):
        return any(
            engine == window.engine and window.scope[6:].casefold() in model.casefold() for engine, model, _effort in candidates
        )
    try:
        scope = json.loads(window.scope)
    except ValueError:
        return False
    if not isinstance(scope, dict):
        return False
    model = scope.get("model")
    if isinstance(model, dict) and isinstance(model.get("display_name"), str):
        label = set(re.findall(r"[a-z]+", model["display_name"].casefold())) - {"claude", "model"}
        return any(
            engine == window.engine and label.intersection(re.findall(r"[a-z]+", candidate.casefold()))
            for engine, candidate, _effort in candidates
        )
    surface = scope.get("surface")
    return isinstance(surface, dict) and "code" in str(surface.get("display_name", "")).casefold()


def choose(
    candidates: list[tuple[str, str, str]],
    *,
    role: str,
    windows: list[QuotaWindow],
    rates: dict[str, float],
    activity_ratio: float = 1.0,
    previous: str | None = None,
) -> tuple[list[tuple[str, str, str]], str]:
    if not candidates:
        raise ValueError("利用できる候補がない")
    if role == "orchestrate":
        return list(candidates), "統括は保存された候補を維持し、通常の上限では同じ会話で回復を待つ"
    now = time.time()
    status = {}
    for engine in ("codex", "claude"):
        applicable = [w for w in windows if w.engine == engine and _applies(w, candidates, role)]
        margins = []
        pressure = ""
        unknown = not applicable
        for window in applicable:
            rate = rates.get(rate_key(window))
            used = window.used_percent
            if used is None or not math.isfinite(used) or not 0 <= used <= 100:
                unknown = True
                continue
            if engine == "claude" and used >= 90:
                pressure = f"{window.window_id}の使用率{used:g}%が90%以上"
            if (
                window.resets_at is None
                or not math.isfinite(window.resets_at)
                or rate is None
                or not math.isfinite(rate)
                or rate < 0
                or not math.isfinite(activity_ratio)
                or not 0 <= activity_ratio <= 1
            ):
                unknown = True
                continue
            demand = max(0, window.resets_at - now) * rate * activity_ratio
            margins.append((100 - used, demand))
            if demand > 100 - used:
                pressure = f"{window.window_id}の回復までの推定需要{demand:g}%が残量{100 - used:g}%を超過"
        status[engine] = (pressure, not unknown and bool(margins) and all(left >= demand * 1.2 for left, demand in margins))
    claude_pressure, claude_room = status["claude"]
    codex_pressure, codex_room = status["codex"]
    result = list(candidates)
    if claude_pressure:
        result.sort(key=lambda candidate: candidate[0] == "claude")
    preferred = "claude" if codex_pressure and claude_room and not claude_pressure else "codex"
    if (
        previous in status
        and any(candidate[0] == previous for candidate in result)
        and previous != preferred
        and not status[preferred][1]
        and not status[previous][0]
    ):
        preferred = previous
    unavailable = not any(candidate[0] == preferred for candidate in result)
    if unavailable:
        preferred = result[0][0]
    result.sort(key=lambda candidate: candidate[0] != preferred)
    if claude_pressure and any(candidate[0] == "claude" for candidate in candidates):
        action = "他の候補を先に試す" if preferred != "claude" else "Claudeだけが設定されているためそのまま試す"
        return result, f"Claudeの利用枠がひっ迫（{claude_pressure}）。{action}"
    reason = f"{preferred}優先"
    if unavailable:
        reason += "（推奨engineのモデル候補が未設定）"
    if not codex_room and not claude_room:
        reason += "（余裕不明または不足）"
    return result, reason
