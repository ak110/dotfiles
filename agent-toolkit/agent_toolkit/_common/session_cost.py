from __future__ import annotations

import collections
import dataclasses
import datetime
import math
from typing import Any

from agent_toolkit._common import model_pricing
from agent_toolkit._common.agy_records import usage_tokens


def _number(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _timestamp(value: Any) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo is not None else result.replace(tzinfo=datetime.UTC)


@dataclasses.dataclass(frozen=True)
class CostRecord:
    record_id: str
    engine: str
    entries: list[dict[str, Any]]
    role: str = "session"
    parent_id: str | None = None


@dataclasses.dataclass
class CostEstimate:
    reported_usd: float = 0
    usage_estimate_usd: float = 0
    unknown_models: collections.Counter[str] = dataclasses.field(default_factory=collections.Counter)
    unknown_tokens: dict[str, int] = dataclasses.field(default_factory=lambda: dict.fromkeys(model_pricing.TOKEN_COMPONENTS, 0))
    incomplete: bool = False

    def add(self, other: CostEstimate) -> None:
        self.reported_usd = math.fsum((self.reported_usd, other.reported_usd))
        self.usage_estimate_usd = math.fsum((self.usage_estimate_usd, other.usage_estimate_usd))
        self.unknown_models.update(other.unknown_models)
        self.incomplete |= other.incomplete
        for key, value in other.unknown_tokens.items():
            self.unknown_tokens[key] += value

    def to_json(self) -> dict[str, Any]:
        known = math.fsum((self.reported_usd, self.usage_estimate_usd))
        return {
            "estimated_usd": known if not self.unknown_models and not self.incomplete else None,
            "known_usd": known,
            "reported_usd": self.reported_usd,
            "usage_estimate_usd": self.usage_estimate_usd,
            "unknown_responses": self.unknown_models.total(),
            "unknown_models": dict(self.unknown_models),
            "unknown_tokens": dict(self.unknown_tokens),
            "incomplete": self.incomplete,
        }


@dataclasses.dataclass(frozen=True)
class _Snapshot:
    line: int
    amount: float
    timestamp: datetime.datetime | None


def _snapshot(entries: list[dict[str, Any]]) -> _Snapshot | None:
    latest = None
    for line, entry in enumerate(entries):
        value = entry.get("totalCostUSD")
        if (
            entry.get("type") == "cost-state"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        ):
            latest = _Snapshot(line, float(value), _timestamp(entry.get("timestamp")))
    return latest


def _claude_usages(entries: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any], str | None, dict[str, Any]]]:
    latest: dict[str, tuple[int, dict[str, Any], str | None, dict[str, Any]]] = {}
    for line, entry in enumerate(entries):
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            continue
        identity = message.get("id")
        key = f"message:{identity}" if isinstance(identity, str) else f"line:{line}"
        model = message.get("model")
        latest[key] = (line, entry, model if isinstance(model, str) else None, message["usage"])
    return list(latest.values())


def _codex_usages(entries: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any], str | None, dict[str, Any]]]:
    samples = []
    model = None
    previous_total = None
    for line, entry in enumerate(entries):
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        if entry.get("type") == "turn_context":
            value = payload.get("model")
            model = value if isinstance(value, str) else None
        if entry.get("type") != "event_msg" or payload.get("type") != "token_count":
            continue
        info = payload.get("info")
        if not isinstance(info, dict) or not isinstance(info.get("last_token_usage"), dict):
            continue
        total = info.get("total_token_usage")
        if isinstance(total, dict) and "total_tokens" in total:
            if total == previous_total:
                continue
            previous_total = dict(total)
        usage = info["last_token_usage"]
        if not any(_number(value) for value in usage.values()):
            continue
        samples.append(
            (
                line,
                entry,
                model,
                {
                    "input_tokens": max(_number(usage.get("input_tokens")) - _number(usage.get("cached_input_tokens")), 0),
                    "cache_creation_input_tokens": _number(usage.get("cache_write_input_tokens")),
                    "cache_read_input_tokens": _number(usage.get("cached_input_tokens")),
                    "output_tokens": _number(usage.get("output_tokens")),
                },
            )
        )
    return samples


def _other_usages(entries: list[dict[str, Any]], engine: str) -> list[tuple[int, dict[str, Any], str | None, dict[str, Any]]]:
    samples: list[tuple[int, dict[str, Any], str | None, dict[str, Any]]] = []
    for line, entry in enumerate(entries):
        if engine == "agy":
            usage = usage_tokens(entry)
            if usage is not None:
                original = entry.get("_agy_original", entry)
                body = original.get("step_update", original)
                model = body.get("model")
                samples.append((line, entry, model if isinstance(model, str) else None, usage))
            continue
        body = entry.get("part")
        if not isinstance(body, dict):
            continue
        if engine == "opencode" and entry.get("type") == "step_finish" and isinstance(body.get("tokens"), dict):
            usage = body["tokens"]
            cache = usage.get("cache")
            cache = cache if isinstance(cache, dict) else {}
            samples.append(
                (
                    line,
                    entry,
                    None,
                    {
                        "input_tokens": _number(usage.get("input")),
                        "output_tokens": _number(usage.get("output")),
                        "cache_creation_input_tokens": _number(cache.get("write")),
                        "cache_read_input_tokens": _number(cache.get("read")),
                    },
                )
            )
    return samples


def estimate_record(record: CostRecord, *, covered_by: _Snapshot | None = None) -> CostEstimate:
    """Claude累積額を優先し、その後の応答か未収録部分だけを単価で補う。"""
    result = CostEstimate()
    snapshot = _snapshot(record.entries) if record.engine == "claude" and covered_by is None else None
    if snapshot is not None:
        result.reported_usd = snapshot.amount
    samples = (
        _claude_usages(record.entries)
        if record.engine == "claude"
        else _codex_usages(record.entries)
        if record.engine == "codex"
        else _other_usages(record.entries, record.engine)
    )
    for line, entry, model, usage in samples:
        if snapshot is not None and line <= snapshot.line:
            continue
        if covered_by is not None:
            moment = _timestamp(entry.get("timestamp"))
            if moment is None or covered_by.timestamp is None:
                result.incomplete = True
                continue
            if moment <= covered_by.timestamp:
                continue
        tokens = {key: _number(usage.get(key)) for key in model_pricing.TOKEN_COMPONENTS}
        creation = usage.get("cache_creation")
        short_write = _number(creation.get("ephemeral_5m_input_tokens")) if isinstance(creation, dict) else 0
        amount = model_pricing.response_price(model, tokens, short_write_tokens=short_write)
        if amount is None:
            result.unknown_models[model or ""] += 1
            for key, value in tokens.items():
                result.unknown_tokens[key] += value
        else:
            result.usage_estimate_usd += amount
    return result


def estimate_family(records: list[CostRecord], *, incomplete: bool = False) -> dict[str, Any]:
    """先頭をルートとして、各物理記録を1回だけ数え、親のClaude算出額との重複を除く。"""
    if not records:
        raise ValueError("費用集計にはルート記録が必要である")
    by_id = {record.record_id: record for record in records}
    root = records[0]
    groups = {key: CostEstimate() for key in ("self", "claude_subagents", "delegates")}
    for record in by_id.values():
        owner = record
        seen = {owner.record_id}
        while owner.role == "subagent" and owner.parent_id in by_id and owner.parent_id not in seen:
            owner = by_id[owner.parent_id]
            seen.add(owner.record_id)
        covered = _snapshot(owner.entries) if owner is not record and owner.engine == "claude" else None
        group = (
            "self"
            if record.record_id == root.record_id
            else "claude_subagents"
            if owner.record_id == root.record_id and record.role == "subagent"
            else "delegates"
        )
        groups[group].add(estimate_record(record, covered_by=covered))
    total = CostEstimate(incomplete=incomplete)
    for amount in groups.values():
        total.add(amount)
    return {
        **total.to_json(),
        "breakdown": {key: amount.to_json() for key, amount in groups.items()},
        "pricing": model_pricing.pricing_metadata(),
    }
