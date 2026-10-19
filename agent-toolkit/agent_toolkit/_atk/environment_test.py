"""エージェント環境の判定のテスト。"""

import io

import pytest

from agent_toolkit._atk.environment import is_agent_environment, is_human_terminal

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


class _Stream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.mark.parametrize(("agent", "tty", "expected"), [(False, True, True), (False, False, False), (True, True, False)])
def test_is_human_terminal_requires_terminal_outside_agent_environment(
    monkeypatch: pytest.MonkeyPatch, agent: bool, tty: bool, expected: bool
) -> None:
    """エージェント環境の外で標準エラーが端末の場合だけ、人が端末から直接使う呼び出しと判定する。"""
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    if agent:
        monkeypatch.setenv("CLAUDECODE", "1")

    assert is_human_terminal(_Stream(tty)) is expected
