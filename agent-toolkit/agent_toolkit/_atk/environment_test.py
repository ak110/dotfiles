"""エージェント環境の判定のテスト。"""

import pytest

from agent_toolkit._atk.environment import is_agent_environment

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")


@pytest.mark.parametrize("environment_name", _AGENT_ENVIRONMENT_VARIABLES)
def test_is_agent_environment_accepts_each_supported_variable(
    environment_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """対応する各環境変数が単独で設定された場合にエージェント環境と判定する。"""
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(environment_name, "")

    assert is_agent_environment()


def test_is_agent_environment_rejects_environment_without_supported_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """対応する環境変数が全て未設定の場合はエージェント環境と判定しない。"""
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    assert not is_agent_environment()
