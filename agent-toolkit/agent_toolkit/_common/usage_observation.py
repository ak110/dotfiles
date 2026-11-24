from __future__ import annotations

import json
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass, replace
from typing import Any

from agent_toolkit._common import file_lock
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.state_paths import state_dir

_PRODUCER = uuid.uuid4().hex


@dataclass(frozen=True)
class QuotaWindow:
    engine: str
    account: str
    window_id: str
    used_percent: float | None
    resets_at: float | None
    observed_at: float
    scope: str = "all"
    contract: str = ""
    duration_seconds: float | None = None
    rejected: bool = False


def rate_key(window: QuotaWindow) -> str:
    duration = float(window.duration_seconds) if window.duration_seconds is not None else None
    return json.dumps([window.engine, window.account, window.window_id, window.scope, window.contract, duration])


def _valid(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _read() -> dict[str, Any]:
    path = state_dir() / "usage-observations.json"
    if not path.exists():
        return {"observations": [], "latest": {}, "mixed": False}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("observations"), list)
        or not isinstance(value.get("latest"), dict)
    ):
        raise ValueError("利用枠の観測保存形式が不正")
    return value


def observe(windows: list[QuotaWindow], *, setting_signature: str, active_seconds: float, mixed: bool = False) -> None:
    if len(windows) > 10000:
        raise ValueError("1回の利用枠の観測は10000窓まで")
    if not _valid(active_seconds) or active_seconds < 0:
        raise ValueError("累積活動秒は0以上の有限値で指定する")
    root = state_dir()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "usage-observations.lock").open("a+b") as handle:
        file_lock.acquire_lock(handle)
        try:
            data = _read()
            seen = {tuple(row["identity"]) for row in data["observations"]}
            producer = f"{os.getpid()}:{_PRODUCER}"
            activity_deltas: dict[str, float | None] = {}
            for window in windows:
                if window.engine not in {"claude", "codex"}:
                    raise ValueError("利用枠を観測するengineはclaudeかcodexを指定する")
                if not _valid(window.observed_at):
                    continue
                window = replace(
                    window,
                    used_percent=window.used_percent
                    if window.used_percent is not None and _valid(window.used_percent) and 0 <= window.used_percent <= 100
                    else None,
                    resets_at=window.resets_at if _valid(window.resets_at) else None,
                )
                identity = (window.engine, window.account, window.window_id, window.scope, window.observed_at)
                key = json.dumps(identity[:4])
                old = data["latest"].get(key)
                if identity in seen or (old and window.observed_at <= old["window"]["observed_at"]):
                    continue
                if window.engine not in activity_deltas:
                    latest_activity = data.setdefault("activity_totals", {}).get(window.engine, {})
                    previous_total = latest_activity.get("total")
                    activity_deltas[window.engine] = (
                        active_seconds - previous_total
                        if latest_activity.get("producer") == producer
                        and previous_total is not None
                        and _valid(previous_total)
                        and _valid(active_seconds)
                        and active_seconds >= previous_total
                        else None
                    )
                    data["activity_totals"][window.engine] = {"producer": producer, "total": active_seconds}
                old_total = old.get("activity_total") if old else None
                interval = (
                    active_seconds - old_total
                    if old
                    and old.get("producer") == producer
                    and old_total is not None
                    and _valid(old_total)
                    and _valid(active_seconds)
                    and active_seconds >= old_total
                    else None
                )
                rate = None
                if old:
                    prior = old["window"]
                    if (
                        prior["contract"] == window.contract
                        and prior.get("duration_seconds") == window.duration_seconds
                        and old["setting"] == setting_signature
                        and prior["resets_at"] == window.resets_at
                        and window.resets_at is not None
                        and prior["used_percent"] is not None
                        and window.used_percent is not None
                        and window.used_percent >= prior["used_percent"]
                        and interval is not None
                        and interval > 0
                    ):
                        rate = (window.used_percent - prior["used_percent"]) / interval
                row = {
                    "identity": identity,
                    "window": asdict(window),
                    "setting": setting_signature,
                    "rate": rate,
                    "mixed": mixed,
                    "active_seconds": activity_deltas[window.engine],
                    "activity_total": active_seconds,
                    "producer": producer,
                }
                data["latest"][key] = row
                data["observations"].append(row)
                seen.add(identity)
            cutoff = time.time() - 30 * 86400
            data["observations"] = [row for row in data["observations"] if row["window"]["observed_at"] >= cutoff][-10000:]
            newest: dict[str, dict[str, Any]] = {}
            for row in data["latest"].values():
                window = row["window"]
                previous = newest.get(window["engine"])
                if previous is None or previous["observed_at"] < window["observed_at"]:
                    newest[window["engine"]] = window
            data["latest"] = {
                key: row
                for key, row in data["latest"].items()
                if (row["window"]["account"], row["window"]["contract"])
                == (newest[row["window"]["engine"]]["account"], newest[row["window"]["engine"]]["contract"])
                or row["window"]["observed_at"] >= cutoff
            }
            if len(data["latest"]) > 10000:
                raise ValueError("利用枠の最終状態が10000窓を超えたため、保存せず取得不明として扱う")
            data["mixed"] = mixed
            atomic_write(root / "usage-observations.json", json.dumps(data, ensure_ascii=False, allow_nan=False))
        finally:
            file_lock.release_lock(handle)


def snapshot(*, now: float | None = None) -> dict[str, Any]:
    if now is None:
        now = time.time()
    data = _read()
    rows = list(data["latest"].values())
    current: dict[str, dict[str, Any]] = {}
    for row in rows:
        window = row["window"]
        previous = current.get(window["engine"])
        if previous is None or previous["observed_at"] < window["observed_at"]:
            current[window["engine"]] = window
    rates: dict[str, float] = {}
    setting_rates: dict[str, dict[str, float]] = {}
    for row in rows:
        window = row["window"]
        if row["rate"] is not None and now - window["observed_at"] <= 600:
            key = rate_key(QuotaWindow(**window))
            rates[key] = row["rate"]

    latest = {rate_key(QuotaWindow(**row["window"])): row["window"] for row in rows}
    previous_windows: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in sorted(data["observations"], key=lambda item: item["window"]["observed_at"]):
        window = row["window"]
        key = rate_key(QuotaWindow(**window))
        identity = tuple(window[field] for field in ("engine", "account", "window_id", "scope"))
        prior = previous_windows.get(identity)
        if prior is not None and (
            rate_key(QuotaWindow(**prior)) != key
            or prior["resets_at"] != window["resets_at"]
            or (
                prior["used_percent"] is not None
                and window["used_percent"] is not None
                and window["used_percent"] < prior["used_percent"]
            )
        ):
            for setting in setting_rates.values():
                setting.pop(rate_key(QuotaWindow(**prior)), None)
        previous_windows[identity] = window
        current_window = latest.get(key)
        if (
            row["rate"] is not None
            and now - window["observed_at"] <= 600
            and current_window is not None
            and window["resets_at"] == current_window["resets_at"]
            and current_window["used_percent"] is not None
            and window["used_percent"] <= current_window["used_percent"]
        ):
            setting_rates.setdefault(row["setting"], {})[key] = row["rate"]
    return {
        "windows": [row["window"] for row in rows],
        "current_windows": [
            row["window"]
            for row in rows
            if (row["window"]["account"], row["window"]["contract"])
            == (current[row["window"]["engine"]]["account"], current[row["window"]["engine"]]["contract"])
        ],
        "rates": rates,
        "setting_rates": {setting: values for setting, values in setting_rates.items() if values},
        "mixed": data["mixed"],
        "activity_ratio": _codex_activity_ratio(data["observations"], now),
        "last_observed_at": max((row["window"]["observed_at"] for row in rows), default=None),
    }


def _codex_activity_ratio(observations: list[dict[str, Any]], now: float) -> float | None:
    samples = {
        row["window"]["observed_at"]: row
        for row in observations
        if row["window"]["engine"] == "codex" and now - 7 * 86400 <= row["window"]["observed_at"] <= now
    }
    times = sorted(samples, reverse=True)
    if not times or now - times[0] > 600:
        return None
    latest = samples[times[0]]
    active = 0.0
    intervals = 0
    span = 0.0
    for newer, older in zip(times, times[1:], strict=False):
        current, prior = samples[newer], samples[older]
        delta = current.get("active_seconds")
        if (
            delta is None
            or not _valid(delta)
            or not 0 <= delta <= newer - older
            or not (
                latest.get("producer")
                and prior.get("producer") == latest["producer"]
                and prior["window"]["account"] == latest["window"]["account"]
                and prior["window"]["contract"] == latest["window"]["contract"]
            )
        ):
            break
        active += delta
        intervals += 1
        span = times[0] - older
    # 単発のバーストや瞬間的な休止で予測が変わらないよう、2区間で累計30分以上を求める。  # pyfltr: allow-comment
    return active / span if intervals >= 2 and span >= 1800 and active > 0 else None
