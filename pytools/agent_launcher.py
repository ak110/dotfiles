# PYTHON_ARGCOMPLETE_OK
"""モデルごとに決めた引数を付けてClaude CodeまたはCodexを起動する。"""

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import NoReturn

from pytools._internal import common
from pytools._internal.cli import enable_completion
lazy from pytools import codex_launcher
lazy from pytools._internal import codex_runtime

_LONG_OPTION_PATTERN = re.compile(r"(?<!\S)(--[A-Za-z0-9][A-Za-z0-9-]*)")

type CommandResolver = Callable[[str], str | None]
type ExecutableResolver = Callable[[str, tuple[pathlib.Path, ...]], pathlib.Path | None]
type CommandRunner = Callable[[list[str]], int]
type HelpRunner = Callable[[list[str]], subprocess.CompletedProcess[str] | None]
type TerminalChecker = Callable[[int], bool]

_CLAUDE_VALUE_OPTIONS = frozenset(
    {
        "--add-dir",
        "--agent",
        "--agents",
        "--append-system-prompt",
        "--append-system-prompt-file",
        "--betas",
        "--debug-file",
        "--disallowedTools",
        "--disallowed-tools",
        "--effort",
        "--fallback-model",
        "--input-format",
        "--json-schema",
        "--max-budget-usd",
        "--mcp-config",
        "--name",
        "--output-format",
        "--permission-mode",
        "--plugin-dir",
        "--resume",
        "-r",
        "--session-id",
        "--setting-sources",
        "--settings",
        "--system-prompt",
        "--system-prompt-file",
        "--tools",
        "--allowedTools",
        "--allowed-tools",
    }
)
_CLAUDE_MANAGEMENT = frozenset({"auth", "doctor", "install", "mcp", "plugin", "plugins", "setup-token", "update", "upgrade"})


def _suppress_claude_default_model(arguments: list[str]) -> bool:
    index = 0
    seen_positional = False
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            return False
        name, equal, _value = token.partition("=")
        if name in {"--model", "--help", "-h", "--version", "-v", "-V"}:
            return True
        if name in _CLAUDE_VALUE_OPTIONS and not equal:
            if name not in {"--resume", "-r"} or (index + 1 < len(arguments) and not arguments[index + 1].startswith("-")):
                index += 1
        elif not name.startswith("-") and not seen_positional:
            if name in _CLAUDE_MANAGEMENT:
                return True
            seen_positional = True
        index += 1
    return False


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
    shim_directories = common.mise_shim_directories()
    path_directories = (pathlib.Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry)
    seen: set[pathlib.Path] = set()
    for directory in (*preferred_directories, *path_directories):
        resolved_directory = common.safe_resolve(directory)
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
    codex_bin = codex_runtime.find_runtime_launcher(windows=os.name == "nt")
    if codex_bin is None:
        return []
    return [option for option in _get_claude_options(str(codex_bin.absolute())) if option.startswith(prefix)]


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
        print(
            (
                f"{command_name}コマンドが見つかりません。"
                f"~/.local/binとPATHの導入先を確認し、update-dotfilesを再実行してください。"
            ),
            file=sys.stderr,
        )
        return 127

    del which, isatty
    if command_name == "claude" and _suppress_claude_default_model(argv):
        model_args = tuple(option for option in model_args if not option.startswith("--model="))
    if os_name != "nt":
        try:
            os.execv(command_bin, [command_name, *model_args, *argv])
        except FileNotFoundError:
            print(
                (
                    f"{command_name}コマンドが見つかりません。"
                    f"~/.local/binとPATHの導入先を確認し、update-dotfilesを再実行してください。"
                ),
                file=sys.stderr,
            )
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


# poteto-modeではagent-basics・agent-toolkitを無効にし、pstackを有効にする。
# `--settings`は起動単位の設定として、ユーザー設定の`enabledPlugins`より優先して読まれる。
# claude.aiのアカウントから同期された写し（`@synced`）は、同名の`@ak110-dotfiles`を無効にすると
# 代わりに読み込まれるため、併せて無効にする。
_POTETO_MODE_SETTINGS = json.dumps(
    {
        "enabledPlugins": {
            "agent-basics@ak110-dotfiles": False,
            "agent-toolkit@ak110-dotfiles": False,
            "agent-basics@synced": False,
            "agent-toolkit@synced": False,
            "pstack@pstack-claude": True,
        }
    },
    separators=(",", ":"),
)


def main_poteto_mode(
    argv: list[str] | None = None,
    *,
    os_name: str = os.name,
    home: pathlib.Path | None = None,
    which: CommandResolver = shutil.which,
    resolve: ExecutableResolver = _resolve_executable,
    run: CommandRunner = _run_command,
    isatty: TerminalChecker = os.isatty,
) -> NoReturn:
    """agent-basics・agent-toolkitを無効にしpstackを有効にしてClaude Codeを起動する。"""
    _main(
        "claude",
        ("--permission-mode=auto", "--model=opus[1m]", f"--settings={_POTETO_MODE_SETTINGS}"),
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


def _main_codex(family: str, argv: list[str] | None) -> NoReturn:
    _enable_model_completion("codex")
    arguments = list(sys.argv[1:] if argv is None else argv)
    binary = codex_runtime.find_runtime_launcher(windows=os.name == "nt")
    if binary is None:
        print("codexコマンドが見つかりません。update-dotfilesでCodexを導入して再実行してください。", file=sys.stderr)
        sys.exit(127)
    binary = binary.absolute()
    try:
        catalog_args = codex_launcher.series_catalog_arguments(arguments)
        if catalog_args is not None:
            if catalog_args.remote is not None and catalog_args.command in {"exec", "e"}:
                print(
                    "外部接続のcodex execはモデル指定を受け付けません。モデルを選ぶ場合は対話起動を使ってください。",
                    file=sys.stderr,
                )
                sys.exit(1)
            arguments = ["-m", codex_launcher.resolve_series_model(binary, family, catalog_args), *arguments]
    except ValueError as error:
        print(
            f"Codexの{family}系列を選べません: {error}。接続とログインを確認するか、--modelで完全IDを指定してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        status = codex_launcher.launch_resolved(binary, arguments)
    except OSError:
        print("codexを起動できません。update-dotfilesでCodexの導入先を確認してください。", file=sys.stderr)
        status = 127
    sys.exit(status)


def main_astra(argv: list[str] | None = None) -> NoReturn:
    """Astra系列の最新版でCodexを起動する。"""
    _main_codex("astra", argv)


def main_sol(argv: list[str] | None = None) -> NoReturn:
    """Sol系列の最新版でCodexを起動する。"""
    _main_codex("sol", argv)


def main_terra(argv: list[str] | None = None) -> NoReturn:
    """Terra系列の最新版でCodexを起動する。"""
    _main_codex("terra", argv)
