"""シェルのトークン列からリダイレクトを除く処理を検証する。"""

import shlex

import pytest

from agent_toolkit._common.shell_tokens import strip_redirections


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
