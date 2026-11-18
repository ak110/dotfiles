"""SDKの継承環境から接続変数を除き、明示的に継承するbackend用に保持する。"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping

from agent_toolkit._agents_server import logging_config
from agent_toolkit._atk import config
from agent_toolkit._common import claude_connection, delegated_session, inherited_venv, mcp_environment, runtime_environment

_INHERITED: dict[str, str] = {}


def prepare(environment: MutableMapping[str, str]) -> None:
    """agents_serverの起動時に一度、値を外して保持する。"""
    environment.pop(delegated_session.HANDOFF_PATH_ENV, None)
    paths = (
        path for key in ("claude_credits_env_file", "claude_payg_env_file") if (path := config.resolve_mutable_setting(key))
    )
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
