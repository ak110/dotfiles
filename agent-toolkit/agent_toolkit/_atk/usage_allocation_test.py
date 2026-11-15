"""候補の保持とひっ迫回避の試験。"""

import pytest

from agent_toolkit._atk import usage_allocation
from agent_toolkit._atk.usage_allocation import choose
from agent_toolkit._common.usage_observation import QuotaWindow, rate_key


def test_unknown_prefers_codex():
    candidates = [("claude", "a", "b"), ("codex", "c", "d")]
    result, reason = choose(candidates, role="execute", windows=[], rates={})
    assert result == candidates[::-1]
    assert "不明" in reason


def test_claude_tight():
    windows = [QuotaWindow("claude", "a", "w", 90, None, 0)]
    with pytest.raises(ValueError, match="ひっ迫"):
        choose([("claude", "a", "b")], role="execute", windows=windows, rates={})
    assert choose([("claude", "a", "b")], role="orchestrate", windows=windows, rates={})[0]


def test_reset_horizon_and_activity_change_decision(monkeypatch):
    monkeypatch.setattr(usage_allocation.time, "time", lambda: 100.0)
    candidates = [("codex", "custom-one", "low"), ("claude", "custom-two", "high")]
    far = [QuotaWindow("codex", "a", "w", 50, 1100, 100), QuotaWindow("claude", "b", "w", 20, 1100, 100)]
    rates = {rate_key(far[0]): 0.1, rate_key(far[1]): 0.01}
    assert choose(candidates, role="execute", windows=far, rates=rates)[0] == candidates[::-1]
    near = [QuotaWindow("codex", "a", "w", 50, 110, 100), far[1]]
    assert choose(candidates, role="execute", windows=near, rates=rates)[0] == candidates
    assert choose(candidates, role="execute", windows=far, rates=rates, activity_ratio=0.1)[0] == candidates
    assert choose(candidates, role="orchestrate", windows=far, rates=rates)[0] == candidates


def test_different_window_percentages_have_separate_paces(monkeypatch):
    monkeypatch.setattr(usage_allocation.time, "time", lambda: 100.0)
    candidates = [("codex", "one", "low"), ("claude", "two", "high")]
    windows = [
        QuotaWindow("codex", "a", "primary", 80, 200, 100),
        QuotaWindow("codex", "a", "weekly", 30, 100100, 100),
        QuotaWindow("claude", "b", "weekly", 20, 10000, 100),
    ]
    rates = {rate_key(window): pace for window, pace in zip(windows, [0.1, 0.0001, 0.001], strict=True)}
    assert choose(candidates, role="execute", windows=windows, rates=rates)[0] == candidates


def test_scoped_model_limit_does_not_exclude_another_model():
    window = QuotaWindow("claude", "a", "scoped", 97, 1000, 100, scope="model:sonnet")
    assert choose([("claude", "opus", "low")], role="execute", windows=[window], rates={})[0]
    with pytest.raises(ValueError, match="ひっ迫"):
        choose([("claude", "sonnet", "low")], role="execute", windows=[window], rates={})
