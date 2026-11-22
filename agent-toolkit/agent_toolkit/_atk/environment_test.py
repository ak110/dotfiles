import io

import pytest

from agent_toolkit._atk.environment import is_agent_environment, is_human_terminal

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")


@pytest.mark.parametrize("environment_name", _AGENT_ENVIRONMENT_VARIABLES)
def test_is_agent_environment_accepts_each_supported_variable(
    environment_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(environment_name, "")

    assert is_agent_environment()


def test_is_agent_environment_rejects_environment_without_supported_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    if agent:
        monkeypatch.setenv("CLAUDECODE", "1")

    assert is_human_terminal(_Stream(tty)) is expected
