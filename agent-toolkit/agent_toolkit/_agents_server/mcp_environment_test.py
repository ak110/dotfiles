"""`atk config`が公開する設定キーとMCPサーバーへ渡す環境変数名の対応を、両方を参照できるMCP側の層で検証する。"""

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config
from agent_toolkit._common import mcp_environment, proxy_environment


def test_mcp_setting_names_follow_public_config_keys(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`atk config show`が表示する可変設定は全て環境変数名へ含め、保存先から導出する値は上書き用の環境変数名へ含めない。"""
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
        if key == "claude_api_mode":
            assert name in names
            monkeypatch.setenv(name, "api-credits")
            with pytest.raises(SystemExit) as exited:
                atk.main(["config", "get", key, "claude_orchestrator_api_only", "claude_worker_api_first"])
            assert exited.value.code == 0
            assert capsys.readouterr().out.splitlines() == ["api-credits", "false", "true"]
            monkeypatch.delenv(name)
        elif key not in keys:
            with pytest.raises(SystemExit) as exited:
                atk.main(["config", "get", key])
            assert exited.value.code == 2
            assert "旧モデル設定キー" in capsys.readouterr().err
            monkeypatch.setenv(name, "codex:gpt-6-sol/medium")
            with pytest.raises(ValueError, match=name):
                config.parse_unresolved_model_candidates("execute")
            monkeypatch.delenv(name)
    assert not {"AGENT_TOOLKIT_CONFIG_STATE_DIR", "AGENT_TOOLKIT_CONFIG_PRIVATE_NOTES"} & set(names)
    assert proxy_environment.PROXY_ENVIRONMENT_VARIABLES is mcp_environment.PROXY_ENVIRONMENT_VARIABLES


def test_environment_names_do_not_capture_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """環境変数名の列挙は環境変数の値に左右されず、接頭辞だけが一致する未公開の名前や認証用の名前を含めない。"""
    names = mcp_environment.environment_variable_names(config.config_environment_variable_names())
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_UNPUBLISHED", "diagnostic-value")
    monkeypatch.setenv("DIAGNOSTIC_TOKEN", "diagnostic-secret")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "diagnostic-owner")
    assert mcp_environment.environment_variable_names(config.config_environment_variable_names()) == names
    assert not {"AGENT_TOOLKIT_CONFIG_UNPUBLISHED", "DIAGNOSTIC_TOKEN", "AGENT_TOOLKIT_OWNER_SESSION"} & set(names)
