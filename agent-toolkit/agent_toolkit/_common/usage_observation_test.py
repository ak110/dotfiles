from dataclasses import replace
from typing import Any

import pytest

from agent_toolkit._common import usage_observation as subject


def test_rate_and_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(subject.time, "time", lambda: 200.0)
    subject.observe([subject.QuotaWindow("codex", "a", "w", 10, 1000, 100)], setting_signature="s", active_seconds=0)
    window = subject.QuotaWindow("codex", "a", "w", 20, 1000, 200)
    subject.observe([window], setting_signature="s", active_seconds=10)
    subject.observe([window], setting_signature="s", active_seconds=1)
    assert subject.snapshot()["rates"] == {subject.rate_key(window): 1}


def test_invalid_percent(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "state_dir", lambda: tmp_path)
    subject.observe([subject.QuotaWindow("codex", "a", "w", float("nan"), None, 100)], setting_signature="s", active_seconds=10)
    assert subject.snapshot()["windows"][0]["used_percent"] is None


@pytest.mark.parametrize("change", ["account", "contract", "setting", "reset", "recovery"])
def test_pace_does_not_cross_observation_boundary(tmp_path, monkeypatch, change):
    monkeypatch.setattr(subject, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(subject.time, "time", lambda: 300.0)
    first = subject.QuotaWindow("codex", "a", "w", 10, 1000, 100)
    second = replace(first, used_percent=20, observed_at=200)
    subject.observe([first], setting_signature="model-a", active_seconds=0)
    subject.observe([second], setting_signature="model-a", active_seconds=10)
    assert subject.snapshot()["rates"]
    changes: dict[str, dict[str, Any]] = {
        "account": {"account": "b"},
        "contract": {"contract": "new"},
        "reset": {"resets_at": 2000},
        "recovery": {"used_percent": 5},
        "setting": {},
    }
    next_window = replace(second, observed_at=300, **changes[change])
    subject.observe([next_window], setting_signature="model-b" if change == "setting" else "model-a", active_seconds=20)
    assert subject.rate_key(next_window) not in subject.snapshot()["rates"]


def test_activity_counters_from_other_producer_are_not_subtracted(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(subject.time, "time", lambda: 400.0)
    base = subject.QuotaWindow("codex", "a", "w", 10, 1000, 100)
    subject.observe([base], setting_signature="s", active_seconds=0)
    subject.observe([replace(base, used_percent=20, observed_at=200)], setting_signature="s", active_seconds=10)
    monkeypatch.setattr(subject, "_PRODUCER", "different-process")
    subject.observe([replace(base, used_percent=30, observed_at=300)], setting_signature="s", active_seconds=1000)
    assert not subject.snapshot()["rates"]
    subject.observe([replace(base, used_percent=40, observed_at=400)], setting_signature="s", active_seconds=1010)
    assert list(subject.snapshot()["rates"].values()) == [1.0]


def test_setting_history_survives_switch_without_crossing_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(subject.time, "time", lambda: 500.0)
    base = subject.QuotaWindow("codex", "a", "w", 10, 1000, 100, contract="plan")
    for at, used, signature, seconds in [
        (100, 10, "A", 0),
        (200, 20, "A", 10),
        (300, 30, "B", 20),
        (400, 35, "B", 30),
        (500, 40, "A", 40),
    ]:
        subject.observe([replace(base, observed_at=at, used_percent=used)], setting_signature=signature, active_seconds=seconds)
    key = subject.rate_key(base)
    assert subject.snapshot()["setting_rates"] == {"A": {key: 1.0}, "B": {key: 0.5}}
    subject.observe([replace(base, observed_at=600, used_percent=1, resets_at=2000)], setting_signature="A", active_seconds=50)
    assert not subject.snapshot()["setting_rates"]


def test_activity_ratio_requires_recent_codex_history_from_one_producer(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(subject.time, "time", lambda: 3000.0)
    window = subject.QuotaWindow("codex", "a", "weekly", 10, 10000, 1200, contract="plan")
    subject.observe([window], setting_signature="s", active_seconds=0)
    subject.observe([replace(window, observed_at=2100)], setting_signature="s", active_seconds=90)
    assert subject.snapshot(now=2100)["activity_ratio"] is None
    subject.observe([replace(window, observed_at=3000)], setting_signature="s", active_seconds=180)
    assert subject.snapshot()["activity_ratio"] == pytest.approx(0.1)
    assert subject.snapshot(now=3601)["activity_ratio"] is None
    monkeypatch.setattr(subject, "_PRODUCER", "new")
    subject.observe([replace(window, observed_at=3001)], setting_signature="s", active_seconds=10)
    assert subject.snapshot(now=3001)["activity_ratio"] is None
