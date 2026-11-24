from __future__ import annotations

from collections.abc import Mapping, MutableMapping

from agent_toolkit._agents_server import logging_config
from agent_toolkit._atk import claude_connection as claude_settings
from agent_toolkit._common import claude_connection, delegated_session, inherited_venv, mcp_environment, runtime_environment

_INHERITED: dict[str, str] = {}


def prepare(environment: MutableMapping[str, str]) -> None:
    environment.pop(delegated_session.HANDOFF_PATH_ENV, None)
    paths = claude_settings.isolation_env_files()
    keys = claude_connection.api_environment_keys(paths) | {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN"}
    for key in keys:
        if key in environment:
            _INHERITED[key] = environment.pop(key)


def inherited(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """inherit接続とCodexへ、隔離前と同じ接続値を渡す。

    呼出元contextの有無によらず、起動層が設定した共有依存環境と会話contextを子へ渡さない。
    """
    source = runtime_environment.environment() if environment is None else environment
    # 起動時に外した接続値は、呼出元contextが無い場合だけ補う。呼出元の環境は自身の接続値を持つ。
    result = dict(source) if runtime_environment.current() is not None else {**_INHERITED, **source}
    inherited_venv.strip_inherited_venv(result)
    mcp_environment.strip_conversation_context(result)
    result.pop(delegated_session.HANDOFF_PATH_ENV, None)
    result.pop(logging_config.LAUNCH_RECORD_ENV, None)
    return result
