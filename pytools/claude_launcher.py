# PYTHON_ARGCOMPLETE_OK
"""モデル別の既定引数でClaude Codeを起動する。"""

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import NoReturn

from pytools._internal.cli import enable_completion

_NON_INTERACTIVE_OPTIONS = frozenset(("--help", "-h", "--version", "-v", "--print", "-p"))
_LONG_OPTION_PATTERN = re.compile(r"(?<!\S)(--[A-Za-z0-9][A-Za-z0-9-]*)")

type CommandResolver = Callable[[str], str | None]
type CommandRunner = Callable[[list[str]], int]
type HelpRunner = Callable[[list[str]], subprocess.CompletedProcess[str] | None]
type TerminalChecker = Callable[[int], bool]


def _run_command(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode


def _run_help(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None


def _get_claude_options(claude_bin: str, *, run_help: HelpRunner = _run_help) -> list[str]:
    result = run_help([claude_bin, "--help"])
    if result is None or result.returncode != 0:
        return []
    return sorted(set(_LONG_OPTION_PATTERN.findall(result.stdout or "")))


def _resolve_claude_bin(os_name: str, home: pathlib.Path, which: CommandResolver) -> str | None:
    if os_name != "nt":
        preferred = home / ".local" / "bin" / "claude"
        if preferred.is_file() and os.access(preferred, os.X_OK):
            return str(preferred)
    return which("claude")


def _complete_claude_options(prefix: str, **_: object) -> list[str]:
    claude_bin = _resolve_claude_bin(os.name, pathlib.Path.home(), shutil.which)
    if claude_bin is None:
        return []
    return [option for option in _get_claude_options(claude_bin) if option.startswith(prefix)]


def _enable_claude_completion() -> None:
    parser = argparse.ArgumentParser(add_help=False, prefix_chars="+")
    action = parser.add_argument("claude_arguments", nargs="*")
    vars(action)["completer"] = _complete_claude_options
    enable_completion(parser)


def _run_claude(
    model_args: tuple[str, ...],
    argv: list[str],
    *,
    os_name: str,
    home: pathlib.Path,
    which: CommandResolver,
    run: CommandRunner,
    isatty: TerminalChecker,
) -> int:
    claude_bin = _resolve_claude_bin(os_name, home, which)
    if claude_bin is None:
        print("claudeコマンドが見つかりません。", file=sys.stderr)
        return 127

    interactive = not any(arg in _NON_INTERACTIVE_OPTIONS for arg in argv)
    return_code = run([claude_bin, *model_args, *argv])
    if interactive and return_code == 0 and isatty(1) and isatty(2):
        clear_bin = which("c")
        if clear_bin is not None:
            run([clear_bin])
    return return_code


def _main(
    model_args: tuple[str, ...],
    argv: list[str] | None,
    *,
    os_name: str,
    home: pathlib.Path | None,
    which: CommandResolver,
    run: CommandRunner,
    isatty: TerminalChecker,
) -> NoReturn:
    _enable_claude_completion()
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    actual_home = pathlib.Path.home() if home is None else home
    sys.exit(
        _run_claude(
            model_args,
            actual_argv,
            os_name=os_name,
            home=actual_home,
            which=which,
            run=run,
            isatty=isatty,
        )
    )


def main_sonnet(
    argv: list[str] | None = None,
    *,
    os_name: str = os.name,
    home: pathlib.Path | None = None,
    which: CommandResolver = shutil.which,
    run: CommandRunner = _run_command,
    isatty: TerminalChecker = os.isatty,
) -> NoReturn:
    """SonnetモデルでClaude Codeを起動する。"""
    _main(
        ("--permission-mode=auto", "--model=sonnet[1m]"),
        argv,
        os_name=os_name,
        home=home,
        which=which,
        run=run,
        isatty=isatty,
    )


def main_opus(
    argv: list[str] | None = None,
    *,
    os_name: str = os.name,
    home: pathlib.Path | None = None,
    which: CommandResolver = shutil.which,
    run: CommandRunner = _run_command,
    isatty: TerminalChecker = os.isatty,
) -> NoReturn:
    """OpusモデルでClaude Codeを起動する。"""
    _main(
        ("--permission-mode=auto", "--model=opus[1m]"),
        argv,
        os_name=os_name,
        home=home,
        which=which,
        run=run,
        isatty=isatty,
    )


def main_fable(
    argv: list[str] | None = None,
    *,
    os_name: str = os.name,
    home: pathlib.Path | None = None,
    which: CommandResolver = shutil.which,
    run: CommandRunner = _run_command,
    isatty: TerminalChecker = os.isatty,
) -> NoReturn:
    """FableモデルでClaude Codeを起動する。"""
    _main(
        (
            "--permission-mode=auto",
            "--model=fable",
            "--fallback-model=claude-opus-4-7[1m]",
        ),
        argv,
        os_name=os_name,
        home=home,
        which=which,
        run=run,
        isatty=isatty,
    )
