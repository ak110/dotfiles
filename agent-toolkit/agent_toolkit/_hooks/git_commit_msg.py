"""既存のGit hookを先に実行し、新規commitの本文を共通の書式で検査する。pyfltr: allow-comment"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess

import psutil

from agent_toolkit._common import commit_hook_environment, inherited_venv, next_action
from agent_toolkit._common.shell_segments import ExecutionSegment
from agent_toolkit._git import command as git_command
from agent_toolkit._hooks.pretooluse import commit_identity, shell_checks_git

_HOOK_NAME = re.compile(r"[a-z][a-z-]*")


def _forward_existing(name: str, arguments: list[str], directory: pathlib.Path, original: dict[str, str]) -> tuple[bool, int]:
    resolved = git_command.run(["rev-parse", "--git-path", f"hooks/{name}"], env=original, capture_output=True, check=True)
    path = pathlib.Path(os.fsdecode(resolved.stdout.rstrip(b"\r\n"))).absolute()
    if path.resolve().parent == directory.resolve() or not path.is_file() or not os.access(path, os.X_OK):
        return False, 0
    return True, subprocess.run(  # noqa: S603
        ["sh", "-c", 'exec "$@"', name, str(path), *arguments],  # noqa: S607
        env=original,
        check=False,
    ).returncode


def _commit_arguments() -> tuple[str, ...] | None:
    try:
        for parent in psutil.Process().parents():
            if parent.name().lower() not in {"git", "git.exe"}:
                continue
            argv = parent.cmdline()
            if not argv:
                return None
            parsed = shell_checks_git.git_subcommand_tokens(ExecutionSegment(("git", *argv[1:]), resolved=True))
            return parsed[1] if parsed is not None and parsed[0] == "commit" else None
    except psutil.Error:
        return None
    return None


def _identity_environment() -> tuple[dict[str, str], bool]:
    thread = os.environ.get("CODEX_THREAD_ID", "")
    claude = os.environ.get(commit_hook_environment.CLAUDE_ACTOR_ENV, "")
    if claude:
        return commit_identity.read_commit_observation("claude", claude), False
    if thread:
        return commit_identity.read_commit_observation("codex", thread), True
    return {}, False


def _validate_message(path: pathlib.Path, original: dict[str, str], *, forwarded: bool) -> int:
    cleaned = git_command.run(
        ["stripspace", "--strip-comments"], input=path.read_bytes(), env=original, capture_output=True, check=True
    )
    payload, is_codex = _identity_environment()
    if not payload and not (os.environ.get("CODEX_THREAD_ID") or os.environ.get(commit_hook_environment.CLAUDE_ACTOR_ENV)):
        return 0
    exempt = not is_codex and commit_identity.claude_commit_attribution_disabled(str(pathlib.Path.cwd()))
    identity = commit_identity.hook_observed_identity(payload, is_codex=is_codex) if not exempt else None
    if notice := shell_checks_git._git_commit_block(  # noqa: SLF001  # pylint: disable=protected-access
        (cleaned.stdout.decode("utf-8", errors="replace"),),
        identity,
        prepared=True,
    ):
        status = "既存のcommit-msgは終了0。" if forwarded else "既存のcommit-msgはありません。"
        next_action.report(
            f"{notice}\n{status}",
            next_action="上記の指摘に従って本文を直し、commitだけを再実行してください。",
        )
        return 1
    return 0


def run_hook(name: str, arguments: list[str], directory: pathlib.Path) -> int:
    original = commit_hook_environment.original_environment(directory, os.environ)
    inherited_venv.restore_launch_environment(original)
    forwarded, status = _forward_existing(name, arguments, directory, original)
    if status or name != "commit-msg" or len(arguments) != 1:
        return status
    commit = _commit_arguments()
    if commit is None or not shell_checks_git.is_new_commit_message(commit):
        return 0
    return _validate_message(pathlib.Path(arguments[0]), original, forwarded=forwarded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=pathlib.Path)
    parser.add_argument("hook")
    parser.add_argument("cwd", type=pathlib.Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not _HOOK_NAME.fullmatch(args.hook) or not args.directory.is_absolute() or not args.cwd.is_absolute():
        parser.error("Git hookの絶対配置先と名前を指定する")
    try:
        os.chdir(args.cwd)
        return run_hook(args.hook, args.arguments, args.directory)
    except (OSError, subprocess.SubprocessError) as error:
        next_action.report(str(error), next_action="既存Git hookと検査の起動先を確認し、commitだけを再実行してください。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
