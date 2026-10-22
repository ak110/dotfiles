"""モード保存が他のatk設定を保持し、初回導入と同じ値を解決することを確かめる。"""

import json

import pytest

from agent_toolkit._common import dotfiles_mode


def test_mode_is_persisted_without_losing_other_settings(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"base_engine": "codex", "other": {"value": 1}}))
    monkeypatch.setattr(dotfiles_mode, "configuration_path", lambda: path)
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, "full")
    assert dotfiles_mode.select("minimal") == "minimal"
    monkeypatch.delenv(dotfiles_mode.MODE_ENV)
    assert dotfiles_mode.is_minimal()
    assert json.loads(path.read_text()) == {"base_engine": "codex", "other": {"value": 1}, "dotfiles_mode": "minimal"}


@pytest.mark.parametrize("value", ["agent", "", "MINIMAL"])
def test_invalid_mode_leaves_saved_settings_unchanged(value, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"dotfiles_mode": "full"}')
    monkeypatch.setattr(dotfiles_mode, "configuration_path", lambda: path)
    with pytest.raises(ValueError):
        dotfiles_mode.select(value)
    assert path.read_text() == '{"dotfiles_mode": "full"}'
