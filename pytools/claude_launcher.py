# PYTHON_ARGCOMPLETE_OK
"""モデル別の既定引数でClaude CodeまたはCodexを起動する。"""

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import NoReturn

from pytools._internal import claude_common
from pytools._internal.cli import enable_completion

_LONG_OPTION_PATTERN = re.compile(r"(?<!\S)(--[A-Za-z0-9][A-Za-z0-9-]*)")

type CommandResolver = Callable[[str], str | None]
type ExecutableResolver = Callable[[str, tuple[pathlib.Path, ...]], pathlib.Path | None]
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


def _resolve_executable(name: str, preferred_directories: tuple[pathlib.Path, ...]) -> pathlib.Path | None:
    """Mise shimを避け、実行名のシンボリックリンクを保持して探索する。"""
    shim_directories = claude_common.mise_shim_directories()
    path_directories = (pathlib.Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry)
    seen: set[pathlib.Path] = set()
    for directory in (*preferred_directories, *path_directories):
        resolved_directory = claude_common.safe_resolve(directory)
        if resolved_directory in seen or resolved_directory in shim_directories:
            continue
        seen.add(resolved_directory)
        found = shutil.which(name, path=str(directory))
        if found is not None:
            return pathlib.Path(found).absolute()
    return None


def _resolve_command(name: str, home: pathlib.Path, resolve: ExecutableResolver) -> str | None:
    """公式導入先を優先し、実行名を保ったCLIのパスを返す。"""
    resolved = resolve(name, (home / ".local" / "bin",))
    return str(resolved) if resolved is not None else None


def _resolve_claude_bin(os_name: str, home: pathlib.Path, resolve: ExecutableResolver) -> str | None:
    del os_name
    return _resolve_command("claude", home, resolve)


def _complete_claude_options(prefix: str, **_: object) -> list[str]:
    claude_bin = _resolve_claude_bin(os.name, pathlib.Path.home(), _resolve_executable)
    if claude_bin is None:
        return []
    return [option for option in _get_claude_options(claude_bin) if option.startswith(prefix)]


def _complete_codex_options(prefix: str, **_: object) -> list[str]:
    codex_bin = _resolve_command("codex", pathlib.Path.home(), _resolve_executable)
    if codex_bin is None:
        return []
    return [option for option in _get_claude_options(codex_bin) if option.startswith(prefix)]


def _enable_model_completion(command_name: str) -> None:
    parser = argparse.ArgumentParser(add_help=False, prefix_chars="+")
    action = parser.add_argument("model_arguments", nargs="*")
    vars(action)["completer"] = _complete_claude_options if command_name == "claude" else _complete_codex_options
    enable_completion(parser)


def _run_model(
    command_name: str,
    model_args: tuple[str, ...],
    argv: list[str],
    *,
    os_name: str,
    home: pathlib.Path,
    which: CommandResolver,
    resolve: ExecutableResolver,
    run: CommandRunner,
    isatty: TerminalChecker,
) -> int:
    command_bin = _resolve_command(command_name, home, resolve)
    if command_bin is None:
        print(f"{command_name}コマンドが見つかりません。", file=sys.stderr)
        return 127

    del which, isatty
    if os_name != "nt":
        try:
            os.execv(command_bin, [command_name, *model_args, *argv])
        except FileNotFoundError:
            print(f"{command_name}コマンドが見つかりません。", file=sys.stderr)
            return 127
    return run([command_bin, *model_args, *argv])


def _run_claude(
    model_args: tuple[str, ...],
    argv: list[str],
    *,
    os_name: str,
    home: pathlib.Path,
    which: CommandResolver,
    resolve: ExecutableResolver,
    run: CommandRunner,
    isatty: TerminalChecker,
) -> int:
    return _run_model(
        "claude",
        model_args,
        argv,
        os_name=os_name,
        home=home,
        which=which,
        resolve=resolve,
        run=run,
        isatty=isatty,
    )


def _main(
    command_name: str,
    model_args: tuple[str, ...],
    argv: list[str] | None,
    *,
    os_name: str,
    home: pathlib.Path | None,
    which: CommandResolver,
    resolve: ExecutableResolver,
    run: CommandRunner,
    isatty: TerminalChecker,
) -> NoReturn:
    _enable_model_completion(command_name)
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    actual_home = pathlib.Path.home() if home is None else home
    sys.exit(
        _run_model(
            command_name,
            model_args,
            actual_argv,
            os_name=os_name,
            home=actual_home,
            which=which,
            resolve=resolve,
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
    resolve: ExecutableResolver = _resolve_executable,
    run: CommandRunner = _run_command,
    isatty: TerminalChecker = os.isatty,
) -> NoReturn:
    """SonnetモデルでClaude Codeを起動する。"""
    _main(
        "claude",
        ("--permission-mode=auto", "--model=sonnet[1m]"),
        argv,
        os_name=os_name,
        home=home,
        which=which,
        resolve=resolve,
        run=run,
        isatty=isatty,
    )


def main_opus(
    argv: list[str] | None = None,
    *,
    os_name: str = os.name,
    home: pathlib.Path | None = None,
    which: CommandResolver = shutil.which,
    resolve: ExecutableResolver = _resolve_executable,
    run: CommandRunner = _run_command,
    isatty: TerminalChecker = os.isatty,
) -> NoReturn:
    """OpusモデルでClaude Codeを起動する。"""
    _main(
        "claude",
        ("--permission-mode=auto", "--model=opus[1m]"),
        argv,
        os_name=os_name,
        home=home,
        which=which,
        resolve=resolve,
        run=run,
        isatty=isatty,
    )


def main_fable(
    argv: list[str] | None = None,
    *,
    os_name: str = os.name,
    home: pathlib.Path | None = None,
    which: CommandResolver = shutil.which,
    resolve: ExecutableResolver = _resolve_executable,
    run: CommandRunner = _run_command,
    isatty: TerminalChecker = os.isatty,
) -> NoReturn:
    """FableモデルでClaude Codeを起動する。

    `--fallback-model`は`--print`を伴う実行でだけ有効である。
    """
    _main(
        "claude",
        (
            "--permission-mode=auto",
            "--model=fable",
            "--fallback-model=opus[1m]",
        ),
        argv,
        os_name=os_name,
        home=home,
        which=which,
        resolve=resolve,
        run=run,
        isatty=isatty,
    )


def _main_codex(
    model: str,
    argv: list[str] | None,
    *,
    os_name: str,
    home: pathlib.Path | None,
    resolve: ExecutableResolver,
    run: CommandRunner,
) -> NoReturn:
    _main(
        "codex",
        ("-m", model),
        argv,
        os_name=os_name,
        home=home,
        which=shutil.which,
        resolve=resolve,
        run=run,
        isatty=os.isatty,
    )


def main_astra(
    argv: list[str] | None = None,
    *,
    os_name: str = os.name,
    home: pathlib.Path | None = None,
    resolve: ExecutableResolver = _resolve_executable,
    run: CommandRunner = _run_command,
) -> NoReturn:
    """AstraモデルでCodexを起動する。"""
    _main_codex("gpt-6-astra", argv, os_name=os_name, home=home, resolve=resolve, run=run)


def main_sol(
    argv: list[str] | None = None,
    *,
    os_name: str = os.name,
    home: pathlib.Path | None = None,
    resolve: ExecutableResolver = _resolve_executable,
    run: CommandRunner = _run_command,
) -> NoReturn:
    """SolモデルでCodexを起動する。"""
    _main_codex("gpt-5.6-sol", argv, os_name=os_name, home=home, resolve=resolve, run=run)


def main_terra(
    argv: list[str] | None = None,
    *,
    os_name: str = os.name,
    home: pathlib.Path | None = None,
    resolve: ExecutableResolver = _resolve_executable,
    run: CommandRunner = _run_command,
) -> NoReturn:
    """TerraモデルでCodexを起動する。"""
    _main_codex("gpt-5.6-terra", argv, os_name=os_name, home=home, resolve=resolve, run=run)
