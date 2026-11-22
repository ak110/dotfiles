"""Claudeの累積額と子孫の重複、応答別単価、未知モデルを費用の外部結果で検証する。"""

from typing import Any

import pytest

from agent_toolkit._common import model_pricing, session_cost


def _claude(identity: str, model: str, usage: dict[str, Any], *, timestamp: str = "2026-10-20T01:00:00Z") -> dict[str, Any]:
    return {"type": "assistant", "timestamp": timestamp, "message": {"id": identity, "model": model, "usage": usage}}


def _state(amount: float, timestamp: str = "2026-10-20T02:00:00Z") -> dict[str, Any]:
    return {"type": "cost-state", "timestamp": timestamp, "totalCostUSD": amount}


def test_latest_claude_cost_state_covers_native_children_and_keeps_other_processes() -> None:
    root = session_cost.CostRecord(
        "root",
        "claude",
        [
            _claude("old", "unknown-model", {"input_tokens": 99, "output_tokens": 11}),
            _state(3),
            _state(7.25),
            _claude(
                "new", "claude-opus-5-5", {"input_tokens": 1_000_000, "output_tokens": 0}, timestamp="2026-10-20T03:00:00Z"
            ),
        ],
    )
    native = session_cost.CostRecord(
        "native",
        "claude",
        [
            _claude("sub-old", "unknown-model", {"input_tokens": 1000, "output_tokens": 100}),
        ],
        "subagent",
        "root",
    )
    delegate = session_cost.CostRecord("delegate", "claude", [_state(2.5)], parent_id="root")
    delegated_native = session_cost.CostRecord(
        "delegated-native",
        "claude",
        [
            _claude("other-old", "claude-opus-5-5", {"input_tokens": 10000, "output_tokens": 1000}),
        ],
        "subagent",
        "delegate",
    )
    result = session_cost.estimate_family([root, native, delegate, delegated_native, native])
    assert result["estimated_usd"] == pytest.approx(13.75)
    assert result["unknown_responses"] == 0
    assert result["breakdown"]["self"]["known_usd"] == pytest.approx(11.25)
    assert result["breakdown"]["claude_subagents"]["known_usd"] == 0
    assert result["breakdown"]["delegates"]["known_usd"] == pytest.approx(2.5)


def test_cost_state_only_parent_equals_reported_amount() -> None:
    root = session_cost.CostRecord("root", "claude", [_state(1), _state(9.125)])
    child = session_cost.CostRecord(
        "sub",
        "claude",
        [
            _claude("message", "claude-opus-5-5", {"input_tokens": 1000, "output_tokens": 100}),
        ],
        "subagent",
        "root",
    )
    result = session_cost.estimate_family([root, child])
    assert result["estimated_usd"] == 9.125
    assert result["usage_estimate_usd"] == 0


def test_claude_latest_message_usage_and_cache_ttl() -> None:
    entries = [
        _claude("m", "claude-opus-5-5[1m]", {"input_tokens": 100, "output_tokens": 10}),
        _claude(
            "m",
            "claude-opus-5-5[1m]",
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_creation_input_tokens": 30,
                "cache_read_input_tokens": 50,
                "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20},
            },
        ),
    ]
    entries.append(
        {**_claude("request-copy", "claude-opus-5-5", {"input_tokens": 1000, "output_tokens": 10}), "type": "api-request-blob"}
    )
    result = session_cost.estimate_record(session_cost.CostRecord("root", "claude", entries)).to_json()
    assert result["estimated_usd"] == pytest.approx((100 * 4 + 20 * 20 + 10 * 5 + 20 * 8 + 50 * 0.2) / 1_000_000)


def test_codex_response_model_and_duplicate_token_notifications() -> None:
    sample = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 600,
                    "cache_write_input_tokens": 30,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 20,
                    "total_tokens": 1050,
                },
                "total_token_usage": {"total_tokens": 1050},
            },
        },
    }
    entries = [
        {"type": "turn_context", "payload": {"model": "gpt-6-sol"}},
        sample,
        sample,
        {"type": "turn_context", "payload": {"model": "gpt-6-luna"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"input_tokens": 10, "output_tokens": 20},
                    "total_token_usage": {"total_tokens": 30},
                },
            },
        },
    ]
    result = session_cost.estimate_record(session_cost.CostRecord("root", "codex", entries)).to_json()
    assert result["estimated_usd"] == pytest.approx(
        (400 * 2 + 600 * 0.2 + 30 * 2.5 + 50 * 10 + 10 * 0.1 + 20 * 0.5) / 1_000_000
    )


@pytest.mark.parametrize(
    ("model", "count", "expected"),
    [
        ("gpt-6.1-sol", 272_000, 0.544),
        ("gpt-6.1-sol", 272_001, 1.088004),
        ("claude-haiku-5-5", 100_000, 0.01),
        ("claude-haiku-5-5", 100_001, 0.0500005),
    ],
)
def test_price_brackets_follow_each_response_input(model: str, count: int, expected: float) -> None:
    assert model_pricing.response_price(model, {"input_tokens": count}) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("uncached", "expected"),
    [(200_000, (400_000 + 50_000 + 5200 + 1000) / 1e6), (200_001, (800_004 + 100_000 + 10_400 + 1500) / 1e6)],
)
def test_sol_62_prices_full_request_at_context_boundary(uncached: int, expected: float) -> None:
    tokens = {
        "input_tokens": uncached,
        "cache_creation_input_tokens": 20_000,
        "cache_read_input_tokens": 52_000,
        "output_tokens": 100,
    }
    assert model_pricing.response_price("gpt-6.2-sol", tokens) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("model", "count", "short_write", "expected"),
    [
        ("claude-fable-5-2", 1_000_000, 0, 8 + 16 + 0.20 + 40),
        ("claude-fable-5-2", 1_000_000, 1_000_000, 8 + 10 + 0.20 + 40),
        ("claude-mythos-5-2", 1_000_000, 0, 8 + 16 + 0.20 + 40),
        ("gpt-6.1-terra", 50_000, 0, (1 + 1.25 + 0.05 + 5) * 0.05),
        ("gpt-6.1-terra", 1_000_000, 0, 2 + 2.5 + 0.10 + 7.50),
    ],
)
def test_added_models_use_published_rates(model: str, count: int, short_write: int, expected: float) -> None:
    """公式の料金表にある4区分の単価を、同数のトークンへ適用した金額と比べる。"""
    tokens: dict[str, int] = {name: count for name in model_pricing.TOKEN_COMPONENTS}
    assert model_pricing.response_price(model, tokens, short_write_tokens=short_write) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("engine", "entries", "input_tokens", "output_tokens"),
    [
        ("claude", [_claude("m", "future-model", {"input_tokens": 20, "output_tokens": 10})], 20, 10),
        (
            "agy",
            [
                {
                    "event": "step_update",
                    "step_update": {"step_type": "agent_response", "usage": {"input_tokens": 20, "output_tokens": 10}},
                }
            ],
            20,
            10,
        ),
        ("opencode", [{"type": "step_finish", "part": {"cost": 0, "tokens": {"input": 20, "output": 10}}}], 20, 10),
    ],
)
def test_unknown_model_is_not_zero_cost(
    engine: str, entries: list[dict[str, Any]], input_tokens: int, output_tokens: int
) -> None:
    result = session_cost.estimate_family([session_cost.CostRecord("root", engine, entries)])
    assert result["estimated_usd"] is None
    assert result["unknown_responses"] == 1
    assert result["unknown_tokens"]["input_tokens"] == input_tokens
    assert result["unknown_tokens"]["output_tokens"] == output_tokens
    assert result["pricing"]["as_of"] == model_pricing.PRICE_DATE
    assert result["pricing"]["sources"]


def test_unknown_model_counts_include_children_once() -> None:
    root = session_cost.CostRecord("root", "claude", [_claude("1", "future-sol", {"input_tokens": 1})])
    child = session_cost.CostRecord(
        "child",
        "claude",
        [_claude("2", "future-sol", {"input_tokens": 2}), _claude("3", "other", {"output_tokens": 1})],
        "subagent",
        "root",
    )
    cost = session_cost.estimate_family([root, child, child])
    assert cost["unknown_responses"] == 3
    assert cost["unknown_models"] == {"future-sol": 2, "other": 1}
    assert cost["breakdown"]["self"]["unknown_models"] == {"future-sol": 1}
    assert cost["breakdown"]["claude_subagents"]["unknown_models"] == {"future-sol": 1, "other": 1}


def test_incomplete_family_keeps_known_subtotal() -> None:
    result = session_cost.estimate_family([session_cost.CostRecord("root", "claude", [_state(4.5)])], incomplete=True)
    assert result["estimated_usd"] is None
    assert result["known_usd"] == 4.5
    assert result["incomplete"]
