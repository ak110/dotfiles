"""MCPサーバーへ引き継ぐ環境変数名の一覧は、呼出元が渡した名前だけを加え、環境変数の値を取り込まない。"""

import pytest

from agent_toolkit._common import mcp_environment, proxy_environment


def test_environment_names_preserve_supplied_names_and_proxy_identity() -> None:
    """将来の設定名も呼出元が渡した列から許可リストへ加え、重複した名前を含めない。"""
    setting_names = ("AGENT_TOOLKIT_CONFIG_BASE_ENGINE", "http_proxy", "AGENT_TOOLKIT_CONFIG_NEW_SETTING")
    names = mcp_environment.environment_variable_names(setting_names)
    assert set(setting_names) <= set(names)
    assert names.count("http_proxy") == 1
    assert names.index("AGENT_TOOLKIT_CONFIG_BASE_ENGINE") < names.index("AGENT_TOOLKIT_CONFIG_NEW_SETTING")
    assert proxy_environment.PROXY_ENVIRONMENT_VARIABLES is mcp_environment.PROXY_ENVIRONMENT_VARIABLES
    assert {"AGENT_TOOLKIT_RUNTIME_DATA", "UV_PROJECT_ENVIRONMENT"} <= set(names)
    assert not {"CLAUDE_PLUGIN_DATA", "PLUGIN_DATA"} & set(names)


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
    """miseの設定・信頼状態・導入先・cacheのディレクトリを指す変数名を許可リストへ含め、明示した所在を子へ引き継ぐ。"""
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


@pytest.mark.parametrize("lowercase", [False, True])
def test_runtime_names_preserve_open_environment_and_exclude_parent_context(lowercase: bool) -> None:
    """WIの除外名は大小文字によらず除き、共有資源・認証・独自名は値へ触れずに配送する。"""
    # 20261014-145521-001.md「起動元に固有の名前」の全名。
    excluded = [
        "CLAUDECODE",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_BRIDGE_SESSION_ID",
        "CLAUDE_CODE_REMOTE_SESSION_ID",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_PID",
        "CLAUDE_JOB_DIR",
        "CLAUDE_EFFORT",
        "CLAUDE_CODE_REMOTE",
        "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
        "CLAUDE_ENV_FILE",
        "CODEX_SESSION_ID",
        "CODEX_THREAD_ID",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PLUGIN_DATA",
        "PLUGIN_ROOT",
        "PLUGIN_DATA",
        "AGENT_TOOLKIT_OWNER_SESSION",
        "AGENT_TOOLKIT_STATUS_HOST_SESSION",
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "PWD",
        "OLDPWD",
        "SHLVL",
        "_",
        "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
        "NODE_REPL_AUTH_TOKEN",
        "CODEX_GUARDIAN_DECISIONS_API_KEY",
        "OPENAI_FEDERATION_RULE_ID",
        "OPENAI_IDENTITY_TOKEN_FILE",
        "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
        "HERDR_PANE_ID",
        "HERDR_TAB_ID",
        "HERDR_WORKSPACE_ID",
        "HERDR_SOCKET_PATH",
        "HERDR_ENV",
        "HERDR_STARTUP_CWD",
        "HERDR_CLIENT_SOCKET_PATH",
        "CLAUDE_CODE_SESSION_ATTENDED",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_EXECPATH",
        "AI_AGENT",
        "__MISE_DIFF",
        "__MISE_SESSION",
        "__MISE_ORIG_PATH",
        "MOTD_SHOWN",
        "SSH_CLIENT",
        "SSH_CONNECTION",
        "SSH_AGENT_PID",
    ]
    if lowercase:
        excluded = [name.lower() for name in excluded]
    retained = (
        "CUSTOM_VENDOR_API_KEY",
        "ARBITRARY_NEW_SETTING",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "LC_ALL",
        "DBUS_SESSION_BUS_ADDRESS",
        "SSH_AUTH_SOCK",
        "CLAUDE_CODE_TASK_LIST_ID",
        "CLAUDE_CLIENT_PRESENCE_FILE",
        "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS",
        "HERDR_BIN_PATH",
        "UV_PROJECT_ENVIRONMENT",
    )

    class NamesOnly(dict[str, str]):
        """名前列挙で値を参照すると失敗する環境mapping。"""

        def __getitem__(self, key: str) -> str:
            raise AssertionError(key)

    environment = NamesOnly.fromkeys([*excluded, *retained], "synthetic-secret")
    assert set(mcp_environment.runtime_environment_variable_names(environment)) == set(retained)
    assert not mcp_environment.runtime_environment_variable_names({})
