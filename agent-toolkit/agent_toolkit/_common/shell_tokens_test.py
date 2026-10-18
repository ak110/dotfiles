"""シェルのトークン列からリダイレクトを除く処理を検証する。"""

import pathlib
import shlex

import pytest

from agent_toolkit._common import plugin_environment, shell_segments, shell_tokens
from agent_toolkit._common.shell_tokens import strip_redirections


@pytest.mark.parametrize("windows", [False, True])
def test_atk_launcher_names_are_one_definition(windows: bool) -> None:
    instructions = plugin_environment.cli_instructions(pathlib.Path("/plugin root"), windows=windows)
    launcher = instructions.splitlines()[1].removeprefix("起動部分: ")
    for suffix, predicate in (
        ("agents wait", shell_tokens.is_agents_wait_command),
        ("agents-exit-session", shell_tokens.is_agents_exit_session_command),
    ):
        segments = shell_segments.extract_execution_segments(f"{launcher} {suffix}")
        assert any(segment.resolved and predicate(segment.tokens) for segment in segments)


@pytest.mark.parametrize("name", ["atk", "atk.cmd", "atk.py"])
@pytest.mark.parametrize("prefix", ["", "C:/Users/x/", "C:\\Users\\x\\"])
def test_atk_launchers_accept_windows_paths(name: str, prefix: str) -> None:
    assert shell_tokens.is_agents_wait_command((prefix + name, "agents", "wait"))
    assert shell_tokens.is_agents_exit_session_command((prefix + name, "agents-exit-session"))


@pytest.mark.parametrize(
    "command",
    [
        "git rev-parse HEAD > /tmp/x",
        "git rev-parse HEAD >/tmp/x",
        "git rev-parse HEAD >> f",
        "git rev-parse HEAD < /dev/null",
        "git rev-parse HEAD 2>/dev/null",
        "git rev-parse HEAD 2> err > out",
        "git rev-parse HEAD 2>&1",
        "git rev-parse HEAD >&2",
        "git rev-parse HEAD &>/tmp/x",
        "git rev-parse HEAD &>> f",
        "git rev-parse HEAD <<< x",
        "git rev-parse HEAD 1>f",
        "git rev-parse HEAD << EOF",
        "git rev-parse HEAD >| f",
        "git rev-parse HEAD >",
    ],
)
def test_redirection_operator_and_target_are_removed(command: str) -> None:
    assert strip_redirections(shlex.split(command, posix=True)) == ("git", "rev-parse", "HEAD")


def test_options_and_arguments_are_kept() -> None:
    tokens = ("git", "-C", "/tmp/repo", "rev-parse", "--short=7", "HEAD~1", "HEAD", "--", "path")
    assert strip_redirections(tokens) == tokens
