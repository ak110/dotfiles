"""セッションで使うGit hookの固定配置とコマンドスコープの設定。pyfltr: allow-comment"""

import os
import pathlib
import shlex
import shutil

from agent_toolkit._common import atomic_file

HOOKS_ENV = "AGENT_TOOLKIT_COMMIT_HOOKS"
CLAUDE_ACTOR_ENV = "AGENT_TOOLKIT_COMMIT_CLAUDE_ACTOR"

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


def config_parameter(directory: pathlib.Path) -> str:
    value = directory.as_posix().replace("'", "'\\''").replace("!", "'\\!'")
    return "'core.hooksPath'='" + value + "'"


def shell_exports(directory: pathlib.Path) -> str:
    setting = shlex.quote(config_parameter(directory))
    return (
        f"export {HOOKS_ENV}={shlex.quote(directory.as_posix())}\n"
        f'case " ${{GIT_CONFIG_PARAMETERS-}} " in\n'
        f'  *" "{setting}" "*) ;;\n'
        f'  *) export GIT_CONFIG_PARAMETERS="${{GIT_CONFIG_PARAMETERS:+$GIT_CONFIG_PARAMETERS }}"{setting} ;;\n'
        "esac\n"
    )


def install(directory: pathlib.Path, root: pathlib.Path) -> bool:
    if not directory.is_absolute() or not root.is_absolute():
        raise ValueError("Git hookの固定配置と配布先は絶対パスで指定する")
    uv = shutil.which("uv")
    if not uv:
        raise FileNotFoundError("Git hookの起動に必要なuvが見つからない")
    launch = root / "scripts" / "launch.py"
    target = root / "agent_toolkit" / "_hooks" / "git_commit_msg.py"
    if not launch.is_file() or not target.is_file():
        raise FileNotFoundError(f"Git hookの配布先が揃っていない: {root}")
    runner = shlex.join(
        [
            pathlib.Path(uv).as_posix(),
            "run",
            "--no-cache",
            "--no-config",
            "--no-project",
            "--python",
            "3.15",
            "python",
            launch.as_posix(),
            target.as_posix(),
            directory.as_posix(),
        ]
    )
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
    import tomlkit  # noqa: PLC0415

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
