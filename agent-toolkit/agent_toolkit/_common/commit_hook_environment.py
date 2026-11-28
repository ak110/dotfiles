"""セッションで使うGit hookの固定配置とコマンドスコープの設定。pyfltr: allow-comment"""

import os
import pathlib
import re
import shlex
import shutil
from collections.abc import Mapping, Sequence

from agent_toolkit._common import atomic_file, host_homes

HOOKS_ENV = "AGENT_TOOLKIT_COMMIT_HOOKS"
CLAUDE_ACTOR_ENV = "AGENT_TOOLKIT_COMMIT_CLAUDE_ACTOR"
_CLAUDE_ROOT_ENV = "AGENT_TOOLKIT_COMMIT_CLAUDE_ROOT"
_CLAUDE_UV_ENV = "AGENT_TOOLKIT_COMMIT_CLAUDE_UV"

_LAUNCHER = "scripts/launch.py"
_TARGET = "agent_toolkit/_hooks/git_commit_msg.py"
_UV_ARGUMENTS = ("run", "--no-cache", "--no-config", "--no-project", "--python", "3.15", "python")

_HOOKS = (
    "applypatch-msg",
    "pre-applypatch",
    "post-applypatch",
    "pre-commit",
    "pre-merge-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "pre-rebase",
    "post-checkout",
    "post-merge",
    "pre-push",
    "pre-receive",
    "update",
    "proc-receive",
    "post-receive",
    "post-update",
    "reference-transaction",
    "push-to-checkout",
    "pre-auto-gc",
    "post-rewrite",
    "sendemail-validate",
    "fsmonitor-watchman",
    "p4-changelist",
    "p4-prepare-changelist",
    "p4-post-changelist",
    "p4-pre-submit",
    "post-index-change",
)


def claude_directory(home: pathlib.Path) -> pathlib.Path:
    return host_homes.commit_hook_directory(home, "claude") / "session"


def config_parameter(directory: pathlib.Path) -> str:
    value = directory.as_posix().replace("'", "'\\''").replace("!", "'\\!'")
    return "'core.hooksPath'='" + value + "'"


def original_environment(directory: pathlib.Path, environment: Mapping[str, str]) -> dict[str, str]:
    original = dict(environment)
    key = "GIT_CONFIG_PARAMETERS"
    directories = [directory]
    home = pathlib.Path.home()
    if directory == claude_directory(home):
        # 入れ子の会話が継承した旧Claude・Codexのラッパーを再実行せず、既存hookへ転送する。  # pyfltr: allow-comment
        directories.extend(host_homes.commit_hook_directory(home, host) for host in ("claude", "codex"))
    restored = original.get(key, "")
    for installed in directories:
        pattern = r"(?<!\S)" + re.escape(config_parameter(installed)) + r"(?!\S)"
        restored = re.sub(pattern, "", restored).strip()
    if restored:
        original[key] = restored
    else:
        original.pop(key, None)
    for name in (HOOKS_ENV, _CLAUDE_ROOT_ENV, _CLAUDE_UV_ENV):
        original.pop(name, None)
    return original


def atk_environment(arguments: Sequence[str], environment: dict[str, str]) -> dict[str, str]:
    directory = environment.get(HOOKS_ENV)
    if directory and tuple(arguments[:2]) != ("run-script", "commit-with-timeout"):
        return original_environment(pathlib.Path(directory), environment)
    return environment


def install_claude(directory: pathlib.Path, root: pathlib.Path) -> str:
    uv = _validate_installation(directory, root).absolute()
    runner = (
        f'"${{{_CLAUDE_UV_ENV}:?}}" {shlex.join(_UV_ARGUMENTS)} '
        f'"${{{_CLAUDE_ROOT_ENV}:?}}/{_LAUNCHER}" "${{{_CLAUDE_ROOT_ENV}:?}}/{_TARGET}" ' + shlex.quote(directory.as_posix())
    )
    _install(directory, runner)
    setting = shlex.quote(config_parameter(directory))
    return (
        f"export {HOOKS_ENV}={shlex.quote(directory.as_posix())}\n"
        f"export {_CLAUDE_ROOT_ENV}={shlex.quote(root.as_posix())}\n"
        f"export {_CLAUDE_UV_ENV}={shlex.quote(uv.as_posix())}\n"
        f'case " ${{GIT_CONFIG_PARAMETERS-}}" in\n'
        f'  *" "{setting}) ;;\n'
        f'  *) export GIT_CONFIG_PARAMETERS="${{GIT_CONFIG_PARAMETERS:+$GIT_CONFIG_PARAMETERS }}"{setting} ;;\n'
        "esac\n"
    )


def install(directory: pathlib.Path, root: pathlib.Path) -> bool:
    uv = _validate_installation(directory, root)
    runner = shlex.join(
        [uv.as_posix(), *_UV_ARGUMENTS, (root / _LAUNCHER).as_posix(), (root / _TARGET).as_posix(), directory.as_posix()]
    )
    return _install(directory, runner)


def _validate_installation(directory: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    if not directory.is_absolute() or not root.is_absolute():
        raise ValueError("Git hookの固定配置と配布先は絶対パスで指定する")
    uv = shutil.which("uv")
    if not uv:
        raise FileNotFoundError("Git hookの起動に必要なuvが見つからない")
    if not (root / _LAUNCHER).is_file() or not (root / _TARGET).is_file():
        raise FileNotFoundError(f"Git hookの配布先が揃っていない: {root}")
    return pathlib.Path(uv)


def _install(directory: pathlib.Path, runner: str) -> bool:
    changed = False
    for name in _HOOKS:
        path = directory / name
        source = f'#!/bin/sh\noriginal_cwd=$PWD\ncd / || exit 1\nexec {runner} {name} "$original_cwd" "$@"\n'
        if not path.is_file() or path.read_text(encoding="utf-8") != source:
            atomic_file.atomic_write(path, source)
            changed = True
        if not os.access(path, os.X_OK):
            path.chmod(0o755)
    return changed


def merge_codex_config(path: pathlib.Path, directory: pathlib.Path | None) -> bool:
    import tomlkit  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    original = path.read_text(encoding="utf-8") if path.exists() else ""
    document = tomlkit.parse(original)
    policy = document.setdefault("shell_environment_policy", {})
    settings = policy.setdefault("set", {})
    previous = settings.get("GIT_CONFIG_PARAMETERS", "")
    old = settings.get(HOOKS_ENV)
    if old:
        old_parameter = config_parameter(pathlib.Path(old))
        if previous.endswith(old_parameter):
            previous = previous.removesuffix(old_parameter).rstrip()
    if directory is None:
        if not old:
            return False
        del settings[HOOKS_ENV]
        if previous:
            settings["GIT_CONFIG_PARAMETERS"] = previous
        else:
            settings.pop("GIT_CONFIG_PARAMETERS", None)
        atomic_file.atomic_write(path, tomlkit.dumps(document))
        return True
    settings[HOOKS_ENV] = directory.as_posix()
    settings["GIT_CONFIG_PARAMETERS"] = (previous + " " + config_parameter(directory)).strip()
    if includes := policy.get("include_only"):
        for name in (HOOKS_ENV, "GIT_CONFIG_PARAMETERS"):
            if name not in includes:
                includes.append(name)
    if (filters := policy.get("filters")) and "include" in filters.values():
        for name in (HOOKS_ENV, "GIT_CONFIG_PARAMETERS"):
            filters.setdefault(name, "include")
    generated = tomlkit.dumps(document)
    if original == generated:
        return False
    atomic_file.atomic_write(path, generated)
    return True
