"""公開設定CLIとMCP配送名の接続を、両方を消費できるMCP側の層で検証する。"""

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config
from agent_toolkit._common import mcp_environment, proxy_environment


def test_mcp_setting_names_follow_public_config_keys(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開showの可変設定全件を配送し、保存先の導出値を設定override名へ混ぜない。"""
    with pytest.raises(SystemExit) as exited:
        atk.main(["config", "show"])
    assert exited.value.code == 0
    keys = {line.partition(" ")[0] for line in capsys.readouterr().out.splitlines()}
    keys -= {"config_dir", "state_dir", "data_dir", "private_notes"}
    setting_names = config.config_environment_variable_names()
    names = mcp_environment.environment_variable_names(setting_names)
    for key in keys:
        assert f"AGENT_TOOLKIT_CONFIG_{key.upper()}" in names
        config.resolve_mutable_setting(key)
    for name in setting_names:
        key = name.removeprefix("AGENT_TOOLKIT_CONFIG_").lower()
        if key not in keys:
            with pytest.raises(SystemExit) as exited:
                atk.main(["config", "get", key])
            assert exited.value.code == 2
            assert "旧モデル設定キー" in capsys.readouterr().err
            monkeypatch.setenv(name, "codex:gpt-6-sol/medium")
            with pytest.raises(ValueError, match=name):
                config.parse_unresolved_model_candidates("high_tier")
            monkeypatch.delenv(name)
    assert not {"AGENT_TOOLKIT_CONFIG_STATE_DIR", "AGENT_TOOLKIT_CONFIG_PRIVATE_NOTES"} & set(names)
    assert proxy_environment.PROXY_ENVIRONMENT_VARIABLES is mcp_environment.PROXY_ENVIRONMENT_VARIABLES


def test_environment_names_do_not_capture_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """列挙は環境値と無関係で、prefixだけが一致する未公開名や認証名を許可しない。"""
    names = mcp_environment.environment_variable_names(config.config_environment_variable_names())
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_UNPUBLISHED", "diagnostic-value")
    monkeypatch.setenv("DIAGNOSTIC_TOKEN", "diagnostic-secret")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "diagnostic-owner")
    assert mcp_environment.environment_variable_names(config.config_environment_variable_names()) == names
    assert not {"AGENT_TOOLKIT_CONFIG_UNPUBLISHED", "DIAGNOSTIC_TOKEN", "AGENT_TOOLKIT_OWNER_SESSION"} & set(names)
