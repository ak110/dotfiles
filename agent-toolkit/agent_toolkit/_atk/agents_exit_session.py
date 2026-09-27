"""現在の対話CLI本体を一意に識別できる場合だけ終了を要求する。"""

from __future__ import annotations

import json
import os
import pathlib
import re
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

import psutil

from agent_toolkit._atk import outcome as _outcome

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
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
_FUNCTION_HOOK_DIR = "agent-toolkit-function-hooks"
_STATE_MAX_AGE_SECONDS = 14 * 24 * 60 * 60


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
        # 実体の名前は導入形態により対話CLIの名前と一致しない。
        # Claude Codeのネイティブ導入では実体が版数名のファイルであり、
        # npm導入ではNode.jsの実体となるため、argv[0]の名前も判定材料へ含める。
        names = {executable.name.lower()}
        if argv:
            names.add(pathlib.Path(argv[0]).name.lower())
        if names & {"codex", "codex.exe"}:
            if not sys.platform.startswith("linux") or process.terminal() in {None, "", "?"} or not is_interactive_codex(argv):
                return None
            host = "codex"
        elif names & {"claude", "claude.exe", "claude-code", "claude-code.exe"}:
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


def _function_hook_dir() -> pathlib.Path | None:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    root = pathlib.Path(configured) if configured and pathlib.Path(configured).is_absolute() else None
    if root is None and home:
        root = pathlib.Path(home) / ".claude"
    return root / _FUNCTION_HOOK_DIR if root is not None and root.is_absolute() else None


def _function_hook_paths(session_id: str) -> tuple[pathlib.Path, pathlib.Path] | None:
    directory = _function_hook_dir()
    if directory is None or not _SESSION_ID.fullmatch(session_id):
        return None
    return directory / f"marker-{session_id}.txt", directory / f"request-{session_id}.txt"


def _request_hook_exit(target: Target, session_id: str | None) -> bool:
    if target.host != "claude" or not session_id:
        return False
    paths = _function_hook_paths(session_id)
    if paths is None:
        return False
    marker, request = paths
    try:
        if marker.read_text(encoding="utf-8") != "ready" or marker.stat().st_mtime <= target.create_time:
            return False
        request.write_text("requested", encoding="utf-8")
    except OSError:
        return False
    return True


def sweep_function_hook_files(*, keep_session_id: str | None = None, clear: bool = False) -> None:
    """期限切れの読込目印と終了要求を回収する。会話破棄時は現行IDの記録も削除する。"""
    directory = _function_hook_dir()
    if directory is None or not directory.is_dir():
        return
    own_paths = _function_hook_paths(keep_session_id) if keep_session_id else None
    threshold = time.time() - _STATE_MAX_AGE_SECONDS
    for pattern in ("marker-*.txt", "request-*.txt"):
        for path in directory.glob(pattern):
            if own_paths and path in own_paths:
                if clear:
                    path.unlink(missing_ok=True)
                continue
            try:
                if path.stat().st_mtime < threshold:
                    path.unlink()
            except FileNotFoundError:
                continue


def request_termination(
    *, session_id: str | None = None, before_signal: Callable[[Target], None] | None = None
) -> tuple[str, Target | None]:
    """停止対象を停止直前に再識別し、一致した場合だけ単一PIDへ終了要求を送る。

    標準出力と標準エラーへは何も書かない。hookのように出力の形式が別に決まっている
    呼び出し元からの実行を可能にするためである。
    戻り値の状態は次の4つとする。

    - `unsupported`: 現在の対話CLI本体を一意に識別できない
    - `changed`: 識別後に対象が変化したため停止しない
    - `exit_requested`: Claude Codeのターン完了時に`/exit`を実行する
    - `terminating`: 単一PIDへ終了要求を送った
    """
    target = identify_current_host()
    if target is None:
        return "unsupported", None
    if not _same_process(target):
        return "changed", target
    if _request_hook_exit(target, session_id or os.environ.get("CLAUDE_CODE_SESSION_ID")):
        return "exit_requested", target
    if before_signal is not None:
        before_signal(target)
    if os.name == "nt":
        psutil.Process(target.pid).terminate()
    else:
        os.kill(target.pid, signal.SIGTERM)
    return "terminating", target


def main() -> int:
    """終了要求の実行証跡を出力し、再照合済みの単一PIDだけを停止する。"""

    def _before_signal(target: Target) -> None:
        _outcome.report_success(
            f"現在のセッションへ終了要求を送る: {target.host} pid={target.pid}",
            _outcome.ResultKind.VALUE_OUTPUT,
        )
        print(
            json.dumps(
                {"exit_session_invoked": True, "status": "terminating", "host": target.host, "pid": target.pid},
                separators=(",", ":"),
            ),
            flush=True,
        )

    status, target = request_termination(before_signal=_before_signal)
    if status == "terminating":
        return 0
    if status == "unsupported":
        print(json.dumps({"exit_session_invoked": True, "status": "unsupported"}, separators=(",", ":")))
        _outcome.report_warning("現在の対話CLI本体を一意に識別できない。/exit又は/quitを入力して終了する。")
        return 0
    if status == "changed":
        print(json.dumps({"exit_session_invoked": True, "status": "changed"}, separators=(",", ":")))
        _outcome.report_warning("終了対象が識別後に変化したため停止しない。")
        return 0
    assert target is not None
    _outcome.report_success(
        "ターンを終えると/exitでセッションを終了する。これ以上ツールを呼ばずにターンを終える。",
        _outcome.ResultKind.VALUE_OUTPUT,
    )
    print(
        json.dumps(
            {"exit_session_invoked": True, "status": "exit_requested", "host": target.host, "pid": target.pid},
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0
