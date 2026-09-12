"""現在の対話CLI本体を一意に識別できる場合だけ終了を要求する。"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import sys
from dataclasses import dataclass

import psutil

_NO_VALUE = frozenset(
    {
        "--strict-config",
        "--oss",
        "--approve-for-me",
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "--search",
        "--no-alt-screen",
    }
)
_ONE_VALUE = frozenset(
    {
        "-c",
        "--config",
        "--enable",
        "--disable",
        "-m",
        "--model",
        "--local-provider",
        "-p",
        "--profile",
        "-s",
        "--sandbox",
        "-C",
        "--cd",
        "--add-dir",
        "-a",
        "--ask-for-approval",
    }
)
_REMOTE = frozenset({"--remote", "--remote-auth-token-env"})
_NONINTERACTIVE = frozenset(
    {
        "agents",
        "exec",
        "e",
        "review",
        "login",
        "logout",
        "mcp",
        "plugin",
        "mcp-server",
        "app-server",
        "remote-control",
        "completion",
        "update",
        "doctor",
        "sandbox",
        "debug",
        "apply",
        "a",
        "queue",
        "archive",
        "delete",
        "migrate-rollouts",
        "unarchive",
        "fork",
        "cloud",
        "exec-server",
        "features",
        "help",
    }
)
_SHORT_WITH_VALUE = frozenset("cmp sCai".replace(" ", ""))


@dataclass(frozen=True)
class Target:
    """再照合に必要な単一プロセスの識別情報。"""

    pid: int
    host: str
    create_time: float
    executable: pathlib.Path
    device: int
    inode: int


def is_interactive_codex(argv: list[str]) -> bool:
    """Codexの通常起動又はresume起動だけを受理する。"""
    if not argv or pathlib.Path(argv[0]).name not in {"codex", "codex.exe"}:
        return False
    index = 1
    resume = False
    positional = 0
    option_mode = True
    while index < len(argv):
        value = argv[index]
        if option_mode and value == "--":
            option_mode = False
            index += 1
            continue
        if option_mode and value in {"--help", "-h", "--version", "-V"}:
            return False
        if option_mode and value in _REMOTE:
            return False
        if option_mode and any(value.startswith(f"{name}=") for name in _REMOTE):
            return False
        if option_mode and value in _NO_VALUE | ({"--last", "--all", "--include-non-interactive"} if resume else set()):
            index += 1
            continue
        if option_mode and value in _ONE_VALUE:
            if index + 1 >= len(argv):
                return False
            index += 2
            continue
        if option_mode and any(value.startswith(f"{name}=") for name in _ONE_VALUE if name.startswith("--")):
            index += 1
            continue
        if option_mode and value.startswith("-") and len(value) > 2 and value[1] in _SHORT_WITH_VALUE:
            index += 1
            continue
        if option_mode and value in {"-i", "--image"}:
            if index + 1 >= len(argv):
                return False
            index += 2
            continue
        if option_mode and value.startswith("-"):
            return False
        if not resume and positional == 0 and value == "resume":
            resume = True
            index += 1
            continue
        if not resume and positional == 0 and value in _NONINTERACTIVE:
            return False
        positional += 1
        if positional > (2 if resume else 1):
            return False
        index += 1
    return True


def _target(process: psutil.Process) -> Target | None:
    try:
        argv = process.cmdline()
        executable = pathlib.Path(process.exe()).resolve()
        stat = executable.stat()
        basename = executable.name.lower()
        if basename in {"codex", "codex.exe"}:
            if not sys.platform.startswith("linux") or process.terminal() in {None, "", "?"} or not is_interactive_codex(argv):
                return None
            host = "codex"
        elif basename in {"claude", "claude.exe", "claude-code", "claude-code.exe"}:
            host = "claude"
        else:
            return None
        return Target(process.pid, host, process.create_time(), executable, stat.st_dev, stat.st_ino)
    except (OSError, psutil.Error):
        return None


def identify_current_host() -> Target | None:
    """現在プロセスの祖先から最初の対話ホストを一意に識別する。"""
    try:
        ancestors = psutil.Process(os.getpid()).parents()
    except psutil.Error:
        return None
    for process in ancestors:
        target = _target(process)
        if target is not None:
            return target
        try:
            argv = process.cmdline()
        except psutil.Error:
            continue
        if any(value in {"app-server", "remote-control"} for value in argv[1:2]):
            return None
    return None


def _same_process(target: Target) -> bool:
    try:
        process = psutil.Process(target.pid)
        executable = pathlib.Path(process.exe()).resolve()
        stat = executable.stat()
        return (
            process.create_time() == target.create_time
            and executable == target.executable
            and stat.st_dev == target.device
            and stat.st_ino == target.inode
        )
    except (OSError, psutil.Error):
        return False


def main() -> int:
    """終了要求の実行証跡を出力し、再照合済みの単一PIDだけを停止する。"""
    target = identify_current_host()
    if target is None:
        print(json.dumps({"exit_session_invoked": True, "status": "unsupported"}, separators=(",", ":")))
        print("現在の対話CLI本体を一意に識別できません。/exit又は/quitを入力してください。", file=sys.stderr)
        return 0
    if not _same_process(target):
        print(json.dumps({"exit_session_invoked": True, "status": "changed"}, separators=(",", ":")))
        print("終了対象が識別後に変化したため停止しません。", file=sys.stderr)
        return 0
    print(
        json.dumps(
            {"exit_session_invoked": True, "status": "terminating", "host": target.host, "pid": target.pid},
            separators=(",", ":"),
        ),
        flush=True,
    )
    if os.name == "nt":
        psutil.Process(target.pid).terminate()
    else:
        os.kill(target.pid, signal.SIGTERM)
    return 0
