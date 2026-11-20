"""通常Codexの端末を保持し、管理daemon交代後も同じ会話の仕事を続行する。"""

from __future__ import annotations

import contextlib
import io
import math
import os
import pathlib
import sys
from collections.abc import Sequence
from typing import Any, NamedTuple
from urllib.parse import unquote, urlsplit

# 非対話の公開コマンドはhookから繰り返し起動される。対話端末の復旧だけが使う依存は、
# 実行体へ引き渡すまで読み込まない（docs/development/design-hosts-codex-runtime.md「Codexの起動管理とdaemon復旧」）。
lazy import asyncio

lazy import psutil
lazy from agent_toolkit._common import codex_models, process_identity, process_tree
lazy from wsproto.utilities import ProtocolError

from pytools._internal import codex_runtime
lazy from pytools._internal import common
lazy from pytools._internal.codex_bridge import RecoveryBridge
lazy from pytools._internal.codex_recovery import RunningTurn
lazy from pytools._internal.codex_transport import RpcChannel, native_proxy

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
        "-a",
        "--ask-for-approval",
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
_MODEL_COMMANDS = frozenset({"exec", "e", "review", "resume", "fork"})
_CATALOG_VALUE_OPTIONS = frozenset({"-c", "--config", "--enable", "--disable"})
_CONTROL_OPTIONS = frozenset({"-h", "--help", "-V", "--version"})
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


class CatalogTarget(NamedTuple):
    """モデル一覧と通常起動で一致させる設定および接続先。"""

    settings: tuple[str, ...]
    cwd: str | None
    remote: str | None
    token_env: str | None
    profile: str | None
    ignore_user_config: bool


def series_catalog_arguments(arguments: Sequence[str]) -> CatalogTarget | None:
    """明示指定や管理操作を除き、系列一覧が必要な起動から設定とcwdを取る。"""
    config: list[str] = []
    cwd = None
    remote = token_env = profile = None
    ignore_user_config = False
    command_seen = False
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            break
        option, equal, value = token.partition("=")
        if len(option) > 2 and option[:2] in {"-m", "-p", "-c", "-C", "-i", "-s", "-a"} and not option.startswith("--"):
            option, value, equal = option[:2], token[2:], "="
        if option in _CONTROL_OPTIONS or option in {"--oss", "--local-provider", "-m", "--model"}:
            return None
        if option in _VALUE_OPTIONS:
            if not equal:
                index += 1
                if index == len(arguments) or arguments[index].startswith("-"):
                    return None
                value = arguments[index]
            if option in {"-p", "--profile"}:
                profile = value
            elif option in _CATALOG_VALUE_OPTIONS:
                config.extend((option, value))
            elif option in {"-C", "--cd"}:
                cwd = value
            elif option == "--remote":
                remote = value
            elif option == "--remote-auth-token-env":
                token_env = value
        elif option == "--ignore-user-config":
            ignore_user_config = True
        elif option == "--strict-config":
            config.append(option)
        elif not option.startswith("-") and not command_seen:
            if option in _COMMANDS and option not in _MODEL_COMMANDS:
                return None
            command_seen = True
        index += 1
    return CatalogTarget(tuple(config), cwd, remote, token_env, profile, ignore_user_config)


async def _fetch_catalog_from_channel(channel: RpcChannel) -> list[dict[str, Any]]:
    next_id = 0

    async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal next_id
        next_id += 1
        await channel.send({"id": next_id, "method": method, "params": params})
        while True:
            response = await channel.receive()
            if response.get("id") != next_id:
                continue
            if "error" in response or not isinstance(result := response.get("result"), dict):
                raise ValueError("外部App Serverがモデル選択に必要な応答を返しませんでした")
            return result

    await request("initialize", {"clientInfo": {"name": "dotfiles-model-launcher", "version": "1.0"}, "capabilities": {}})
    await channel.send({"method": "initialized", "params": {}})
    return await codex_models.fetch_catalog(request)


async def _remote_model_catalog(binary: pathlib.Path, url: str, token_env: str | None) -> list[dict[str, Any]]:
    endpoint = urlsplit(url)
    timeout = 30
    async with asyncio.timeout(timeout):
        if endpoint.scheme == "unix" and not endpoint.netloc:
            async with native_proxy(
                str(binary), os.environ, timeout=timeout, socket_path=unquote(endpoint.path) or None
            ) as channel:
                return await _fetch_catalog_from_channel(channel)
        if endpoint.scheme not in {"ws", "wss"} or not endpoint.hostname or endpoint.username is not None or endpoint.fragment:
            raise ValueError("外部App ServerのURLを認識できません")
        token = os.environ.get(token_env) if token_env else None
        if token_env and not token:
            raise ValueError("外部App Serverの認証用環境変数が空です")
        tls = endpoint.scheme == "wss"
        reader, writer = await asyncio.open_connection(endpoint.hostname, endpoint.port or (443 if tls else 80), ssl=tls)
        try:
            channel = RpcChannel(reader, writer, client=True)
            target = (endpoint.path or "/") + ("?" + endpoint.query if endpoint.query else "")
            await channel.handshake(token=token, host=endpoint.netloc, target=target)
            return await _fetch_catalog_from_channel(channel)
        finally:
            writer.close()
            await writer.wait_closed()


def resolve_series_model(binary: pathlib.Path, family: str, target: CatalogTarget) -> str:
    """実際の起動と同じ実行体または接続先から、系列の候補だけを取得する。"""
    if target.profile or (target.ignore_user_config and target.remote is None):
        # 現行のApp ServerはTUIのprofile・設定無視指定を受け取れない。
        # 異なる認証やproviderの一覧へ置き換えない。
        raise ValueError("指定されたprofileまたは設定無視条件でモデル一覧を取得できません")
    if target.remote is not None and target.settings:
        raise ValueError("外部App Serverと組み合わせた設定指定でモデル一覧を取得できません")
    try:
        catalog = (
            asyncio.run(_remote_model_catalog(binary, target.remote, target.token_env))
            if target.remote is not None
            else codex_models.list_models_at((str(binary), *target.settings, "app-server", "--stdio"), cwd=target.cwd)
        )
    except (EOFError, OSError, ProtocolError, ValueError) as error:
        # 接続先やRPCの診断に認証情報が含まれる場合がある。
        raise ValueError("モデル一覧を取得できません") from error
    return codex_models.latest_model(family, catalog)


def _with_cwd(arguments: Sequence[str], cwd: str) -> list[str]:
    # --remote接続のTUIは自身のcwdを送らず、管理daemonのcwdで会話を始める。
    # 通常起動と同じく起動したディレクトリを作業ルートにするため、指定が無ければ補う。
    options = arguments[: arguments.index("--")] if "--" in arguments else arguments
    if any(token in {"-C", "--cd"} or token.startswith(("-C", "--cd=")) for token in options):
        return list(arguments)
    return ["--cd", cwd, *arguments]


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
    async with RecoveryBridge(str(binary), environment, codex_runtime.codex_home(), timeout=timeout) as bridge:
        environment[_TOKEN_ENV] = bridge.token
        command = _with_cwd(arguments, os.getcwd())
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
                    process_identity.start_id(identity)
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
            conversation = bridge.state.conversation
            if code == 0 or code in _INTERRUPTED or not isinstance(conversation, RunningTurn):
                return code if code >= 0 else 128 - code
            current = bridge.daemon_generation()
            generation = bridge.connected_generation
            if current is None or generation is None or current == generation:
                return code if code >= 0 else 128 - code
            bridge.prepare_tui_resume()
            command = _resume_arguments(arguments, conversation.thread_id, conversation.cwd)


def main(argv: list[str] | None = None) -> int:
    """通常TUIだけを管理し、その他の公開コマンドはnativeへそのまま渡す。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    arguments = list(sys.argv[1:] if argv is None else argv)
    binary = codex_runtime.find_runtime_launcher(windows=sys.platform == "win32")
    if binary is None:
        print("管理対象のCodexが見つからない。update-dotfilesでCodexを導入して再実行する", file=sys.stderr)
        return 1
    return launch_resolved(binary, arguments)


def launch_resolved(binary: pathlib.Path, arguments: list[str]) -> int:
    """解決済みの実行体で、通常起動の復旧と管理操作の処理を共有する。"""
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
