# PYTHON_ARGCOMPLETE_OK
"""モデルごとに決めた引数を付けてClaude CodeまたはCodexを起動する。

公式の claude・codex を置き換えず、独自処理は補助入口に置く（docs/development/concepts-environment.md、pyfltr: allow-comment）。
"""

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

from agent_toolkit._common import startup_features
lazy from agent_toolkit._common import codex_models

from pytools._internal import common
from pytools._internal.cli import enable_completion
lazy from pytools import codex_launcher

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
        "-n",
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


def _has_claude_model(arguments: list[str]) -> bool:
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            return False
        name, equal, _value = token.partition("=")
        if name == "--model":
            return True
        if (
            name in _CLAUDE_VALUE_OPTIONS
            and not equal
            and (name not in {"--resume", "-r"} or (index + 1 < len(arguments) and not arguments[index + 1].startswith("-")))
        ):
            index += 1
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
    family: str | None = None,
    full: bool = True,
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

    del which
    if command_name == "claude" and _has_claude_model(argv):
        model_args = tuple(option for option in model_args if not option.startswith("--model="))
    if command_name == "claude" and not full and _claude_conversation(argv):
        model_args = _claude_startup_arguments(model_args)
    if family is not None:
        try:
            model_args = _codex_model_args(command_bin, family, argv)
        except ValueError as error:
            print(f"{error} --modelで完全なIDを指定して再実行できます。", file=sys.stderr)
            return 1
        if isatty(0) and codex_launcher.is_interactive(argv):
            return codex_launcher.run_interactive(pathlib.Path(command_bin), [*model_args, *argv], full=full)
        if not full and codex_launcher.uses_startup_features(argv):
            model_args = (*startup_features.codex_arguments(), *model_args)
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
    wrapper: str,
    os_name: str,
    home: pathlib.Path | None,
    which: CommandResolver,
    resolve: ExecutableResolver,
    run: CommandRunner,
    isatty: TerminalChecker,
    family: str | None = None,
) -> NoReturn:
    _enable_model_completion(command_name)
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    full = actual_argv[:1] == ["--full"]
    if full:
        actual_argv.pop(0)
    if _has_wrapper_help(command_name, actual_argv):
        defaults = f"-m <利用可能な最新の{family}系列>" if family else " ".join(model_args)
        print(f"使い方: {wrapper} [引数 ...]\n{command_name}を起動する。既定: {defaults}")
        print(f"残りの引数は{command_name}へ渡す。本体のヘルプは `{command_name} --help` で表示する。")
        print("先頭の --full で追加機能を止めずに起動する。")
        sys.exit(0)
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
            family=family,
            full=full,
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
        wrapper="sonnet",
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
        wrapper="opus",
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
        wrapper="poteto-mode",
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
        wrapper="fable",
        os_name=os_name,
        home=home,
        which=which,
        resolve=resolve,
        run=run,
        isatty=isatty,
    )


_CODEX_CONFIG_OPTIONS = frozenset({"-c", "--config", "--enable", "--disable", "-p", "--profile"})
_CODEX_CWD_OPTIONS = frozenset({"-C", "--cd"})
_CODEX_CATALOG_SWITCHES = frozenset({"--ignore-user-config", "--strict-config"})
_CODEX_VALUE_OPTIONS = (
    _CODEX_CONFIG_OPTIONS
    | _CODEX_CWD_OPTIONS
    | {
        "--remote",
        "--remote-auth-token-env",
        "-i",
        "--image",
        "-s",
        "--sandbox",
        "-a",
        "--ask-for-approval",
        "--add-dir",
        "--color",
        "--thread-source",
        "--cyber-access-program",
        "--output-schema",
        "-o",
        "--output-last-message",
    }
)
_CODEX_MANAGEMENT = frozenset(
    {
        "a",
        "agents",
        "app-server",
        "apply",
        "archive",
        "cloud",
        "completion",
        "debug",
        "delete",
        "doctor",
        "exec-server",
        "features",
        "help",
        "login",
        "logout",
        "mcp",
        "migrate-nfs-db",
        "migrate-rollouts",
        "plugin",
        "queue",
        "remote-control",
        "sandbox",
        "unarchive",
        "update",
    }
)


def _has_wrapper_help(command_name: str, arguments: list[str]) -> bool:
    value_options = (
        (_CLAUDE_VALUE_OPTIONS - {"-r", "--resume"}) | {"--model"}
        if command_name == "claude"
        else _CODEX_VALUE_OPTIONS | {"-m", "--model"}
    )
    tokens = iter(arguments)
    for token in tokens:
        if token == "--":
            break
        if token in {"-h", "--help"}:
            return True
        if token in value_options:
            value = next(tokens, None)
            if value == "--":
                break
    return False


def _claude_conversation(arguments: list[str]) -> bool:
    tokens = iter(arguments)
    for token in tokens:
        if token == "--":
            return True
        if token in {"--version", "-v", "auth", "agents", "mcp", "plugin", "doctor", "install", "update"}:
            return False
        if token in _CLAUDE_VALUE_OPTIONS | {"--model"} and token not in {"-r", "--resume"}:
            next(tokens, None)
        elif not token.startswith("-"):
            return True
    return True


def _claude_startup_arguments(model_args: tuple[str, ...]) -> tuple[str, ...]:
    settings = dict(startup_features.CLAUDE_SETTINGS)
    result = []
    for option in model_args:
        if option.startswith("--settings="):
            settings.update(json.loads(option.partition("=")[2]))
        else:
            result.append(option)
    return (*result, f"--settings={json.dumps(settings, separators=(',', ':'))}", "--no-chrome")


def _codex_model_args(binary: str, family: str, argv: list[str]) -> tuple[str, ...]:
    settings: list[str] = []
    cwd = None
    remote = False
    provider = None
    exec_subcommand = False
    first_positional = True
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            break
        option, equal, value = token.partition("=")
        if len(option) > 2 and option[:2] in {"-m", "-p", "-c", "-C", "-i", "-s", "-a", "-o"}:
            option, value, equal = option[:2], token[2:], "="
        # 公式app-serverは--profileを受理しない。通常起動にはそのまま渡し、一覧で別条件のモデルを選ばない。
        if option in {"-m", "--model", "-p", "--profile", "--oss", "--local-provider", "--help", "-h", "--version", "-V"}:
            return ()
        if option == "--remote":
            remote = True
        if token in _CODEX_CATALOG_SWITCHES:
            settings.append(token)
        if option in _CODEX_VALUE_OPTIONS:
            if not equal:
                index += 1
                if index == len(argv) or argv[index].startswith("-"):
                    return ()
                value = argv[index]
            if option in _CODEX_CONFIG_OPTIONS:
                settings.extend((option, value))
                if option in {"-c", "--config"}:
                    key, separator, raw = value.partition("=")
                    if separator and key.strip().strip("\"'") == "model_provider":
                        provider = raw.strip().strip("\"'")
            elif option in _CODEX_CWD_OPTIONS:
                cwd = value
        elif exec_subcommand and not option.startswith("-"):
            if option == "help":
                return ()
            exec_subcommand = False
        elif first_positional and not option.startswith("-"):
            if option in _CODEX_MANAGEMENT:
                return ()
            exec_subcommand = option in {"exec", "e"}
            first_positional = False
        index += 1
    if provider not in (None, "openai"):
        return ()
    if remote:
        raise ValueError("リモート接続のモデル一覧を取得できません。")
    try:
        catalog = codex_models.list_models_at((binary, *settings, "app-server", "--stdio"), cwd=cwd)
    except OSError, ValueError:
        raise ValueError("Codexのモデル一覧を取得できません。接続とログインを確認してください。") from None
    try:
        return ("-m", codex_models.latest_model(family, catalog))
    except ValueError:
        raise ValueError(f"Codexのモデル一覧に利用可能な{family}系列がありません。") from None


def _main_codex(
    family: str,
    argv: list[str] | None,
    *,
    os_name: str,
    home: pathlib.Path | None,
    resolve: ExecutableResolver,
    run: CommandRunner,
) -> NoReturn:
    _main(
        "codex",
        (),
        argv,
        wrapper=family,
        os_name=os_name,
        home=home,
        which=shutil.which,
        resolve=resolve,
        run=run,
        isatty=os.isatty,
        family=family,
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
    _main_codex("astra", argv, os_name=os_name, home=home, resolve=resolve, run=run)


def main_sol(
    argv: list[str] | None = None,
    *,
    os_name: str = os.name,
    home: pathlib.Path | None = None,
    resolve: ExecutableResolver = _resolve_executable,
    run: CommandRunner = _run_command,
) -> NoReturn:
    """SolモデルでCodexを起動する。"""
    _main_codex("sol", argv, os_name=os_name, home=home, resolve=resolve, run=run)


def main_terra(
    argv: list[str] | None = None,
    *,
    os_name: str = os.name,
    home: pathlib.Path | None = None,
    resolve: ExecutableResolver = _resolve_executable,
    run: CommandRunner = _run_command,
) -> NoReturn:
    """TerraモデルでCodexを起動する。"""
    _main_codex("terra", argv, os_name=os_name, home=home, resolve=resolve, run=run)
