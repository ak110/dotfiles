"""実モデルの系列と段の比較を検証する。"""

import pytest

from agent_toolkit._common import model_tiers


@pytest.mark.parametrize(
    "model,tier",
    [
        ("claude-fable-5-5", 3),
        ("gpt-6-astra", 3),
        ("claude-opus-5-5", 2),
        ("gpt-6.1-sol", 2),
        ("claude-sonnet-5", 1),
        ("gpt-6-terra", 1),
        ("claude-haiku-4-5", 0),
        ("gpt-6-luna", 0),
        ("unknown", None),
    ],
)
def test_model_tier(model, tier):
    assert model_tiers.model_tier(model) == tier


@pytest.mark.parametrize(
    "parent,child,expected",
    [
        ("claude-fable-5", "claude-opus-5", True),
        ("gpt-6-sol", "gpt-6-sol", False),
        ("gpt-6-luna", "gpt-6-astra", False),
        ("unknown", "gpt-6-luna", False),
        ("gpt-6-astra", "unknown", False),
    ],
)
def test_higher_model(parent, child, expected):
    assert model_tiers.is_higher_model(parent, child) is expected
