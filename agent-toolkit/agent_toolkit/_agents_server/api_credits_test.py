"""agent-toolkit/agent_toolkit/_agents_server/api_credits.py のテスト。"""

import pytest

from agent_toolkit._agents_server import api_credits
from agent_toolkit._atk import claude_connection

_CREDITS = claude_connection.Connection("credits", "credits.env", "credits.env")
_SUBSCRIPTION = claude_connection.Connection("subscription", isolation_env_file="credits.env")
_TTL = api_credits.CREDIT_SHORTAGE_TTL_SECONDS


def _kind(connection: claude_connection.Connection) -> str:
    return connection.kind


def test_connections_keep_order_without_observation() -> None:
    assert api_credits.order((_CREDITS, _SUBSCRIPTION), _kind, engine="claude", now=0.0) == (_CREDITS, _SUBSCRIPTION)


@pytest.mark.parametrize(("elapsed", "reordered"), [(0.0, True), (_TTL - 1.0, True), (_TTL, False), (_TTL + 1.0, False)])
def test_credits_move_behind_subscription_only_within_ttl(elapsed: float, reordered: bool) -> None:
    """観測から一定時間内はcreditsを後ろへ回して残し、期限を過ぎたら通常の順序へ戻す。"""
    api_credits.record_shortage("claude", now=1000.0)
    ordered = api_credits.order((_CREDITS, _SUBSCRIPTION), _kind, engine="claude", now=1000.0 + elapsed)
    assert ordered == ((_SUBSCRIPTION, _CREDITS) if reordered else (_CREDITS, _SUBSCRIPTION))


def test_observation_is_kept_per_engine() -> None:
    """一方のエンジンの残高不足は、他方のエンジンの使用順を変えない。"""
    api_credits.record_shortage("codex", now=0.0)
    assert api_credits.shortage_active("codex", now=1.0)
    assert not api_credits.shortage_active("claude", now=1.0)
    providers = (("credits", "credit-provider"), ("subscription", "openai"))
    assert api_credits.order(providers, lambda item: item[0], engine="codex", now=1.0) == providers[::-1]


def test_clear_forgets_observation() -> None:
    api_credits.record_shortage("claude", now=0.0)
    api_credits.clear()
    assert not api_credits.shortage_active("claude", now=1.0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Credit balance is too low to access the Anthropic API", True),
        ("{'type': 'billing_error'}", True),
        ("Insufficient credits", True),
        ("Claude usage limit reached", False),
        ("rate_limit_error", False),
    ],
)
def test_credit_shortage_detection_excludes_usage_limits(text: str, expected: bool) -> None:
    assert api_credits.is_credit_shortage(text) is expected
