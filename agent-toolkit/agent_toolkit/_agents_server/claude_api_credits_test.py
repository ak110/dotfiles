"""agent-toolkit/agent_toolkit/_agents_server/claude_api_credits.py のテスト。"""

import pytest

from agent_toolkit._agents_server import claude_api_credits
from agent_toolkit._atk import claude_connection

_API = claude_connection.Connection("api", "api.env", "api.env")
_SUBSCRIPTION = claude_connection.Connection("subscription", isolation_env_file="api.env")
_TTL = claude_api_credits.CREDIT_SHORTAGE_TTL_SECONDS


def test_connections_keep_order_without_observation() -> None:
    assert claude_api_credits.order_connections((_API, _SUBSCRIPTION), now=0.0) == (_API, _SUBSCRIPTION)


@pytest.mark.parametrize(("elapsed", "reordered"), [(0.0, True), (_TTL - 1.0, True), (_TTL, False), (_TTL + 1.0, False)])
def test_api_moves_behind_subscription_only_within_ttl(elapsed: float, reordered: bool) -> None:
    """観測から一定時間内はAPIを後ろへ回して残し、期限を過ぎたら通常の順序へ戻す。"""
    claude_api_credits.record_shortage(now=1000.0)
    ordered = claude_api_credits.order_connections((_API, _SUBSCRIPTION), now=1000.0 + elapsed)
    assert ordered == ((_SUBSCRIPTION, _API) if reordered else (_API, _SUBSCRIPTION))


def test_clear_forgets_observation() -> None:
    claude_api_credits.record_shortage(now=0.0)
    claude_api_credits.clear()
    assert not claude_api_credits.shortage_active(now=1.0)


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
    assert claude_api_credits.is_credit_shortage(text) is expected
