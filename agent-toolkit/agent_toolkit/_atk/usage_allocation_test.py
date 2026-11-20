"""候補の保持とひっ迫回避の試験。"""

from dataclasses import replace

import pytest

from agent_toolkit._atk import usage_allocation
from agent_toolkit._atk.usage_allocation import choose
from agent_toolkit._common.usage_observation import QuotaWindow, rate_key


def test_unknown_prefers_codex():
    candidates = [("claude", "a", "b"), ("codex", "c", "d")]
    result, reason = choose(candidates, role="execute", windows=[], rates={})
    assert result == candidates[::-1]
    assert "不明" in reason


def test_claude_tight(monkeypatch):
    monkeypatch.setattr(usage_allocation.time, "time", lambda: 100.0)
    windows = [QuotaWindow("claude", "a", "w", 90, None, 0)]
    claude = [("claude", "a", "low"), ("claude", "b", "high")]
    assert choose(claude, role="execute", windows=windows, rates={})[0] == claude
    candidates = [*claude, ("agy", "other", "low"), ("codex", "c", "low")]
    assert choose(candidates, role="execute", windows=windows, rates={}, previous="claude")[0] == [
        candidates[3],
        candidates[2],
        *claude,
    ]
    assert choose(candidates, role="orchestrate", windows=windows, rates={})[0] == candidates


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


def test_tight_scoped_window_only_affects_matching_model(monkeypatch):
    monkeypatch.setattr(usage_allocation.time, "time", lambda: 100.0)
    window = QuotaWindow("claude", "a", "scoped", 97, 1000, 100, scope="model:sonnet")
    for model, tight in [("sonnet", True), ("opus", False)]:
        candidates = [("claude", model, "low"), ("codex", "other", "low")]
        result, reason = choose(candidates, role="execute", windows=[window], rates={}, previous="claude")
        assert result == (candidates[::-1] if tight else candidates)
        assert ("ひっ迫" in reason) == tight


def test_forecast_demotes_claude_before_high_usage(monkeypatch):
    monkeypatch.setattr(usage_allocation.time, "time", lambda: 100.0)
    candidates = [("claude", "a", "low"), ("codex", "b", "low")]
    window = QuotaWindow("claude", "account", "weekly", 80, 400, 100)
    result, reason = choose(candidates, role="execute", windows=[window], rates={rate_key(window): 0.1}, previous="claude")
    assert result == candidates[::-1]
    assert all(detail in reason for detail in ("weekly", "20%", "30%"))


def test_roomy_codex_precedes_claude_even_after_claude_was_preferred(monkeypatch):
    monkeypatch.setattr(usage_allocation.time, "time", lambda: 100.0)
    candidates = [("claude", "opus", "high"), ("codex", "sol", "low")]
    codex = QuotaWindow("codex", "c", "primary", 20, 200, 100)
    for used in (None, 20, 95, 100):
        claude = QuotaWindow("claude", "a", "weekly", used, 200, 100)
        result, _reason = choose(
            candidates,
            role="execute",
            windows=[codex, claude],
            rates={rate_key(codex): 0.01, rate_key(claude): 0.01},
            previous="claude",
        )
        assert result == ([candidates[1]] if used == 100 else candidates[::-1])


@pytest.mark.parametrize("different", ["account", "window", "model", "surface"])
def test_recovery_only_supersedes_same_claude_window(monkeypatch, different):
    monkeypatch.setattr(usage_allocation.time, "time", lambda: 102.0)
    rejected = QuotaWindow("claude", "a", "seven_day_opus", None, None, 100, scope="model:opus", contract="plan", rejected=True)
    recovered = QuotaWindow(
        "claude", "a", "weekly_scoped", 0, None, 101, scope='{"model":{"display_name":"Opus"}}', contract="plan"
    )
    other = {
        "account": replace(recovered, account="b"),
        "window": replace(recovered, window_id="weekly_all", scope="all"),
        "model": replace(recovered, scope='{"model":{"display_name":"Sonnet"}}'),
        "surface": replace(recovered, scope='{"surface":{"display_name":"Code"}}'),
    }[different]
    candidates = [("claude", "opus", "low"), ("claude", "sonnet", "low")]
    selected, reason = choose(candidates, role="execute", windows=[other, rejected], rates={})
    assert selected == candidates[1:] and "seven_day_opus" in reason


def test_expired_newer_report_does_not_restore_old_rejection(monkeypatch):
    monkeypatch.setattr(usage_allocation.time, "time", lambda: 102.0)
    older = QuotaWindow("claude", "a", "five_hour", None, None, 100, rejected=True)
    newer = QuotaWindow("claude", "a", "session", 100, 102, 101)
    candidates = [("claude", "opus", "low")]
    selected, _reason = choose(candidates, role="execute", windows=[newer, older], rates={})
    assert selected == candidates
