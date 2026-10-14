"""Codexのstdio MCPサーバーへ値を引き継ぐ環境変数（保存先、公開設定、通信環境）の名前。"""

from collections.abc import Iterable

from agent_toolkit._common import host_homes, plugin_environment, private_notes

PROXY_ENVIRONMENT_VARIABLES = (
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "GLOBAL_AGENT_HTTP_PROXY",
    "GLOBAL_AGENT_HTTPS_PROXY",
    "GLOBAL_AGENT_NO_PROXY",
)

NAMESPACE_ENVIRONMENT_VARIABLES = (
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "XDG_STATE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "MISE_STATE_DIR",
    "MISE_CACHE_DIR",
    "MISE_DATA_DIR",
    "MISE_CONFIG_DIR",
    private_notes.PRIVATE_NOTES_ENV,
    host_homes.CLAUDE_CONFIG_DIR_ENV,
    host_homes.CODEX_HOME_ENV,
    "CLAUDE_PLUGIN_DATA",
    "PLUGIN_DATA",
    plugin_environment.RUNTIME_DATA_ENV,
)


def environment_variable_names(setting_names: Iterable[str]) -> tuple[str, ...]:
    """公開設定の名前を加え、MCPの許可リストを返す。値の取得や未設定値の補完は行わない。

    設定キーの所有者が具体名を渡すため、共通層から設定CLIへの逆依存を持たない。
    Codexの`env_vars`は名前だけを受け取り、親に存在する値だけを子へ配送する。
    """
    return tuple(dict.fromkeys((*PROXY_ENVIRONMENT_VARIABLES, *NAMESPACE_ENVIRONMENT_VARIABLES, *setting_names)))
