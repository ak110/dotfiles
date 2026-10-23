"""atk configと初回bootstrapの永続モードの互換を確認する。"""

# pylint: disable=protected-access  # 設定保存の内部関数とbootstrapの相互運用を検証する。
import argparse

import pytest

from agent_toolkit._atk import config
from agent_toolkit._common import dotfiles_mode


@pytest.mark.parametrize("value", ["full", "minimal"])
def test_atk_config_and_bootstrap_share_saved_value(value, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "_config_file_path", lambda: path)
    monkeypatch.setattr(dotfiles_mode, "configuration_path", lambda: path)
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, value)
    config._cmd_config_set(argparse.Namespace(key="dotfiles_mode", value=value))
    monkeypatch.delenv(dotfiles_mode.MODE_ENV)
    assert dotfiles_mode.resolve() == value
