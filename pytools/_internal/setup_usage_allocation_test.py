import json
from pathlib import Path

import pytest
from agent_toolkit._atk import config_settings
from agent_toolkit._common.next_action import ActionableError

from pytools._internal import setup_usage_allocation


def test_defaults_preserve_explicit_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_settings, "config_file_path", lambda: path)
    config_settings.save_config({"custom": {"preserved": True}})
    assert setup_usage_allocation.run().changed
    assert config_settings.load_config_strict() == {
        "custom": {"preserved": True},
        "usage_auto_allocation": "true",
        "usage_activity_mode": "intermittent",
    }
    settings = config_settings.load_config_strict()
    settings.update(usage_auto_allocation="false", usage_activity_mode="continuous")
    config_settings.save_config(settings)
    before = path.read_bytes()
    assert not setup_usage_allocation.run().changed
    assert path.read_bytes() == before


@pytest.mark.parametrize("content", ["{broken", "[]"])
def test_invalid_config_is_not_overwritten(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_settings, "config_file_path", lambda: path)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ActionableError):
        setup_usage_allocation.run()
    assert path.read_text(encoding="utf-8") == content


def test_missing_config_is_created(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "new" / "config.json"
    monkeypatch.setattr(config_settings, "config_file_path", lambda: path)
    assert setup_usage_allocation.run().changed
    assert json.loads(path.read_text(encoding="utf-8"))["usage_auto_allocation"] == "true"
