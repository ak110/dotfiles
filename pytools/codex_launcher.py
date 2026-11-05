"""通常Codexの端末を保持し、管理daemon交代後も同じ会話の仕事を続行する。"""

from __future__ import annotations

import asyncio
import contextlib
import io
import math
import os
import pathlib
import sys
from collections.abc import Sequence

import psutil
from agent_toolkit._common import process_tree

from pytools._internal import common, prune_codex_packages, setup_codex_cli
from pytools._internal.codex_bridge import RecoveryBridge

_VALUE_OPTIONS = frozenset(
    {
        "-c",
        "--config",
        "--enable",
        "--disable",
        "--remote",
        "--remote-auth-token-env",
        "-i",
        "--image",
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
    }
)
_COMMANDS = frozenset(
    {
        "agents",
        "exec",
        "e",
        "review",
        "login",
        "logout",
        "mcp",
        "plugin",
        "app-server",
        "remote-control",
        "completion",
        "update",
        "doctor",
        "sandbox",
        "debug",
        "apply",
        "a",
        "resume",
        "queue",
        "archive",
        "delete",
        "migrate-rollouts",
        "migrate-nfs-db",
        "unarchive",
        "fork",
        "cloud",
        "exec-server",
        "features",
        "help",
    }
)
_INTERRUPTED = frozenset({130, 143, -2, -15, 0xC000013A, -1073741510})
_TOKEN_ENV = "DOTFILES_CODEX_RECOVERY_TOKEN"


def _interactive(arguments: Sequence[str]) -> bool:
    options = arguments[: arguments.index("--")] if "--" in arguments else arguments
    if any(token.split("=", 1)[0] in {"--remote", "--no-daemon", "--help", "-h", "--version", "-V"} for token in options):
        return False
    index = 0
    while index < len(arguments):
        token = arguments[index]
        name = token.split("=", 1)[0]
        if name in {"--remote", "--no-daemon", "--help", "-h", "--version", "-V"}:
            return False
        if token == "--":
            return True
        if name in _VALUE_OPTIONS:
            index += 1 if "=" in token else 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token not in _COMMANDS or token in {"resume", "fork"}
    return True


def _resume_arguments(arguments: Sequence[str], identifier: str, cwd: str) -> list[str]:
    # 初期プロンプト・画像・worktree作成は元turnへ保存済み。再開時に再投入しない。
    result: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            break
        if token.startswith(("-C", "-i")) and len(token) > 2:
            index += 1
            continue
        name = token.split("=", 1)[0]
        if name in _VALUE_OPTIONS:
            pair = [token] if "=" in token else list(arguments[index : index + 2])
            if name not in {"-i", "--image", "-C", "--cd"}:
                result.extend(pair)
            index += len(pair)
            continue
        if token.startswith("-") and name not in {"--worktree", "--last", "--all"}:
            result.append(token)
        index += 1
    return [*result, "--cd", cwd, "resume", identifier]


async def _run(binary: pathlib.Path, arguments: list[str], *, timeout: float) -> int:
    environment = dict(os.environ)
    # native自身が通常起動で行う起動処理を、IPC中継の接続より先に終える。
    started = await asyncio.to_thread(
        common.run_subprocess,
        [str(binary), "app-server", "daemon", "start"],
        timeout=timeout,
        tag="codex",
        env_overrides=environment,
    )
    if started is None or started.returncode != 0:
        print(f"Codexの管理daemonを起動できない: {common.format_cli_error(started)}。codex doctorで確認する", file=sys.stderr)
        return 1
    async with RecoveryBridge(str(binary), environment, prune_codex_packages.codex_home(), timeout=timeout) as bridge:
        environment[_TOKEN_ENV] = bridge.token
        command = arguments
        while True:
            process = await asyncio.create_subprocess_exec(
                str(binary),
                "--remote",
                bridge.endpoint,
                "--remote-auth-token-env",
                _TOKEN_ENV,
                *command,
                env=environment,
            )
            identity = None
            try:
                with contextlib.suppress(psutil.NoSuchProcess):
                    identity = psutil.Process(process.pid)
                    identity.create_time()
                waited = asyncio.create_task(process.wait())
                try:
                    assert bridge.failure is not None
                    done, _ = await asyncio.wait((waited, bridge.failure), return_when=asyncio.FIRST_COMPLETED)
                    if bridge.failure in done:
                        raise bridge.failure.result()
                    code = waited.result()
                    if code == 0 or code in _INTERRUPTED:
                        # 明示終了では対話待ちの復旧を中断し、context managerで所有資源を回収する。
                        return code if code >= 0 else 128 - code
                    await bridge.wait_tui_closed()
                finally:
                    waited.cancel()
                    await asyncio.gather(waited, return_exceptions=True)
            finally:
                if process.returncode is None:
                    result = await asyncio.to_thread(process_tree.stop_tree, identity)
                    process_tree.log_residual(result.descriptions(), context="Codex TUI")
                    await asyncio.wait_for(process.wait(), process_tree.RECLAIM_TIMEOUT_SECONDS)
            if code == 0 or code in _INTERRUPTED or not bridge.state.active or bridge.state.interrupted:
                return code if code >= 0 else 128 - code
            current = bridge.daemon_generation()
            generation = bridge.connected_generation
            if current is None or generation is None or current == generation:
                return code if code >= 0 else 128 - code
            identifier, cwd = bridge.state.thread_id, bridge.state.cwd
            if identifier is None or cwd is None:
                return code if code >= 0 else 128 - code
            bridge.prepare_tui_resume()
            command = _resume_arguments(arguments, identifier, cwd)


def main(argv: list[str] | None = None) -> int:
    """通常TUIだけを管理し、その他の公開コマンドはnativeへそのまま渡す。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    arguments = list(sys.argv[1:] if argv is None else argv)
    binary = setup_codex_cli.find_runtime_launcher(windows=sys.platform == "win32")
    if binary is None:
        print("管理対象のCodexが見つからない。update-dotfilesでCodexを導入して再実行する", file=sys.stderr)
        return 1
    if not _interactive(arguments) or not sys.stdin.isatty():
        if sys.platform == "win32":
            # WindowsのCRT execvは空白を含むargvと子の終了状態を保持しない。
            return process_tree.run_with_timeout([str(binary), *arguments]).returncode
        os.execv(str(binary), [str(binary), *arguments])
    try:
        timeout = float(os.environ.get("DOTFILES_CODEX_RECOVERY_TIMEOUT_SECONDS", "60"))
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("復旧待機時間には有限の正数が必要")
        return asyncio.run(_run(binary, arguments, timeout=timeout))
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # pylint: disable=broad-exception-caught
        # 外部RPCの検証例外は入力を含み得るため、本文を端末やログへ複製しない。
        diagnostic = _safe_error(error)
        print(f"Codexの自動復旧に失敗: {diagnostic}。codex doctorで確認し、元の会話をcodex resumeで再開する", file=sys.stderr)
        return 1


def _safe_error(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        return "; ".join(_safe_error(child) for child in error.exceptions)
    if isinstance(error, TimeoutError):
        return "復旧の応答待機が設定した期限に達した"
    return str(error) if isinstance(error, (OSError, RuntimeError, TimeoutError)) else type(error).__name__


if __name__ == "__main__":
    sys.exit(main())
