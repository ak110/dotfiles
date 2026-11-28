from __future__ import annotations

import pathlib

from agent_toolkit._common.shell_segments import ExecutionSegment
from agent_toolkit._common.shell_tokens import is_atk_command

GLOBAL_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "-C",
        "-c",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--super-prefix",
        "--config-env",
        "--list-cmds",
    }
)


_GLOBAL_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {
        "--no-pager",
        "-p",
        "--paginate",
        "--bare",
        "--no-replace-objects",
        "--literal-pathspecs",
        "--glob-pathspecs",
        "--noglob-pathspecs",
        "--icase-pathspecs",
        "--no-optional-locks",
        "--exec-path",
        "--html-path",
        "--man-path",
        "--info-path",
        "--help",
        "--version",
    }
)


def git_subcommand_tokens(segment: ExecutionSegment) -> tuple[str, tuple[str, ...]] | None:
    if not segment.resolved or not segment.tokens:
        return None
    tokens = segment.tokens

    if is_atk_command(tokens) and tokens[1:4] == ("run-script", "commit-with-timeout", "--"):
        arguments = tokens[4:]
        if len(arguments) >= 3 and arguments[0] == "--timeout" and arguments[2] == "--":
            return "commit", arguments[3:]
        if len(arguments) >= 2 and arguments[0].startswith("--timeout=") and arguments[1] == "--":
            return "commit", arguments[2:]
        return None
    if pathlib.PurePath(tokens[0]).name != "git":
        return None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-"):
            return token, tuple(tokens[index + 1 :])
        if token in GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if token in _GLOBAL_OPTIONS_WITHOUT_VALUE or "=" in token:
            index += 1
            continue
        return None
    return None
