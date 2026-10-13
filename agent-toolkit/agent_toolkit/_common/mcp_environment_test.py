"""MCPの配送名は、呼出元が渡した名前だけを加え環境値を取り込まない。"""

import pytest

from agent_toolkit._common import mcp_environment, proxy_environment


def test_environment_names_preserve_supplied_names_and_proxy_identity() -> None:
    """将来の設定名も呼出元の列から配送し、重複した許可名を含めない。"""
    setting_names = ("AGENT_TOOLKIT_CONFIG_BASE_ENGINE", "http_proxy", "AGENT_TOOLKIT_CONFIG_NEW_SETTING")
    names = mcp_environment.environment_variable_names(setting_names)
    assert set(setting_names) <= set(names)
    assert names.count("http_proxy") == 1
    assert names.index("AGENT_TOOLKIT_CONFIG_BASE_ENGINE") < names.index("AGENT_TOOLKIT_CONFIG_NEW_SETTING")
    assert proxy_environment.PROXY_ENVIRONMENT_VARIABLES is mcp_environment.PROXY_ENVIRONMENT_VARIABLES


def test_environment_names_do_not_capture_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """列挙は環境値と無関係で、prefixだけが一致する未公開名や認証名を許可しない。"""
    setting_names = ("AGENT_TOOLKIT_CONFIG_BASE_ENGINE",)
    names = mcp_environment.environment_variable_names(setting_names)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_UNPUBLISHED", "diagnostic-value")
    monkeypatch.setenv("DIAGNOSTIC_TOKEN", "diagnostic-secret")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "diagnostic-owner")
    assert mcp_environment.environment_variable_names(setting_names) == names
    assert not {"AGENT_TOOLKIT_CONFIG_UNPUBLISHED", "DIAGNOSTIC_TOKEN", "AGENT_TOOLKIT_OWNER_SESSION"} & set(names)


@pytest.mark.parametrize("explicit_override", [False, True])
def test_mise_directory_names_preserve_resolver_locations(monkeypatch: pytest.MonkeyPatch, explicit_override: bool) -> None:
    """名前空間を配送しても、設定・信頼状態・導入先・cacheの明示した所在を失わない。"""
    directories = ("MISE_STATE_DIR", "MISE_CACHE_DIR", "MISE_DATA_DIR", "MISE_CONFIG_DIR")
    values = {}
    for index, name in enumerate(directories):
        if explicit_override:
            value = f"/diagnostic/mise-{index}"
            monkeypatch.setenv(name, value)
            values[name] = value
        else:
            monkeypatch.delenv(name, raising=False)
    names = mcp_environment.environment_variable_names(())
    assert set(directories) <= set(names)
    assert all(names.count(name) == 1 for name in directories)
    assert set(values.values()).isdisjoint(names)
    assert {"XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"} <= set(names)
