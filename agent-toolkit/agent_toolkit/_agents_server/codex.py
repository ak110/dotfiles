"""Codex App ServerとのJSON-RPC通信を担当する。

App Serverのwire protocolはJSON-RPC 2.0に準拠するが、stdioの各行では
``jsonrpc``フィールドを省略できる（OpenAI公式資料）。この実装では送信時に
``jsonrpc``を省略し、受信時は存在していても受理する。

公式資料: https://developers.openai.com/codex/app-server
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import sys
import time
import typing
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any

from agent_toolkit._agents_server import (
    api_credits,
    child_environment,
    codex_providers,
    codex_resume_state,
    compaction_metrics,  # pylint: disable=wrong-import-position
    context_usage,
    engine_availability,
    lifetime,
    result_contract,
    result_projection,
    resume_waits,
    session_errors,
    session_identity,
    shared_roots,  # pylint: disable=wrong-import-position
    wait_output_tracking,
)
from agent_toolkit._agents_server import (
    plugin_root as plugin_roots,
)
from agent_toolkit._agents_server import state as shared_state  # pylint: disable=wrong-import-position
from agent_toolkit._agents_server.file_operations import finish_task, run_file_operation
from agent_toolkit._agents_server.input_validation import validate_cwd, validate_model_effort, validate_prompt
from agent_toolkit._agents_server.launch_prompts import (
    AUTO_RESUME_NOTICE,
    LAUNCH_SYSTEM_PROMPTS,
    LIGHTWEIGHT_LAUNCH_KINDS,
    SUBAGENT_BASE_INSTRUCTIONS,
    cli_runtime_instructions,
    python_runtime_instructions,
)
from agent_toolkit._agents_server.session_errors import SessionInitializationTimeoutError
from agent_toolkit._agents_server.state import (
    TERMINAL_STATUSES,
    LaunchKind,
    ModelCandidate,
    ResumePrompt,
    SessionState,
    append_bounded,
    begin_reply,
    initialize_turn,
    utc_now,
)
from agent_toolkit._agents_server.wait_output_tracking import consume_agents_server_tool_result
from agent_toolkit._atk import codex_connection
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk.usage_limit_policy import should_wait
from agent_toolkit._common import (
    codex_models,
    codex_usage_limit,
    inherited_venv,
    mcp_environment,
    plugin_environment,
    process_tree,  # pylint: disable=wrong-import-position
)
from agent_toolkit._common import delegated_session as _delegated_session
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.shell_tokens import is_agents_wait_command

if typing.TYPE_CHECKING:
    import psutil

_LOG = logging.getLogger("agent-toolkit.agents-server.codex")

APP_SERVER_COMMAND = ("codex", "app-server", "--stdio", "-c", "thread_unload_delay_secs=0")
# Codexホストでは親の作業ディレクトリが版数付きplugin cacheとなり、
# plugin更新で消失すると生存中のApp Serverが設定を読み込めないため継承しない。
APP_SERVER_WORKING_DIRECTORY = str(Path.home())
DEFAULT_WAIT_TIMEOUT = 300.0
# App Serverのstdio用StreamReaderのbuffer上限と、stdoutを1回に読み取るchunk長を兼ねる値（バイト）。
# JSON-RPC recordの最大値には使わず、この長さを超える行もchunkを結合して復元する。
APP_SERVER_STREAM_LIMIT_BYTES = 8 * 1024 * 1024
APP_SERVER_STDERR_LIMIT_CHARS = 4000
APP_SERVER_EXIT_DIAGNOSTIC_TIMEOUT = 1.0
# `item/started`のうちモデル出力に数えないitem種別。
# `userMessage`はモデル呼び出しより前に届く入力の記録で、利用上限の失敗はその後に起こり得る。
# codex-cli 0.157.0の通常のturnでは`userMessage`の後に`reasoning`が届いた（2026年9月26日の実行で確認）。
_NON_MODEL_OUTPUT_ITEM_TYPES = frozenset({"userMessage", "hookPrompt", "contextCompaction"})


def _current_service_tier() -> str:
    """turnの開始時の実効設定を読み、Codex側の設定を継承せず速度を明示する。"""
    return "priority" if _atk_config.resolve_mutable_setting("codex_fast_mode") == "true" else "default"


_BOUNDARY_PATH_PATTERN = re.compile(rf'<{AUTO_INSERTED_ELEMENT}\b[^>]*\spath="([^"]*)"')
"""`atk-auto`要素の開始タグから`path`属性の値を取り出す。"""


def _embedded_rule_paths() -> set[str]:
    """Codexが読む全体指示ファイルが`atk-auto`要素で埋め込む規範の`path`属性の集合を返す。

    全体指示ファイルは`CODEX_HOME`が設定済みなら`$CODEX_HOME/AGENTS.md`、未設定なら`~/.codex/AGENTS.md`とする。
    ファイルが無いか読めない場合は空集合を返し、呼び出し側は`~/.claude/rules/`配下の全ファイルを委譲先へ渡す。
    """
    agents_md = _host_homes.codex_home() / "AGENTS.md"
    try:
        text = agents_md.read_text(encoding="utf-8")
    except OSError, UnicodeError:
        return set()
    return set(_BOUNDARY_PATH_PATTERN.findall(text))


def _user_rules_instructions() -> str:
    """`~/.claude/rules/`配下の規範ファイルのうち、全体指示ファイルが埋め込まないものの本文を連結して返す。

    Claude backendの委譲先はユーザー設定の読込元から同じファイル群を受け取る。Codex backendの委譲先へも
    同じ集合を渡し、engineの選択で委譲先が従う規範が変わらないようにする。
    ファイル先頭の`atk-auto`要素の`path`属性が全体指示ファイルの埋め込みと一致するファイルは、同じ本文が
    既に届くため除く。ユーザーの編集を次の起動と再開から反映するため、呼び出しのたびに読み直す。
    対象が無い場合は空文字列を返す。
    """
    rules_dir = _host_homes.claude_config_dir() / "rules"
    if not rules_dir.is_dir():
        return ""
    embedded = _embedded_rule_paths()
    sections: list[str] = []
    for path in sorted(rules_dir.rglob("*.md")):
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8").rstrip("\n")
        except (OSError, UnicodeError) as exc:
            _LOG.warning("ユーザー規範を読めないため委譲先へ渡しません: path=%s error=%s", path, exc)
            continue
        first_line = body.split("\n", 1)[0]
        match = _BOUNDARY_PATH_PATTERN.match(first_line)
        if match is not None and match.group(1) in embedded:
            continue
        sections.append(auto_message(body, source="agents-server", kind="user-rules", attributes={"path": str(path)}))
    if not sections:
        return ""
    return (
        "\n\n委譲元のホストで`~/.claude/rules/`に置かれた規範ファイルを次に示す。"
        "各ファイルが定める適用範囲に作業が入る条文に従う。各要素の`path`属性のファイルは読了済みとして扱う。\n"
        + "\n".join(sections)
    )


def _developer_instructions(launch_kind: LaunchKind) -> str:
    """委譲先の役割、このprocessが起動時に確定した配布物root、ユーザーが置いた規範ファイルの本文を1つの指示として返す。"""
    plugin_root = plugin_roots.SERVER_PLUGIN_ROOT
    runtime = f"\n{python_runtime_instructions()}" if launch_kind in LIGHTWEIGHT_LAUNCH_KINDS else ""
    return (
        f"{LAUNCH_SYSTEM_PROMPTS[launch_kind]}\n{AUTO_RESUME_NOTICE}{runtime}\n{cli_runtime_instructions(plugin_root)}\n\n"
        f"agent-toolkit plugin root: {plugin_root}\n"
        "担当自身が使うagent-toolkitのskillと実行基盤のplugin内部資源は、この実在する絶対パスを起点に読む。"
        "`<役割名>.subagent.md`や別hostのcache版数から別のplugin rootを組み立てない。"
        "成果物と、その成果物が参照する実装・文書・CLIの存否と挙動は、"
        "受領した絶対パスと対象worktreeまたは保存版から判定する。"
        "保持版での不在を対象版の不在の根拠にしない。"
        f"{_user_rules_instructions()}"
    )


class AppServerError(session_errors.DelegateBackendError):
    """App Serverとの通信または要求検証に失敗した。"""


class StartCandidateUnavailable(AppServerError):
    """モデルの実行開始前に候補の不成立が確定し、資源も回収済みであることを表す。"""

    def __init__(self, reason: str, *, scope: typing.Literal["candidate", "launch_engine"] = "candidate") -> None:
        super().__init__(reason)
        self.reason = reason
        # launch_engineは同じ起動要求内のengine共通能力の不成立で、永続記録へ渡さない。
        self.scope: typing.Literal["candidate", "launch_engine"] = scope


class ResourceReleaseError(session_errors.ActionableRuntimeError):
    """委譲sessionが所有するApp Serverまたはその子孫の終了を確認できず、資源を解放済みにできない。"""

    def __init__(self, session_id: str, residual: list[str]) -> None:
        super().__init__(
            f"Codex App Server resources for session {session_id} are still running: {'; '.join(residual)}",
            next_action=(
                "資源は未解放のまま所有側に残っている。同じ`session_id`へ`stop`を再発行して解放を再試行する。"
                "再試行でも残る場合は、示したPIDのプロセスが終了できない原因を調べる"
            ),
        )
        self.session_id = session_id
        self.residual = residual


class JsonRpcResponseError(AppServerError):
    """App ServerがJSON-RPC error responseを返した。"""

    def __init__(self, method: str, code: Any, message: str, data: Any = None) -> None:
        super().__init__(f"{method}: {message}")
        self.method = method
        self.code = code
        self.data = data


class TurnStartResponseError(AppServerError):
    """turn/startの応答形式が不正である。要求拒否を確認できないため、client生存中は受理状態が曖昧である。"""


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _thread_id_from(params: Any) -> str | None:
    if not isinstance(params, dict):
        return None
    for key in ("threadId", "thread_id", "session_id"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _turn_id_from(params: Any) -> str | None:
    if not isinstance(params, dict):
        return None
    for key in ("turnId", "turn_id"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _response_turn_id(response: Any) -> str | None:
    """JSON-RPC responseからsteer対象turn IDを取り出す。"""
    if not isinstance(response, dict):
        return None
    turn_id = response.get("turnId")
    return turn_id if isinstance(turn_id, str) and turn_id else None


def _client_pid(client: Any) -> int | None:
    """App Serverのクライアントが起動した子プロセスのpidを返す。起動前と子プロセスを持たない代替では`None`を返す。"""
    pid = getattr(getattr(client, "process", None), "pid", None)
    return pid if isinstance(pid, int) and not isinstance(pid, bool) else None


class JsonRpcProcess:
    """stdio App Serverとの要求・応答を多重化するクライアント。"""

    _close_task: asyncio.Task[list[str]] | None = None

    async def __aenter__(self) -> typing.Self:
        return self

    async def __aexit__(self, *_exception: object) -> None:
        residual = await self.close()
        if residual:
            raise ResourceReleaseError("unassigned", residual)

    def __init__(
        self,
        on_notification: Callable[[dict[str, Any]], Awaitable[None]],
        on_server_request: Callable[[dict[str, Any]], Awaitable[None]],
        on_failure: Callable[[BaseException], Awaitable[None]] | None = None,
        *,
        root_session_id: str | None = None,
        launch_kind: LaunchKind | None = None,
        handoff_prompt: str = "",
    ) -> None:
        self._on_notification = on_notification
        self._on_server_request = on_server_request
        self._on_failure = on_failure
        self._root_session_id = root_session_id
        self._launch_kind = launch_kind
        self._handoff_prompt = handoff_prompt
        self.process: asyncio.subprocess.Process | None = None
        self.parent_environment: Mapping[str, str] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._reader_failure: BaseException | None = None
        self._stderr_text = ""
        self._initialization_stage = "created"
        self._received_message_types: dict[str, int] = {}
        self._residual: list[psutil.Process] = []

    def initialization_diagnostic(self) -> dict[str, Any]:
        """初期化の到達段階と、長さに上限を設けた子プロセスの診断値を返す。"""
        process = self.process
        return {
            "stage": self._initialization_stage,
            "received_message_types": dict(sorted(self._received_message_types.items())),
            "child_pid": None if process is None else process.pid,
            "stderr": self._stderr_text.strip(),
        }

    async def start(self) -> None:
        """子プロセスを起動し、initialize/initializedを完了する。"""
        if self.process is not None:
            return
        self._initialization_stage = "starting_process"
        try:
            environment = plugin_environment.cli_environment(plugin_roots.SERVER_PLUGIN_ROOT, child_environment.inherited())
            inherited_venv.strip_inherited_venv(environment)
            environment.pop(_delegated_session.DELEGATED_SESSION_ENV, None)
            # 委譲先のhookが軽量起動を判別する。turnを実行しない照会用の接続は起動区分を持たない。
            environment.pop(_delegated_session.LAUNCH_KIND_ENV, None)
            environment[_delegated_session.PLUGIN_ROOT_ENV] = str(plugin_roots.SERVER_PLUGIN_ROOT)
            if self._launch_kind is not None:
                environment[_delegated_session.LAUNCH_KIND_ENV] = self._launch_kind
            environment.pop(_delegated_session.HANDOFF_PATH_ENV, None)
            environment.update(_delegated_session.handoff_environment(self._handoff_prompt))
            owner_session_id = self._root_session_id
            if owner_session_id is not None:
                environment[_delegated_session.OWNER_SESSION_ENV] = owner_session_id
            self.parent_environment = environment
            self.process = await asyncio.create_subprocess_exec(
                *APP_SERVER_COMMAND,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=APP_SERVER_STREAM_LIMIT_BYTES,
                cwd=APP_SERVER_WORKING_DIRECTORY,
                env=environment,
            )
        except OSError as exc:
            _LOG.exception("Codex App Serverを起動できません: command=%s", " ".join(APP_SERVER_COMMAND))
            raise AppServerError(f"failed to start {' '.join(APP_SERVER_COMMAND)}: {exc}") from exc
        _LOG.info(
            "Codex App Serverを起動しました: command=%s cwd=%s pid=%s",
            " ".join(APP_SERVER_COMMAND),
            APP_SERVER_WORKING_DIRECTORY,
            self.process.pid,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._initialization_stage = "process_started"
        _LOG.info("Codex App Server初期化段階: %s", self.initialization_diagnostic())
        try:
            # 子プロセスが応答を返さないまま生存する場合、読取taskが失敗しても要求の応答futureは解消しない。
            async with asyncio.timeout(shared_state.SESSION_INITIALIZATION_TIMEOUT):
                self._initialization_stage = "initialize_requested"
                initialize_result = await self.request(
                    "initialize",
                    {
                        "clientInfo": {"name": "agent-toolkit-codex-app-server", "version": "1.0"},
                        "capabilities": {},
                    },
                )
                self._initialization_stage = "initialize_received"
                _LOG.info("Codex App Serverのinitialize応答を受信しました: keys=%s", sorted(initialize_result))
                await self.notify("initialized", {})
                self._initialization_stage = "initialized_sent"
                _LOG.info("Codex App Server初期化完了: %s", self.initialization_diagnostic())
        except TimeoutError as exc:
            diagnostic = self.initialization_diagnostic()
            _LOG.error("Codex App Server初期化timeout: diagnostic=%s", diagnostic)
            await self.close()
            raise SessionInitializationTimeoutError(
                f"Codex App Server did not complete initialize within {shared_state.SESSION_INITIALIZATION_TIMEOUT:.0f}s: "
                f"command={' '.join(APP_SERVER_COMMAND)}; diagnostic={diagnostic}"
            ) from exc
        except BaseException as exc:
            _LOG.error(
                "Codex App Server初期化失敗: exception_type=%s exception=%s diagnostic=%s",
                type(exc).__name__,
                exc,
                self.initialization_diagnostic(),
            )
            await self.close()
            raise

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """JSON-RPC requestを送り、対応するIDの応答を返す。"""
        if self.process is None or self.process.stdin is None:
            raise AppServerError("Codex App Server is not running")
        if self._closed or self._reader_failure is not None:
            raise AppServerError("Codex App Server client is closed")
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params or {}})
            if on_sent is not None:
                on_sent()
            response = await future
        finally:
            self._pending.pop(request_id, None)
        if "error" in response:
            error = response.get("error")
            if isinstance(error, dict):
                message = _as_text(error.get("message")) or "JSON-RPC request failed"
                raise JsonRpcResponseError(method, error.get("code"), message, error.get("data"))
            message = "JSON-RPC request failed"
            raise JsonRpcResponseError(method, None, message)
        result = response.get("result", {})
        return result if isinstance(result, dict) else {}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """JSON-RPC notificationを送る。"""
        await self._send({"method": method, "params": params or {}})

    async def _send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None or self._closed:
            raise AppServerError("Codex App Server stdin is unavailable")
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                self.process.stdin.write(encoded)
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionError) as exc:
                diagnostic = self.initialization_diagnostic()
                _LOG.error(
                    "Codex App Serverへの書込失敗: exception_type=%s exception=%s diagnostic=%s",
                    type(exc).__name__,
                    exc,
                    diagnostic,
                )
                raise AppServerError(f"failed to write to Codex App Server: {exc}; diagnostic={diagnostic}") from exc

    @property
    def closed(self) -> bool:
        """子プロセスとの接続が終了処理済みであるかを返す。"""
        return self._closed

    @property
    def reader_failure(self) -> BaseException | None:
        """Stdout readerが検出した失敗を返す。"""
        return self._reader_failure

    async def send(self, message: dict[str, Any]) -> None:
        """JSON-RPC応答を接続へ送る。"""
        await self._send(message)

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        buffer = bytearray()
        try:
            while True:
                chunk = await self.process.stdout.read(APP_SERVER_STREAM_LIMIT_BYTES)
                if not chunk:
                    if buffer:
                        await self._dispatch_stdout_line(bytes(buffer))
                    error = await self._stdout_closed_error()
                    _LOG.error("%s", error)
                    raise error
                buffer.extend(chunk)
                while (newline_index := buffer.find(b"\n")) >= 0:
                    raw_line = bytes(buffer[:newline_index])
                    del buffer[: newline_index + 1]
                    await self._dispatch_stdout_line(raw_line)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._reader_failure = exc
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(AppServerError(str(exc)))
            if self._on_failure is not None:
                with contextlib.suppress(Exception):
                    await self._on_failure(exc)

    async def _dispatch_stdout_line(self, raw_line: bytes) -> None:
        """復元済みの1 JSON-RPC recordを既存の応答・要求・通知処理へ渡す。"""
        try:
            message = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppServerError(f"invalid Codex App Server JSON line: {exc}") from exc
        if not isinstance(message, dict):
            return
        if "id" in message and ("result" in message or "error" in message):
            message_type = "response"
        elif "id" in message and isinstance(message.get("method"), str):
            message_type = "server_request"
        else:
            message_type = str(message.get("method", "notification"))
        self._received_message_types[message_type] = self._received_message_types.get(message_type, 0) + 1
        if "id" in message and ("result" in message or "error" in message):
            request_id = message.get("id")
            future = self._pending.get(request_id) if isinstance(request_id, int) else None
            if future is not None and not future.done():
                future.set_result(message)
            return
        if "id" in message and isinstance(message.get("method"), str):
            await self._on_server_request(message)
            return
        if isinstance(message.get("method"), str):
            await self._on_notification(message)

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace")
                self._stderr_text = append_bounded(
                    self._stderr_text,
                    text,
                    APP_SERVER_STDERR_LIMIT_CHARS,
                )
                print(
                    text,
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
        except OSError as exc:
            _LOG.debug("Codex App Server stderrの読取を終了しました: %s", exc)

    async def _stdout_closed_error(self) -> AppServerError:
        """子プロセス終了時の診断情報を、待ち時間と長さに上限を設けて収集する。"""
        assert self.process is not None
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                self.process.wait(),
                timeout=APP_SERVER_EXIT_DIAGNOSTIC_TIMEOUT,
            )
        stderr_task = self._stderr_task
        if stderr_task is not None and stderr_task is not asyncio.current_task():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(stderr_task),
                    timeout=APP_SERVER_EXIT_DIAGNOSTIC_TIMEOUT,
                )

        command = " ".join(APP_SERVER_COMMAND)
        message = f"Codex App Server stdout closed: command={command}; returncode={self.process.returncode}"
        stderr = self._stderr_text.strip()
        if stderr:
            message = f"{message}; stderr={stderr}"
        return AppServerError(message)

    async def close(self) -> list[str]:
        """自身が起動した子プロセスと、その子孫プロセスを終了し、関連taskを回収する。

        対象は自身が起動したプロセスからの子孫関係だけで特定する。
        戻り値は資源を保持したまま残った対象（App Server自身を含む）の記述であり、空なら全て終了している。
        残った対象は保持し、同じ接続への再度の`close`で回収を再試行する。
        """
        task = self._close_task
        if task is None or task.done() and (task.cancelled() or task.exception() is not None or task.result()):
            task = self._close_task = asyncio.create_task(self._close())
        return await finish_task(task)

    async def _close(self) -> list[str]:
        if self._closed and self.process is None and not self._residual:
            return []
        process = self.process
        if not self._closed:
            self._closed = True
            # 子孫の列挙は終了要求より前に行う。App Serverが終了すると子孫の親が変わり、
            # 保持しているPIDを起点に辿れなくなるためである。
            self._residual = await run_file_operation(
                process_tree.collect_descendants, None if process is None else process.pid
            )
            tasks = tuple(
                task
                for task in (self._reader_task, self._stderr_task)
                if task is not None and task is not asyncio.current_task()
            )
            self._reader_task = None
            self._stderr_task = None
            for task in tasks:
                if not task.done():
                    task.cancel()
        else:
            tasks = ()
        if process is not None:
            with contextlib.suppress(OSError, ProcessLookupError):
                if process.returncode is None:
                    process.terminate()
            if process.returncode is None:
                with contextlib.suppress(asyncio.TimeoutError):
                    await lifetime.wait_for_shutdown((asyncio.create_task(process.wait()),), timeout=5)
            if process.returncode is None:
                with contextlib.suppress(OSError, ProcessLookupError):
                    process.kill()
                with contextlib.suppress(asyncio.TimeoutError):
                    await lifetime.wait_for_shutdown((asyncio.create_task(process.wait()),), timeout=5)
        if tasks:
            await lifetime.wait_for_shutdown(tasks)
        if process is not None:
            _LOG.info("Codex App Serverを終了しました: returncode=%s", process.returncode)
        self._residual = await asyncio.to_thread(process_tree.reclaim_processes, self._residual)
        residual = [process_tree.describe(item) for item in self._residual]
        if process is not None and process.returncode is not None:
            self.process = None
        elif process is not None:
            residual.insert(0, f"{process.pid}: {' '.join(APP_SERVER_COMMAND)}")
        process_tree.log_residual(residual, context="codex app-server")
        error = AppServerError("Codex App Server client closed")
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        return residual


class AppServerManager:
    """Codex App Serverと共有session状態を管理する。

    App Serverは委譲sessionごとに起動し、そのsessionの開始・継続・再開・中断を所有する接続へ送る。
    App Serverはthreadごとのstdio MCPサーバーを子孫として起動し、購読解除の後も猶予の間は生存させる。
    このためsessionの資源解放は、所有するApp Serverと子孫の終了を確認して完了とする。
    """

    _close_task: asyncio.Task[None] | None = None

    START_FAILURE_EXCLUDES_CANDIDATE: typing.ClassVar[bool] = False
    """起動の例外は候補を変えても結果が変わらないため、次の候補へ進まずに委譲元へ返す。"""
    INTERRUPT_REQUIRES_TURN_ID: typing.ClassVar[bool] = True
    """`turn/interrupt`はturnの識別子を要するため、中断の要求はturnの開始を待ってから送る。"""
    ORPHAN_TAKEOVER: typing.ClassVar[bool] = True
    """所有者のいない`running`の登録簿の記録を、`thread/read`の結果から終了として引き継げる。"""

    async def __aenter__(self) -> typing.Self:
        return self

    async def __aexit__(self, *_exception: object) -> None:
        await self.close()

    @staticmethod
    def unavailable_reason(session: SessionState) -> str | None:
        """終了したsessionがengineの可用性を理由に失敗した場合、その除外理由を返す。

        接続先がモデルIDを受け付けなかった失敗も、別の候補なら結果が変わるため除外理由にする。
        """

        def model_rejected(error: Mapping[str, Any]) -> str | None:
            if engine_availability.rejected_parameter(error) == "model":
                return engine_availability.ENGINE_MODEL_REJECTED_REASON
            return None

        return engine_availability.unavailable_reason(session, other_reason=model_rejected)

    @staticmethod
    def excludes_with_recorded_reason(reason: str) -> bool:
        """sandboxの旧記録と、API設定済みの利用上限の旧記録は現在の利用可否で判定し直す。"""
        if reason.partition(":")[0] in {
            "exploreSandboxNamespaceDenied",
            "exploreSandboxRpcError",
            "exploreSandboxTimeout",
            "exploreSandboxInitializationFailed",
            "exploreSandboxProbeFailed",
            "exploreSandboxInvalidResponse",
            "exploreSandboxCommandFailed",
        }:
            return False
        return reason != "usageLimitExceeded" or not codex_providers.has_provider_configuration()

    def __init__(
        self,
        sessions: dict[str, SessionState] | None = None,
        condition: asyncio.Condition | None = None,
        publish_registry: bool = False,
        *,
        root_session_id: str | None = None,
        validate_session_id: session_identity.IdentityValidator | None = None,
    ) -> None:
        self._clients: dict[str, JsonRpcProcess] = {}
        """委譲session（親thread）の識別子から、そのsessionが所有するApp Server接続への対応。"""
        self._unassigned_clients: dict[int, JsonRpcProcess] = {}
        """sessionへの所有移譲前と、短命な照会中の接続。解放失敗後も再試行できるよう保持する。"""
        self.sessions = sessions if sessions is not None else {}
        self._condition = condition if condition is not None else asyncio.Condition()
        self._publish_registry = publish_registry
        self._root_session_id = root_session_id
        self._validate_session_id = validate_session_id or session_identity.identity_validator(self.sessions, root_session_id)
        self._lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._writer_session_ids: dict[str, str] = {}
        self._resume_close_events: dict[str, asyncio.Event] = {}

    @staticmethod
    def _agents_server_config(owner_session_id: str, writer_session_id: str, client: Any = None) -> dict[str, Any]:
        """内側のMCPサーバーへ状態ファイルの書込主体を配送する設定を返す。

        `env_vars`は内側MCPを起動するApp Server（`client`）へ実際に渡した環境から選ぶ。
        """
        plugin_root = plugin_roots.SERVER_PLUGIN_ROOT
        parent_environment = getattr(client, "parent_environment", None)
        if parent_environment is None:
            parent_environment = dict(os.environ)
            inherited_venv.strip_inherited_venv(parent_environment)
        return {
            "mcp_servers": {
                "agents_server": {
                    "command": "uv",
                    "args": [
                        "run",
                        "--no-config",
                        "--no-project",
                        "--python",
                        "3.15",
                        "python",
                        str(plugin_root / "scripts" / "launch.py"),
                        "agent_toolkit/agents_server_mcp.py",
                    ],
                    "env": {
                        _delegated_session.OWNER_SESSION_ENV: owner_session_id,
                        "AGENT_TOOLKIT_STATUS_HOST_SESSION": writer_session_id,
                        _delegated_session.PLUGIN_ROOT_ENV: str(plugin_root),
                        plugin_environment.RUNTIME_DATA_ENV: str(plugin_environment.runtime_data(os.environ)),
                    },
                    "env_vars": list(mcp_environment.runtime_environment_variable_names(parent_environment)),
                    "default_tools_approval_mode": "approve",
                }
            }
        }

    @staticmethod
    def _base_thread_config(*, lightweight: bool) -> dict[str, Any]:
        """thread開始・再開のリクエストへ常に載せる設定を返す。

        `bypass_hook_trust`は、hookの定義が変わった後もcodexが承認済みの記録を要求せずにhookを実行するために渡す。
        `agents_server`が開始する委譲先は対話UIを持たず、承認要求へ応答する主体が存在しないため、
        このキーが無いとプラグインの更新のたびに委譲先が起動しない。
        この制約は`launch_kind`によらず同じであるため、軽量起動と通常の委譲で分けない。
        `bypass_hook_trust`は`codex app-server`のコマンドラインオプションとしても`-c`による設定上書きとしても受理されず、
        `thread/start`系リクエストの`config`だけが受理する。
        """
        # 依頼の保持を通常の会話圧縮へ統一し、要約なし切替を提供するホーム設定を上書きする。
        features: dict[str, Any] = {"context_management": {"experimental_mode": False}}
        config: dict[str, Any] = {
            "bypass_hook_trust": True,
            "features": features,
        }
        if lightweight:
            config["project_doc_max_bytes"] = 0
        return config

    def _thread_config(
        self, session_id: str | None = None, *, lightweight: bool, client: Any = None
    ) -> tuple[dict[str, Any], str | None, str | None]:
        """thread開始・再開に必要な設定と書込主体を返す。"""
        config = self._base_thread_config(lightweight=lightweight)
        owner_session_id = self._root_session_id
        if owner_session_id is None:
            return config, None, None
        writer_session_id = self._writer_session_ids.get(session_id) if session_id is not None else None
        if writer_session_id is None:
            writer_session_id = uuid.uuid4().hex
            if session_id is not None:
                self._writer_session_ids[session_id] = writer_session_id
        config.update(self._agents_server_config(owner_session_id, writer_session_id, client))
        return config, owner_session_id, writer_session_id

    @staticmethod
    def _explore_config(config: dict[str, Any], client: Any) -> dict[str, Any]:
        """スキル非配送を保ち、読取MCPの定義を直接の設定として渡す。"""
        root = plugin_roots.SERVER_PLUGIN_ROOT
        pyfltr = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["pyfltr"]
        pyfltr["args"] = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(root)) for arg in pyfltr.get("args", [])]
        environment = getattr(client, "parent_environment", None) or os.environ
        pyfltr["env_vars"] = list(mcp_environment.runtime_environment_variable_names(environment))
        pyfltr["default_tools_approval_mode"] = "approve"
        # pluginを無効にする探索にも、出力省略の回収案内をPostToolUse（Bash）から届ける。
        # 回収可能な省略を理由に続行不能を返した委譲先に探索担当が含まれていた。資料本文は返さない。
        # shell・writeはpluginのhookをそのまま受けるため、ここで重ねて接続しない。
        groups = json.loads((root / "hooks/hooks.codex.json").read_text(encoding="utf-8"))["hooks"]["PostToolUse"]
        hooks = {
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {**handler, "command": handler["command"].replace("${PLUGIN_ROOT}", str(root))}
                        for group in groups
                        for handler in group["hooks"]
                    ],
                }
            ],
        }
        return {
            **config,
            "features": {**config.get("features", {}), "plugins": False, "hooks": True},
            "hooks": hooks,
            "mcp_servers": {**config.get("mcp_servers", {}), "pyfltr": pyfltr},
        }

    def _schedule(self, awaitable: Coroutine[Any, Any, None]) -> None:
        """同じイベントループで回収する管理対象taskを登録する。"""
        task: asyncio.Task[None] = asyncio.create_task(awaitable)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _ensure_client(self, launch_kind: LaunchKind | None = None, handoff_prompt: str = "") -> JsonRpcProcess:
        """新しいApp Serverを起動して初期化した接続を返す。

        `launch_kind`はturnを実行するsessionの起動区分で、App Serverの子環境を通じて委譲先のhookへ届く。

        接続は生成直後からmanagerが保持し、呼び出し元の文脈でsessionへ移譲するか、
        用途を終えた時点で`_discard_client`で終了させる。解放に失敗した接続はmanagerの停止で再試行する。
        サーバー要求への応答と接続障害の反映は、この接続が所有するsessionだけを対象にする。
        """
        client: JsonRpcProcess | None = None

        async def on_server_request(message: dict[str, Any]) -> None:
            assert client is not None
            await self._handle_server_request(message, client)

        async def on_failure(error: BaseException) -> None:
            assert client is not None
            await self._handle_client_failure(error, client)

        client = JsonRpcProcess(
            self._handle_notification,
            on_server_request,
            on_failure,
            root_session_id=self._root_session_id,
            launch_kind=launch_kind,
            handoff_prompt=handoff_prompt,
        )
        self._unassigned_clients[id(client)] = client
        async with contextlib.AsyncExitStack() as resources:
            resources.push_async_callback(self._discard_client, client)
            await client.start()
            resources.pop_all()
        return client

    async def _discard_client(self, client: Any) -> None:
        """sessionへ登録しなかった接続を終了し、全て回収できた場合に保持を外す。"""
        residual = await client.close()
        if residual:
            raise session_errors.ActionableRuntimeError(
                f"Codex App Server resources before session assignment are still running: {'; '.join(residual)}",
                next_action="MCPサーバーの停止時に再度解放する。残存するPIDの終了を妨げる原因を調べる",
            )
        self._unassigned_clients.pop(id(client), None)

    def _owned_client(self, session: SessionState) -> Any:
        """sessionが所有する生存中の接続を返す。無い場合は`None`を返す。"""
        client = self._clients.get(session.session_id)
        if client is None or getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
            return None
        return client

    async def _acquire_owned_client(self, session_id: str, launch_kind: LaunchKind, handoff_prompt: str = "") -> JsonRpcProcess:
        """sessionが所有する生存中の接続を返し、無ければ新しいApp Serverを起動して所有させる。

        接続が失われていた場合は、その接続の残りの資源を回収してから起動し直す。回収できなければ
        `ResourceReleaseError`を送出し、古い接続を所有させたまま残す。
        """
        client = self._clients.get(session_id)
        if client is not None and not getattr(client, "closed", False) and getattr(client, "reader_failure", None) is None:
            return client
        if client is not None:
            await self._release_owned_client(session_id)
        original = self.sessions.get(session_id)
        client = await self._ensure_client(launch_kind, original.prompt if original else handoff_prompt)
        self._clients[session_id] = client
        self._unassigned_clients.pop(id(client), None)
        return client

    async def _release_owned_client(self, session_id: str) -> None:
        """sessionが所有するApp Serverと子孫を終了させ、全ての終了を確認してから所有を外す。"""
        client = self._clients.get(session_id)
        if client is None:
            return
        residual = await client.close()
        if residual:
            raise ResourceReleaseError(session_id, list(residual))
        if self._clients.get(session_id) is client:
            del self._clients[session_id]

    async def read_thread_turns(self, session_id: str) -> list[tuple[str, str]]:
        """保存済みthreadの全turnを、識別子と状態（`inProgress`・`completed`・`failed`・`interrupted`）の組で時系列順に返す。

        再起動前のプロセスが終了を公開できなかった記録について、turnが終わったかを委譲先CLIの記録で確かめるために使う。
        応答がthreadとturnの一覧を持たない場合は`AppServerError`を送出する。
        照会には専用のApp Serverを起動し、応答を得た後に終了させる。
        """
        async with contextlib.AsyncExitStack() as resources:
            client = await self._ensure_client()
            resources.push_async_callback(self._discard_client, client)
            response = await client.request("thread/read", {"threadId": session_id, "includeTurns": True})
        thread = response.get("thread") if isinstance(response, dict) else None
        turns = thread.get("turns") if isinstance(thread, dict) else None
        if not isinstance(turns, list):
            raise AppServerError("thread/read returned no thread.turns")
        result: list[tuple[str, str]] = []
        for turn in turns:
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not isinstance(turn.get("status"), str):
                raise AppServerError("thread/read returned a turn without id or status")
            result.append((turn["id"], turn["status"]))
        return result

    async def list_models(self) -> list[dict[str, Any]]:
        """専用のApp Server接続から表示対象の全モデルページを取得し、接続を終了させる。"""
        async with contextlib.AsyncExitStack() as resources:
            client = await self._ensure_client()
            resources.push_async_callback(self._discard_client, client)
            return await codex_models.fetch_catalog(client.request)

    async def start(
        self,
        prompt: str,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
        *,
        model_type: str | None = None,
        launch_kind: LaunchKind = "delegate",
        excluded_candidates: frozenset[ModelCandidate] = frozenset(),
    ) -> SessionState:
        """新しいthreadとturnを開始し、直ちにsession状態を返す。"""
        validate_prompt(prompt)
        validate_cwd(cwd)
        validate_model_effort(model, effort)
        async with contextlib.AsyncExitStack() as resources:
            client = await self._ensure_client(launch_kind, prompt)
            resources.push_async_callback(self._discard_client, client)
            thread_response, selection, attempted, owner_session_id, writer_session_id = await self._start_thread(
                client, cwd, model, launch_kind, role=codex_connection.role_of(model_type)
            )
            session_id = thread_response["thread"]["id"]
            await session_identity.validate_identity(self._validate_session_id, session_id, None)
            if owner_session_id is not None and writer_session_id is not None:
                await run_file_operation(shared_roots.write_host_alias, owner_session_id, writer_session_id, session_id)
            self._clients[session_id] = client
            self._unassigned_clients.pop(id(client), None)
            resources.pop_all()
        if writer_session_id is not None:
            self._writer_session_ids[session_id] = writer_session_id
        session = SessionState(
            session_id=session_id,
            cwd=cwd,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            model=model,
            effort=effort,
            engine="codex",
            turn_seq=1,
            publish_registry=self._publish_registry,
            codex_model_provider=(thread_response.get("modelProvider") or selection.primary) if selection else None,
            codex_subscription_provider=selection.primary if selection is not None and selection.subscription else None,
            codex_state_provider=thread_response.get("modelProvider") or (selection.primary if selection else None),
            codex_attempted_provider_ids=attempted,
        )
        self.sessions[session_id] = session
        await session.attach_executor_async(_client_pid(client))
        initialize_turn(session)
        session.status = "starting"
        session.touch()
        try:
            await self._start_turn(session, prompt, client)
        except Exception as exc:
            if self._turn_start_response_is_ambiguous(client, exc):
                await self._mark_turn_start_ambiguous(session, exc)
            else:
                await self._mark_failed(session, exc, retryable=False)
            await session.drain_registration()
            return session
        await session.drain_registration()
        return session

    async def _start_thread(
        self, client: JsonRpcProcess, cwd: str, model: str | None, launch_kind: LaunchKind, *, role: str = "delegate"
    ) -> tuple[dict[str, Any], codex_providers.ProviderSelection | None, set[str], str | None, str | None]:
        """新しい接続でthreadを開始し、応答・接続先の選択・試行した接続先・内側MCPの所有者と書込主体を返す。"""
        selection = None
        if codex_providers.has_provider_configuration():
            selection = await codex_providers.select(client.request, cwd, role=role)
            if not codex_providers.has_provider_configuration():
                selection = None
        params: dict[str, Any] = {
            "cwd": cwd,
            "baseInstructions": SUBAGENT_BASE_INSTRUCTIONS,
            # Codexが承認を要すると判定するコマンドは拒否され、PermissionRequest hookは呼ばれない。
            # 委譲先の標準手順は承認を要しない操作で構成し、ホストの権限制約を維持する。
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
            "serviceTier": await run_file_operation(_current_service_tier),
        }
        if model is not None:
            params["model"] = model
        config, owner_session_id, writer_session_id = self._thread_config(
            lightweight=launch_kind in LIGHTWEIGHT_LAUNCH_KINDS, client=client
        )
        if launch_kind == "explore":
            config = await run_file_operation(self._explore_config, config, client)
        params["config"] = config
        params["developerInstructions"] = await run_file_operation(_developer_instructions, launch_kind)
        attempted: set[str] = set()
        try:
            async with asyncio.timeout(shared_state.SESSION_INITIALIZATION_TIMEOUT):
                thread_response = None
                if selection is not None and (selection.primary or selection.candidates):
                    order = (selection.primary, *selection.candidates)
                    if (
                        selection.subscription
                        and selection.ordinary_usage_allowed is False
                        and not should_wait("codex", selection.usage_limit_type)
                    ):
                        order = selection.candidates
                    for candidate in order:
                        attempted.add(candidate)
                        try:
                            thread_response = await client.request("thread/start", {**params, "modelProvider": candidate})
                        except JsonRpcResponseError:
                            _LOG.info("Codex接続先の開始が拒否されました: provider=%s", candidate)
                            # サブスクリプションの主候補が一般的なRPC拒否を返した場合は、API課金の候補へ切り替えない。
                            if candidate == selection.primary and selection.subscription:
                                raise
                            continue
                        if thread_response.get("modelProvider") != candidate:
                            raise AppServerError("thread/start did not confirm the requested connection")
                        break
                    if thread_response is None:
                        raise AppServerError("Codex connection candidates were rejected")
                else:
                    thread_response = await client.request("thread/start", params)
        except TimeoutError as exc:
            diagnostic_method = getattr(client, "initialization_diagnostic", None)
            diagnostic = (
                diagnostic_method()
                if callable(diagnostic_method)
                else {"stage": "thread_start_response_wait", "pid": "unavailable"}
            )
            _LOG.error("Codex thread/start初期化timeout: diagnostic=%s", diagnostic)
            raise SessionInitializationTimeoutError(
                f"Codex thread/start did not return within {shared_state.SESSION_INITIALIZATION_TIMEOUT:.0f}s: "
                f"cwd={cwd}, launch_kind={launch_kind}; diagnostic={diagnostic}"
            ) from exc
        thread = thread_response.get("thread")
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or not thread["id"]:
            raise AppServerError("thread/start returned no thread.id")
        return thread_response, selection, attempted, owner_session_id, writer_session_id

    async def resume(
        self,
        session_id: str,
        prompt: ResumePrompt,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
        *,
        model_type: str | None = None,
        launch_kind: LaunchKind = "delegate",
        excluded_candidates: frozenset[ModelCandidate] = frozenset(),
        turn_seq: int = 0,
        fast_mode: bool | None = None,
        model_provider: str | None = None,
        subscription_provider: str | None = None,
        state_provider: str | None = None,
        handoff_prompt: str = "",
    ) -> SessionState:
        """保存済みthreadを再開して新しいturnを開始する。"""
        validate_cwd(cwd)
        validate_model_effort(model, effort)
        client = await self._acquire_owned_client(session_id, launch_kind, handoff_prompt)
        session = SessionState(
            session_id=session_id,
            cwd=cwd,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            model=model,
            effort=effort,
            engine="codex",
            turn_seq=turn_seq + 1,
            fast_mode=fast_mode,
            publish_registry=self._publish_registry,
            codex_model_provider=model_provider,
            codex_subscription_provider=subscription_provider,
            codex_state_provider=state_provider,
        )
        self.sessions[session_id] = session
        await session.attach_executor_async(_client_pid(client))
        initialize_turn(session)
        try:
            writer_session_id = await self._resume_thread(
                session,
                client,
                self._writer_session_ids.get(session.session_id),
            )
            if writer_session_id is not None:
                self._writer_session_ids[session.session_id] = writer_session_id
            await prompt.deliver(lambda value: self._start_turn(session, value, client))
        except asyncio.CancelledError:
            await self._cancel_resume(session)
        except codex_resume_state.IncompatibleResumeError as exc:
            prompt.record_delivery("reply_failed")
            await self._mark_incompatible_resume(session, exc)
        except Exception as exc:
            if self._turn_start_response_is_ambiguous(client, exc):
                prompt.record_delivery("reply_ambiguous")
                await self._mark_turn_start_ambiguous(session, exc)
            else:
                prompt.record_delivery("reply_failed")
                await self._mark_failed(session, exc, retryable=False)
        await session.drain_registration()
        return session

    async def _cancel_resume(self, session: SessionState) -> None:
        """再開取消時に配送済みturnを中断し、実作業の終了まで待つ。"""
        if not session.turn_start_sent:
            session.status = "interrupted"
            session.turn_completed = True
            session.turn_start_ambiguous = False
            session.interrupt_requested = False
            session.touch()
            await self._notify_waiters()
            return

        if not session.turn_id and not session.turn_completed:
            async with self._condition:
                await self._condition.wait_for(lambda: bool(session.turn_id) or session.turn_completed)
        if session.turn_completed:
            return

        session.interrupt_requested = True
        session.touch()
        await self._notify_waiters()
        await self.interrupt(session)
        async with self._condition:
            await self._condition.wait_for(lambda: session.turn_completed)

    async def send_message(self, session: SessionState, prompt: str) -> dict[str, Any]:
        """実行中turnへ追加指示を送り、終了競合時は同じthreadのreplyを開始する。"""
        validate_prompt(prompt)
        async with session.turn_control_lock:
            if (
                session.awaiting_auto_resume
                and not resume_waits.is_background_completion_wait(session)
                and session.pending_result is not None
            ):
                resume_waits.finalize_pending_result(session, touch=False)
            if session.awaiting_auto_resume and session.pending_result is not None:
                delivery, status, _ = await self._start_reply_locked(session, prompt)
                return {"delivery": delivery, "previous_result": {}, **status}
            if session.terminal:
                previous_result = self._capture_result(session)
                delivery, status, error = await self._start_reply_locked(session, prompt)
                if error is not None and delivery not in {"reply_failed", "reply_ambiguous"}:
                    raise error from None
                result = {
                    "delivery": delivery,
                    "previous_result": previous_result,
                    **status,
                }
                return result
            if session.interrupt_requested:
                raise ActionableError(
                    "the active Codex turn is being interrupted", next_action=result_projection.RESEND_AFTER_WAIT_NEXT_ACTION
                )
            if not session.turn_id:
                raise ActionableError(
                    "the active Codex turn has no turn_id", next_action=result_projection.RESEND_AFTER_WAIT_NEXT_ACTION
                )
            client = self._owned_client(session)
            if client is None:
                raise AppServerError("Codex App Server client is unavailable for steering")
            expected_turn_id = session.turn_id
            try:
                response = await client.request(
                    "turn/steer",
                    {
                        "threadId": session.session_id,
                        "expectedTurnId": expected_turn_id,
                        "input": [{"type": "text", "text": prompt}],
                    },
                )
            except JsonRpcResponseError as exc:
                outcome = await self._wait_after_steer_rejection(session, expected_turn_id, client)
                if outcome == "completed":
                    previous_result = self._capture_result(session)
                    delivery, status, error = await self._start_reply_locked(session, prompt)
                    if error is not None and delivery not in {"reply_failed", "reply_ambiguous"}:
                        raise error from None
                    return {
                        "delivery": delivery,
                        "previous_result": previous_result,
                        **status,
                    }
                raise exc from None
            response_turn_id = _response_turn_id(response)
            if response_turn_id != expected_turn_id:
                raise AppServerError(
                    f"turn/steer returned an unexpected turn.id: expected {expected_turn_id}, got {response_turn_id or 'none'}"
                )
            session.touch()
            return {
                "delivery": "steered",
                **session.public_status(),
            }

    async def release_session(self, session: SessionState) -> None:
        """sessionが所有するApp Serverと子孫（threadごとのMCPサーバーと子孫threadの資源）を終了させる。

        購読解除はApp Serverがthreadの資源を猶予の後に解放する非同期の要求であり、解放の完了を表さない。
        このため所有するApp Serverそのものを終了させ、App Serverと全ての子孫の終了を確認してから戻る。
        終了を確認できない対象が残る場合は`ResourceReleaseError`を送出し、接続の所有を保って再試行に備える。
        他のsessionの接続は変更しない。解放後の`send_message`は新しいApp Serverで`thread/resume`して会話を復元する。
        """
        await self._release_owned_client(session.session_id)
        session.codex_subagent_thread_ids.clear()

    async def interrupt(self, session: SessionState) -> None:
        """公開killから対象turnへ中断要求を送り、受理を待つ。

        結果を保留している間はモデルのturnが終わっているため、中断要求を送らずに保留した結果を確定する。
        """
        if session.awaiting_auto_resume and session.pending_result is not None:
            resume_waits.finalize_pending_result(session)
            await self._notify_waiters()
            return
        if session.terminal:
            return
        if not session.turn_id:
            raise ActionableError(
                "the active Codex turn has no turn_id",
                next_action="`atk agents wait`で状態を確認し、turnが続いていて中断が必要なら`kill`を再発行する",
            )
        client = self._owned_client(session)
        if client is None:
            raise AppServerError("Codex App Server client is unavailable for interrupt")
        try:
            await client.request(
                "turn/interrupt",
                {"threadId": session.session_id, "turnId": session.turn_id},
            )
        except JsonRpcResponseError:
            if session.terminal:
                return
            raise

    async def _start_reply_locked(
        self,
        session: SessionState,
        prompt: str,
    ) -> tuple[str, dict[str, Any], BaseException | None]:
        """lock取得済みのsessionへreplyを開始し、公開状態と失敗分類を返す。"""
        held = resume_waits.HeldTurn.capture(session)
        switch_needed = self._provider_failure(session)
        previous_error = session.pending_result["error"] if session.pending_result is not None else session.error
        if (
            switch_needed
            and isinstance(previous_error, dict)
            and previous_error.get("codexErrorInfo") == "usageLimitExceeded"
            and not isinstance(previous_error.get("usageLimit"), dict)
        ):
            # 子session待ちが上限の通知より先に結果を保留した場合も、同じ解除待ち方針を使う。
            limit = await run_file_operation(codex_usage_limit.from_thread, session.session_id)
            switch_needed = not should_wait("codex", limit.limit_type if limit else None)
        session.codex_provider_resume_pending = False
        previous_status = session.status
        switched = False
        try:
            if switch_needed:
                # cold resumeも追送の排他区間に含まれるため、kill/stopへ終了時の結果として渡さない。
                session.status = "starting"
                session.touch()
            client = await self._acquire_owned_client(session.session_id, session.launch_kind)
            if switch_needed:
                switched = await self._switch_provider(session, client)
                if not switched or session.interrupt_requested:
                    if held is not None and not session.interrupt_requested:
                        resume_waits.finalize_pending_result(session)
                    elif not session.interrupt_requested:
                        session.status = previous_status
                    session.touch()
                    return "reply_failed", session.public_status(), None
            writer_session_id = self._writer_session_ids.get(session.session_id)
            if not switched:
                writer_session_id = await self._resume_thread(session, client, writer_session_id)
            if writer_session_id is not None:
                self._writer_session_ids[session.session_id] = writer_session_id
            begin_reply(session, preserve_waits=held is not None)
        except asyncio.CancelledError:
            if held is not None:
                held.restore(session)
            elif switch_needed:
                session.status = previous_status
                session.touch()
            raise
        except codex_resume_state.IncompatibleResumeError as exc:
            if held is not None:
                held.restore(session)
            await self._mark_incompatible_resume(session, exc, new_reply=True)
            return "reply_failed", session.public_status(), None
        except Exception as exc:
            if held is not None:
                held.restore(session)
                await self._notify_waiters()
                return "reply_failed", session.public_status(), exc
            await self._mark_failed(session, exc, retryable=True)
            return "reply_failed", session.public_status(), exc
        try:
            await self._start_turn(session, prompt, client)
        except asyncio.CancelledError:
            if held is not None and not session.turn_start_sent:
                held.restore(session)
            elif held is not None:
                session.turn_start_ambiguous = True
                session.touch()
            raise
        except Exception as exc:
            if self._turn_start_response_is_ambiguous(client, exc):
                await self._mark_turn_start_ambiguous(session, exc)
                return "reply_ambiguous", session.public_status(), None
            if held is not None:
                held.restore(session)
                await self._notify_waiters()
                return "reply_failed", session.public_status(), exc
            await self._mark_failed(session, exc, retryable=False)
            return "reply_failed", session.public_status(), exc
        return "reply_started", session.public_status(), None

    @staticmethod
    def _provider_failure(session: SessionState, error: Any = None) -> bool:
        """利用上限で終了した失敗と、移行済みAPIが受け付けなかった失敗だけを、代替の接続先での試行の対象と判定する。

        統括担当は会話の中で接続を切り替えないため対象にしない。
        """
        if not session.codex_model_provider or not session.turn_completed or session.turn_start_ambiguous:
            return False
        if codex_connection.role_of(session.model_type) == "orchestrator":
            return False
        result = session.pending_result
        status = result["status"] if result is not None else session.status
        if error is None:
            error = result["error"] if result is not None else session.error
        limit = error.get("usageLimit") if isinstance(error, dict) else None
        if isinstance(limit, dict) and should_wait("codex", limit.get("type")):
            return False
        if session.codex_provider_resume_pending:
            return True
        return status == "failed" and codex_providers.fallback_failure(
            error, subscription=session.codex_model_provider == session.codex_subscription_provider
        )

    async def _switch_provider(self, session: SessionState, client: Any) -> bool:
        """新しいturnを開始する前に、同じthreadを独立API認証の次候補へcold resumeする。"""
        if session.turn_start_ambiguous or session.turn_start_sent and not session.turn_completed:
            return False
        selection = await codex_providers.select(
            client.request, session.cwd, primary=session.codex_model_provider, role=codex_connection.role_of(session.model_type)
        )
        if session.codex_model_provider:
            session.codex_attempted_provider_ids.add(session.codex_model_provider)
        if session.codex_subscription_provider:
            session.codex_attempted_provider_ids.add(session.codex_subscription_provider)
        original = session.codex_model_provider
        for candidate in selection.candidates:
            if candidate in session.codex_attempted_provider_ids:
                continue
            session.codex_attempted_provider_ids.add(candidate)
            if session.interrupt_requested:
                return False
            try:
                await self._close_for_provider_switch(session, client)
                if session.interrupt_requested:
                    return False
                session.codex_model_provider = candidate
                await self._resume_thread(session, client, self._writer_session_ids.get(session.session_id))
            except JsonRpcResponseError:
                session.codex_model_provider = original
                _LOG.info("Codex接続先の再開が拒否されました: session_id=%s provider=%s", session.session_id, candidate)
                continue
            except AppServerError, TimeoutError, OSError:
                session.codex_model_provider = original
                _LOG.info("Codex接続先の再開を確定できません: session_id=%s provider=%s", session.session_id, candidate)
                return False
            except BaseException:
                session.codex_model_provider = original
                raise
            _LOG.info("Codex接続先を確定しました: session_id=%s provider=%s", session.session_id, candidate)
            return True
        return False

    async def _close_for_provider_switch(self, session: SessionState, client: Any) -> None:
        """Loaded threadの接続先が再利用されないよう、unsubscribeの閉鎖完了を待つ。"""
        async with asyncio.timeout(DEFAULT_WAIT_TIMEOUT):
            await self._close_thread(session.session_id, client, session=session)

    async def _close_thread(self, thread_id: str, client: Any, *, session: SessionState | None = None) -> None:
        """解除応答と閉鎖通知を対応付け、対象threadの資源解放を確認する。"""
        closed = asyncio.Event()
        self._resume_close_events[thread_id] = closed
        try:
            if getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
                raise AppServerError("Codex App Server connection lost before thread closure")
            response = await client.request("thread/unsubscribe", {"threadId": thread_id})
            if response.get("status") == "notLoaded":
                return
            if response.get("status") not in {"unsubscribed", "notSubscribed"}:
                raise AppServerError("thread/unsubscribe returned an unexpected status")
            async with self._condition:
                await self._condition.wait_for(
                    lambda: (
                        closed.is_set()
                        or session is not None
                        and session.interrupt_requested
                        or getattr(client, "closed", False)
                        or getattr(client, "reader_failure", None) is not None
                    )
                )
            if not closed.is_set() and not (session is not None and session.interrupt_requested):
                raise AppServerError("thread/unsubscribe did not complete the connection closure")
        finally:
            self._resume_close_events.pop(thread_id, None)

    async def _resume_thread(
        self,
        session: SessionState,
        client: Any,
        writer_session_id: str | None = None,
    ) -> str | None:
        """保存済みCodex threadを現在の実行条件で再開する。"""
        await self._apply_connection_order(session, client)
        try:
            target = await self._compatible_resume_provider(session, client)
        except (AppServerError, TimeoutError, OSError) as error:
            raise codex_resume_state.IncompatibleResumeError(
                "Codexの再開条件を取得できず、再開先との状態互換性を確認できない"
            ) from error
        resume_params: dict[str, Any] = {
            "threadId": session.session_id,
            "cwd": session.cwd,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
            "serviceTier": await run_file_operation(_current_service_tier),
        }
        if session.model is not None:
            resume_params["model"] = session.model
        resume_params["modelProvider"] = target
        config = AppServerManager._base_thread_config(lightweight=session.launch_kind in LIGHTWEIGHT_LAUNCH_KINDS)
        owner_session_id = self._root_session_id
        if owner_session_id is not None:
            writer_session_id = writer_session_id or uuid.uuid4().hex
            config.update(self._agents_server_config(owner_session_id, writer_session_id, client))
        if session.launch_kind == "explore":
            config = await run_file_operation(self._explore_config, config, client)
        resume_params["config"] = config
        resume_params["developerInstructions"] = await run_file_operation(_developer_instructions, session.launch_kind)
        resume_response = await self._request_thread_resume(client, resume_params)
        resumed_thread = resume_response.get("thread")
        if not isinstance(resumed_thread, dict) or resumed_thread.get("id") != session.session_id:
            raise AppServerError("thread/resume returned an unexpected thread.id")
        if (session.codex_model_provider is not None or "modelProvider" in resume_response) and resume_response.get(
            "modelProvider"
        ) != target:
            raise AppServerError("thread/resume did not confirm the requested connection")
        session.codex_state_provider = target
        if owner_session_id is not None and writer_session_id is not None:
            await run_file_operation(shared_roots.write_host_alias, owner_session_id, writer_session_id, session.session_id)
        return writer_session_id

    @staticmethod
    async def _apply_connection_order(session: SessionState, client: Any) -> None:
        """保持したproviderの種別を使用順から外した会話を、使用順の先頭の利用可能な候補へ移す。

        移した後の種別は使用順に含まれるため、使用順から外れた種別へは戻らない。
        """
        role = codex_connection.role_of(session.model_type)
        if not codex_connection.outside_order(
            session.codex_model_provider, role, subscription_provider=session.codex_subscription_provider
        ):
            return
        selection = await codex_providers.select(client.request, session.cwd, role=role)
        _LOG.info("Codex接続先を使用順の先頭へ移しました: session_id=%s provider=%s", session.session_id, selection.primary)
        session.codex_model_provider = selection.primary
        session.codex_subscription_provider = selection.primary if selection.subscription else None

    async def _compatible_resume_provider(self, session: SessionState, client: Any) -> str:
        """通常設定も含めて今回の再開先を確定し、同threadの状態を再利用できることを確かめる。"""
        # 状態の生成先の観測根拠は`docs/development/audit-records.md`の
        # 「agent-toolkit/agent_toolkit/_agents_server/codex_resume_state.py：会話状態の生成先：2026年10月23日」にある。
        target = session.codex_model_provider
        if target is None:
            effective = await client.request("config/read", {"cwd": session.cwd, "includeLayers": False})
            configuration = effective.get("config")
            if not isinstance(configuration, dict):
                raise codex_resume_state.IncompatibleResumeError("Codexの再開先設定を取得できず、状態互換性を確認できない")
            target = configuration.get("model_provider") or "openai"
        if not isinstance(target, str):
            raise codex_resume_state.IncompatibleResumeError("Codexの再開先設定が不正で、状態互換性を確認できない")
        if session.codex_state_provider != target:
            record = await client.request("thread/read", {"threadId": session.session_id, "includeTurns": False})
            thread = record.get("thread")
            path = thread.get("path") if isinstance(thread, dict) else None
            await run_file_operation(
                codex_resume_state.validate_resume,
                session.session_id,
                target,
                session.codex_state_provider,
                path if isinstance(path, str) else None,
            )
        return target

    async def _mark_incompatible_resume(
        self,
        session: SessionState,
        error: codex_resume_state.IncompatibleResumeError,
        *,
        new_reply: bool = False,
    ) -> None:
        """元の会話・進捗を保持し、同threadの再送を終えて引き継げる終了を公開する。"""
        if session.pending_result is not None:
            resume_waits.finalize_pending_result(session, touch=False)
        if new_reply:
            progress = (session.agent_message, session.plan, session.commentary, session.diff_changed, session.last_action)
            # 拒否したreplyにも独立した終了を割り当て、回収済みの前turnと同一視させない。
            begin_reply(session, preserve_waits=True)
            session.agent_message, session.plan, session.commentary, session.diff_changed, session.last_action = progress
        session.codex_provider_resume_pending = False
        session.status = "failed"
        session.error = {"message": str(error), "codexErrorInfo": codex_resume_state.INCOMPATIBLE_STATE}
        session.turn_completed = True
        session.turn_start_ambiguous = False
        session.reply_retryable = False
        session.touch()
        await self._notify_waiters()

    async def _request_thread_resume(self, client: Any, params: dict[str, Any]) -> dict[str, Any]:
        """unsubscribe後の閉鎖と再開が競合した場合だけ、閉鎖通知を待って再送する。"""
        params = {**params, "baseInstructions": SUBAGENT_BASE_INSTRUCTIONS}
        thread_id = params["threadId"]
        closed = asyncio.Event()
        self._resume_close_events[thread_id] = closed
        try:
            async with asyncio.timeout(shared_state.SESSION_INITIALIZATION_TIMEOUT):
                try:
                    return await client.request("thread/resume", params)
                except JsonRpcResponseError as exc:
                    if str(exc) != (
                        f"thread/resume: thread {thread_id} is closing; retry thread/resume after the thread is closed"
                    ):
                        raise
                    async with self._condition:
                        await self._condition.wait_for(
                            lambda: (
                                closed.is_set()
                                or getattr(client, "closed", False)
                                or getattr(client, "reader_failure", None) is not None
                            )
                        )
                    if not closed.is_set():
                        raise
                return await client.request("thread/resume", params)
        finally:
            self._resume_close_events.pop(thread_id, None)

    @staticmethod
    def _capture_result(session: SessionState) -> dict[str, Any]:
        """継続入力へ直前turnの結果を退避する。"""
        return session.previous_result()

    async def _wait_after_steer_rejection(
        self,
        session: SessionState,
        expected_turn_id: str,
        client: Any,
    ) -> str:
        """steer拒否後に終了競合だけを待ち、優先順位付きの判定結果を返す。"""
        timed_out = False

        def _changed() -> bool:
            return bool(
                getattr(client, "closed", False)
                or getattr(client, "reader_failure", None) is not None
                or session.turn_id != expected_turn_id
                or session.result_available
            )

        try:
            async with self._condition:
                await asyncio.wait_for(self._condition.wait_for(_changed), timeout=DEFAULT_WAIT_TIMEOUT)
        except TimeoutError:
            timed_out = True
        if getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
            return "client_failure"
        if session.turn_id != expected_turn_id:
            return "turn_changed"
        if timed_out:
            return "timeout"
        if session.result_available:
            return "completed"
        return "timeout"

    @staticmethod
    def _initialize_turn(session: SessionState) -> None:
        initialize_turn(session)

    @staticmethod
    def _begin_reply(session: SessionState) -> None:
        begin_reply(session)

    async def _mark_failed(
        self,
        session: SessionState,
        error: BaseException,
        *,
        retryable: bool,
    ) -> None:
        """要求開始の失敗を終了状態へ反映し、待機者を起こす。

        `retryable`が真の場合だけ、同じreplyを再試行できる内部状態にする。
        """
        public_error = {"message": str(error) or error.__class__.__name__}
        failure = public_error.copy()
        if isinstance(error, JsonRpcResponseError) and isinstance(error.data, dict):
            # RPCの追加dataは内部の切替判定だけへ渡し、公開結果へ保存しない。
            failure.update(error.data)
        # 読取中に届いた中断要求は、続く保留判定でも維持する。
        session.interrupt_requested = False
        limit = None if isinstance(error, StartCandidateUnavailable) else await self._read_usage_limit_failure(session, failure)
        session.turn_id = ""
        session.status = "failed"
        session.plan = []
        session.record_current_item_start(None)
        session.commentary = ""
        session.diff_changed = False
        session.error = public_error
        if (
            session.codex_model_provider
            and session.codex_model_provider != session.codex_subscription_provider
            and not isinstance(error, StartCandidateUnavailable)
        ):
            session.error = {"message": "Codex connection rejected the request"}
        session.agent_message = ""
        session.protocol_warnings = []
        session.reply_turn_started = False
        session.reply_retryable = retryable
        session.turn_start_ambiguous = False
        session.turn_completed = True
        session.failure_pending_completion = False
        if not isinstance(error, StartCandidateUnavailable) and not self._hold_usage_limit_failure(session, limit):
            self._hold_provider_failure(session, failure)
        session.touch()
        await self._notify_waiters()

    async def _mark_turn_start_ambiguous(self, session: SessionState, error: BaseException) -> None:
        """turn/startの応答が失われたことを未終了状態へ反映する。"""
        if session.terminal:
            return
        session.status = "running"
        session.error = {"message": str(error) or error.__class__.__name__}
        session.reply_retryable = False
        session.turn_start_ambiguous = True
        session.interrupt_requested = False
        session.turn_completed = False
        session.failure_pending_completion = False
        session.touch()
        await self._notify_waiters()

    @staticmethod
    def _turn_start_response_is_ambiguous(client: Any, error: BaseException) -> bool:
        """turn/startの失敗が実行状態を判定できない応答喪失であるかを返す。"""
        if isinstance(error, (JsonRpcResponseError, StartCandidateUnavailable)):
            return False
        if bool(getattr(client, "closed", False)) or getattr(client, "reader_failure", None) is not None:
            return False
        process = getattr(client, "process", None)
        return process is None or getattr(process, "returncode", None) is None

    async def _start_turn(self, session: SessionState, prompt: str, client: JsonRpcProcess | None = None) -> None:
        if client is None:
            client = await self._acquire_owned_client(session.session_id, session.launch_kind)
        # 再起動したApp Serverでturnを始める場合も、実行するプロセスの識別を登録簿へ反映する。
        await session.attach_executor_async(_client_pid(client))
        params: dict[str, Any] = {
            "threadId": session.session_id,
            "input": [{"type": "text", "text": prompt}],
            "cwd": session.cwd,
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "dangerFullAccess"},
            "serviceTier": await run_file_operation(_current_service_tier),
        }
        if session.model is not None:
            params["model"] = session.model
        if session.effort is not None:
            params["effort"] = session.effort
        response = await client.request(
            "turn/start",
            params,
            on_sent=lambda: self._mark_turn_start_sent(session, fast_mode=params["serviceTier"] == "priority"),
        )
        turn = response.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise TurnStartResponseError("turn/start returned no turn.id")
        session.turn_id = turn_id
        if session.reply_attempted:
            session.reply_turn_started = True
        session.touch()
        await self._notify_waiters()

    @staticmethod
    def _mark_turn_start_sent(session: SessionState, *, fast_mode: bool) -> None:
        """turn/startの送信完了を応答待ちより先に記録する。"""
        session.turn_start_sent = True
        session.fast_mode = fast_mode
        session.touch()

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    @staticmethod
    async def _read_usage_limit_failure(session: SessionState, error: Any) -> codex_usage_limit.UsageLimitState | None:
        """終了を公開する前に、保留判定に必要な利用枠だけを読み取る。"""
        if not isinstance(error, dict) or error.get("codexErrorInfo") != "usageLimitExceeded":
            return None
        limit = await run_file_operation(codex_usage_limit.from_thread, session.session_id)
        return limit or codex_usage_limit.UsageLimitState(None)

    def _hold_usage_limit_failure(self, session: SessionState, limit: codex_usage_limit.UsageLimitState | None) -> bool:
        """読取済みの利用枠を使い、終了反映と同じloop区間で保留を確定する。"""
        if session.status != "failed" or limit is None:
            return False
        error = {
            **(session.error if isinstance(session.error, dict) else {}),
            "codexErrorInfo": "usageLimitExceeded",
            "usageLimit": {"type": limit.limit_type, "resetsAt": limit.resets_at_iso()},
        }
        session.error = error
        if session.interrupt_requested or not should_wait("codex", limit.limit_type):
            return False
        resume_waits.begin_auto_resume_wait(
            session, {"status": session.status, "agent_message": session.agent_message, "error": error}
        )
        session.auto_resume_deadline = None
        session.usage_limit_resume_at = asyncio.get_running_loop().time() + limit.delay_seconds(time.time())
        session.usage_limit_wait_count += 1
        if session.usage_limit_first_at is None:
            session.usage_limit_first_at = utc_now()
        session.api_error = {
            "type": "usage_limit",
            "limit_type": limit.limit_type,
            "resets_at": limit.resets_at_iso(),
            "first_at": session.usage_limit_first_at,
            "last_at": utc_now(),
            "count": session.usage_limit_wait_count,
        }
        session.status = "running"
        return True

    def _hold_provider_failure(self, session: SessionState, error: Any = None) -> bool:
        """候補切替の間は失敗を公開せず、既存の監視から継続を一度だけ配送する。"""
        if (
            session.interrupt_requested
            or not self._provider_failure(session, error)
            or not codex_providers.has_provider_configuration()
        ):
            return False
        resume_waits.begin_auto_resume_wait(
            session, {"status": session.status, "agent_message": session.agent_message, "error": session.error}
        )
        # App Serverは残高不足を区別した値を返さないため、クレジットの候補の不受理を残高不足として扱う。
        kind = codex_connection.provider_kind(
            session.codex_model_provider, subscription_provider=session.codex_subscription_provider
        )
        if kind == "credits":
            api_credits.record_shortage("codex")
        session.auto_resume_deadline = None
        session.codex_provider_resume_pending = True
        session.status = "running"
        return True

    async def _handle_notification(self, message: dict[str, Any]) -> None:
        """App Server通知をsession状態へ反映する。

        Codex CLI 0.153.4では``contextCompaction``の``item/started``と
        ``item/completed``がそれぞれミリ秒単位の必須時刻を持つ。
        監査記録は``docs/development/audit-records.md``の
        「agent-toolkit/agent_toolkit/_agents_server/codex.py：コンパクション計測通知：2026年9月10日」に置く。
        """
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        if method == "thread/tokenUsage/updated":
            thread_id = params.get("threadId")
            session = self.sessions.get(thread_id) if isinstance(thread_id, str) else None
            if session is None or session.engine != "codex" or not session.turn_id or params.get("turnId") != session.turn_id:
                return
            usage = params.get("tokenUsage")
            if not isinstance(usage, dict):
                return
            last = usage.get("last")
            if isinstance(last, dict):
                context_usage.record_context_usage(session, last.get("totalTokens"), usage.get("modelContextWindow"))
            return
        if method == "thread/closed":
            close_event = self._resume_close_events.get(_thread_id_from(params) or "")
            if close_event is not None:
                close_event.set()
                await self._notify_waiters()
            return
        if method == "thread/started":
            thread = params.get("thread")
            if isinstance(thread, dict):
                owner = self._find_thread_owner(thread.get("parentThreadId"))
                if owner is not None and isinstance(thread.get("id"), str):
                    owner.codex_subagent_thread_ids.add(thread["id"])
        session = self._find_session(params)
        if session is None:
            # 子孫threadの通知は子孫の追跡だけに使い、親のstatusや進捗へ混ぜない。
            owner = self._find_thread_owner(_thread_id_from(params))
            if owner is not None:
                child_thread_id = _thread_id_from(params)
                if method == "turn/completed" and isinstance(child_thread_id, str):
                    owner.codex_live_subagent_thread_ids.discard(child_thread_id)
                    owner.codex_terminal_subagent_thread_ids.add(child_thread_id)
                self._track_subagent_thread(
                    owner,
                    params.get("item"),
                    source_thread_id=child_thread_id,
                    source_turn_id=self._notification_turn_id(params),
                )
                turn = params.get("turn")
                if isinstance(turn, dict) and isinstance(turn.get("items"), list):
                    for item in turn["items"]:
                        self._track_subagent_thread(
                            owner,
                            item,
                            source_thread_id=child_thread_id,
                            source_turn_id=self._notification_turn_id(params),
                        )
            return
        notification_turn_id = self._notification_turn_id(params)
        if notification_turn_id is not None and session.turn_id and notification_turn_id != session.turn_id:
            return
        if method == "error":
            if params.get("willRetry") is True:
                error = params.get("error")
                info = error.get("codexErrorInfo") if isinstance(error, dict) else None
                error_type = next(iter(info), "api_error") if isinstance(info, dict) else str(info or "api_error")
                detail = info.get(error_type) if isinstance(info, dict) else None
                status = detail.get("httpStatusCode") if isinstance(detail, dict) else None
                session.record_api_error(
                    error_type, status if isinstance(status, int) and not isinstance(status, bool) else None
                )
                await self._notify_waiters()
            return
        item = params.get("item")
        if (
            method
            in {
                "turn/completed",
                "item/agentMessage/delta",
                "item/reasoning/textDelta",
                "item/reasoning/summaryTextDelta",
            }
            or (
                method == "item/started"
                and isinstance(item, dict)
                and item.get("type") not in _NON_MODEL_OUTPUT_ITEM_TYPES
                and item.get("type") != "subAgentActivity"
            )
            or (method == "item/completed" and isinstance(item, dict) and item.get("type") in {"agentMessage", "reasoning"})
        ):
            session.api_error = None
        turn = params.get("turn")
        if method == "turn/started":
            session.turn_start_sent = True
            if isinstance(turn, dict):
                turn_id = turn.get("id")
                if isinstance(turn_id, str):
                    session.turn_id = turn_id
                    if session.reply_attempted:
                        session.reply_turn_started = True
                    session.turn_start_ambiguous = False
            if not session.failure_pending_completion:
                session.status = "running"
        elif method == "turn/completed":
            # 結果を公開可能にする前に保存出力の読取を完了し、終了状態と確定時刻を同じloop区間で反映する。
            if isinstance(turn, dict):
                await self._consume_items(session, turn.get("items"))
            await wait_output_tracking.consume_agents_wait_background_outputs_async(session)
            failure_pending_completion = session.failure_pending_completion
            status = session.status
            error = session.error
            if isinstance(turn, dict):
                turn_id = turn.get("id")
                if isinstance(turn_id, str):
                    session.turn_id = turn_id
                if not failure_pending_completion:
                    status = _public_turn_status(turn.get("status"))
                turn_error = turn.get("error")
                if turn_error is not None or not failure_pending_completion:
                    error = turn_error
            else:
                status = "failed"
                error = "turn/completed did not contain turn"
            if status not in TERMINAL_STATUSES:
                status = "failed"
            # 状態を終了へ進めた後にawaitすると、waitや状態保存が保留判定前の結果を回収できてしまう。
            limit = await self._read_usage_limit_failure(session, error) if status == "failed" else None
            session.status = status
            session.error = error
            session.turn_start_ambiguous = False
            session.turn_completed = True
            session.failure_pending_completion = False
            if session.status == "completed":
                session.codex_attempted_provider_ids = {session.codex_model_provider} if session.codex_model_provider else set()
            result = {"status": session.status, "agent_message": session.agent_message, "error": session.error}
            held_by_result_contract = result_contract.hold_invalid_result(session, result)
            if not held_by_result_contract:
                session.status = result["status"]
                session.error = result["error"]
            if held_by_result_contract:
                pass
            elif (resume_waits.has_pending_auto_resume_targets(session) and not session.auto_resume_consumed) or (
                session.codex_self_message_observed and not session.codex_self_message_repair_consumed
            ):
                # 未観測の孫sessionが残るturnは、待機表明を完了報告として公開せずに結果を保留する。
                # 監査記録は`docs/development/audit-records.md`の
                # 「agent-toolkit/agent_toolkit/_agents_server/codex.py：孫sessionの待機表明と自動再開：2026年10月1日」にある。
                # MCP層の常駐監視（`_monitor_auto_resume`）が孫の終了を観測し、同じsessionを一度だけ再開する。
                # 期限到来・追跡先の喪失・再開失敗の確定と未観測識別子の記録も、同じ監視が既存の診断で行う。
                resume_waits.begin_auto_resume_wait(
                    session, {"status": session.status, "agent_message": session.agent_message, "error": session.error}
                )
                session.status = "running"
            elif session.live_child_session_ids:
                unobserved_session_ids = set(session.live_child_session_ids)
                session.live_child_session_ids.clear()
                resume_waits.record_unobserved_sessions(session, unobserved_session_ids)
            elif self._hold_usage_limit_failure(session, limit) or self._hold_provider_failure(session):
                pass
            elif not resume_waits.is_overload_failure(session):
                resume_waits.clear_overload_resume(session)
            elif (session.availability_checked or session.turn_seq > 1) and resume_waits.begin_overload_resume_wait(
                session, {"status": session.status, "agent_message": session.agent_message, "error": session.error}
            ):
                # 起動の可用性確認を過ぎた後の過負荷は時間の経過で解け得るため、結果を保留して待機後に同じsessionで続ける。
                # 継続の送信はMCP層の常駐監視（`_monitor_auto_resume`）が行う。確認を終える前の過負荷は起動時の候補切替が扱う。
                session.status = "running"
            session.compaction_started_at_ms.clear()
        elif method == "turn/plan/updated":
            plan = params.get("plan")
            if isinstance(plan, list):
                session.plan = [item for item in plan if isinstance(item, dict)]
        elif method == "turn/diff/updated":
            diff = params.get("diff")
            if isinstance(diff, str) and diff:
                session.diff_changed = True
        elif method == "item/started":
            item = params.get("item")
            self._track_subagent_thread(session, item)
            session.record_current_item_start(item if isinstance(item, dict) else None)
            if isinstance(item, dict):
                if item.get("type") not in _NON_MODEL_OUTPUT_ITEM_TYPES:
                    session.model_output_observed = True
                if item.get("type") == "fileChange":
                    session.diff_changed = True
                item_id = item.get("id")
                started_at_ms = params.get("startedAtMs")
                if (
                    item.get("type") == "contextCompaction"
                    and isinstance(item_id, str)
                    and item_id
                    and isinstance(started_at_ms, int)
                    and not isinstance(started_at_ms, bool)
                ):
                    session.compaction_started_at_ms[item_id] = started_at_ms
        elif method == "item/completed":
            item = params.get("item")
            if isinstance(item, dict):
                session.record_current_item_start(None)
                (await self._consume_item(session, item))
                item_id = item.get("id")
                completed_at_ms = params.get("completedAtMs")
                if (
                    item.get("type") == "contextCompaction"
                    and isinstance(item_id, str)
                    and item_id in session.compaction_started_at_ms
                    and isinstance(completed_at_ms, int)
                    and not isinstance(completed_at_ms, bool)
                ):
                    await run_file_operation(
                        compaction_metrics.append_compaction_record,
                        session.session_id,
                        item_id,
                        session.compaction_started_at_ms[item_id],
                        completed_at_ms,
                    )
                    del session.compaction_started_at_ms[item_id]
        elif method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str):
                item_id = params.get("itemId")
                if not isinstance(item_id, str) or not item_id:
                    current = session.current_item
                    item_id = current.get("id") if isinstance(current, dict) else None
                item_id = item_id if isinstance(item_id, str) and item_id else "__current__"
                session.progress_items[item_id] = append_bounded(session.progress_items.get(item_id, ""), delta)
                session.commentary = session.progress_items[item_id]
                session.set_progress(session.commentary)
        elif method in {"item/fileChange/outputDelta", "item/fileChange/patchUpdated"}:
            session.diff_changed = True
        session.touch()
        await self._notify_waiters()

    # Codex CLI 0.148.0のServerRequest schemaで確認した全server-initiated request:
    # item/commandExecution/requestApproval・item/fileChange/requestApproval・
    # item/tool/requestUserInput・mcpServer/elicitation/request・
    # item/permissions/requestApproval・item/tool/call・
    # account/chatgptAuthTokens/refresh・attestation/generate・
    # applyPatchApproval・execCommandApproval。
    # 承認用の公開MCP toolは設けず、readerで必ず応答して非対話要求をfailedへ記録する。
    async def _handle_server_request(self, message: dict[str, Any], client: Any) -> None:
        """要求を受信した接続（`client`）へ応答し、その接続が所有するsessionだけへ失敗を反映する。"""
        method = message.get("method")
        params = message.get("params")
        request_id = message.get("id")
        if not isinstance(method, str):
            return
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            # IDなしの異常なserver requestへはJSON-RPC応答を返せないため、
            # 接続上のactive turnをfailedへ遷移させ、turn完了通知を待つ。
            await self._fail_for_request(params, method, client)
            return
        if method == "mcpServer/elicitation/request":
            response: dict[str, Any] = {"action": "cancel", "content": None, "_meta": None}
            await client.send({"id": request_id, "result": response})
            session = self._find_session(params)
            if session is not None:
                session.protocol_warnings.append("mcpServer/elicitation/request was cancelled")
                session.touch()
                await self._notify_waiters()
            return
        await self._fail_for_request(params, method, client)
        await client.send(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unsupported non-interactive server request: {method}",
                },
            }
        )

    async def _fail_for_request(self, params: Any, method: str, client: Any) -> None:
        session = self._find_session(params)
        if session is not None:
            sessions = [session] if not session.terminal else []
        else:
            sessions = [item for item in self._client_sessions(client) if not item.terminal]
        interrupt_targets: list[tuple[str, str]] = []
        for active in sessions:
            has_active_turn = bool(active.turn_id)
            active.status = "failed"
            active.error = {"message": f"Codex requested interactive server input: {method}"}
            active.protocol_warnings.append(f"unsupported server request: {method}")
            active.turn_completed = not has_active_turn
            active.turn_start_ambiguous = False
            active.failure_pending_completion = has_active_turn
            if has_active_turn and not active.interrupt_requested:
                interrupt_targets.append((active.session_id, active.turn_id))
            active.interrupt_requested = has_active_turn
            active.touch()
        await self._notify_waiters()
        # reader task cannot await a request response for turn/interrupt: that would
        # deadlock the same reader. Schedule it after publishing failed and waking waiters.
        for session_id, turn_id in interrupt_targets:
            self._schedule(self._interrupt(session_id, turn_id, client))

    async def _interrupt(self, session_id: str, turn_id: str, client: Any) -> None:
        if getattr(client, "closed", False):
            return
        try:
            await client.request("turn/interrupt", {"threadId": session_id, "turnId": turn_id})
        except JsonRpcResponseError as exc:
            await self._handle_interrupt_response_error(session_id, turn_id, exc)
        except Exception as exc:
            await self._handle_client_failure(exc, client)

    async def _handle_interrupt_response_error(self, session_id: str, turn_id: str, error: JsonRpcResponseError) -> None:
        """turn/interruptのJSON-RPC errorを対象turnだけへ記録する。"""
        session = self._find_session({"threadId": session_id})
        if session is None or session.turn_completed or session.turn_id != turn_id:
            return
        session.error = {"message": str(error) or error.__class__.__name__}
        session.protocol_warnings.append(f"turn/interrupt failed: {error}")
        session.touch()
        await self._notify_waiters()

    async def _handle_client_failure(self, error: BaseException, client: Any) -> None:
        """reader異常時に、その接続が所有するsessionのactive turnをfailedへ遷移させて待機者を起こす。"""
        detail = str(error) or error.__class__.__name__
        changed = False
        for session in self._client_sessions(client):
            if not session.terminal or session.failure_pending_completion:
                if session.awaiting_auto_resume and session.pending_result is not None:
                    resume_waits.finalize_pending_result(session, touch=False)
                session.status = "failed"
                if not session.failure_pending_completion:
                    session.error = {
                        **(session.error if isinstance(session.error, dict) else {}),
                        "message": f"Codex App Server stopped: {detail}",
                    }
                session.api_error = None
                session.interrupt_requested = False
                session.turn_start_ambiguous = False
                session.turn_completed = True
                session.failure_pending_completion = False
                session.touch()
                changed = True
        if changed or self._resume_close_events:
            await self._notify_waiters()

    def _codex_sessions(self) -> list[SessionState]:
        """managerと共有する一覧から、このbackendが状態を更新できるsessionだけを返す。"""
        return [session for session in self.sessions.values() if session.engine == "codex"]

    def _client_sessions(self, client: Any) -> list[SessionState]:
        """共有一覧のCodex sessionのうち、`client`の接続を所有するsessionだけを返す。"""
        return [session for session in self._codex_sessions() if self._clients.get(session.session_id) is client]

    def _find_thread_owner(self, thread_id: Any) -> SessionState | None:
        """直接開始したthreadか、その子孫を所有するCodex sessionを返す。"""
        if not isinstance(thread_id, str):
            return None
        return next(
            (
                session
                for session in self._codex_sessions()
                if thread_id == session.session_id or thread_id in session.codex_subagent_thread_ids
            ),
            None,
        )

    @staticmethod
    def _track_subagent_thread(
        session: SessionState,
        item: Any,
        *,
        source_thread_id: str | None = None,
        source_turn_id: str | None = None,
    ) -> None:
        """開始・再操作で観測したsubagentのthreadを記録し、そのthreadの通知を所有sessionへ対応付ける。"""
        if not isinstance(item, dict) or item.get("type") != "subAgentActivity":
            return
        thread_id = item.get("agentThreadId")
        if not isinstance(thread_id, str) or not thread_id:
            return
        activity_id = item.get("id")
        kind = item.get("kind")
        if isinstance(activity_id, str):
            activity_key = (
                source_thread_id or session.session_id,
                source_turn_id or session.turn_id,
                activity_id,
                str(kind),
            )
            if activity_key in session.codex_subagent_activity_ids:
                return
            session.codex_subagent_activity_ids.add(activity_key)
        if thread_id == session.session_id:
            if kind == "interacted" and source_thread_id in {None, session.session_id}:
                session.codex_self_message_observed = True
            return
        session.codex_subagent_thread_ids.add(thread_id)
        if kind == "started":
            session.codex_live_subagent_thread_ids.add(thread_id)
            session.codex_terminal_subagent_thread_ids.discard(thread_id)
            session.auto_resume_consumed = False
        elif kind in {"completed", "interrupted"}:
            session.codex_live_subagent_thread_ids.discard(thread_id)
            session.codex_terminal_subagent_thread_ids.add(thread_id)

    def _find_session(self, params: Any) -> SessionState | None:
        thread_id = _thread_id_from(params)
        sessions = self._codex_sessions()
        if thread_id is not None:
            session = next((item for item in sessions if item.session_id == thread_id), None)
            if session is not None:
                return session
        turn_id = _turn_id_from(params)
        if turn_id is not None:
            return next((item for item in sessions if item.turn_id == turn_id), None)
        return None

    @staticmethod
    def _notification_turn_id(params: dict[str, Any]) -> str | None:
        """通知本文に含まれるturn IDを取得する。"""
        turn_id = _turn_id_from(params)
        if turn_id is not None:
            return turn_id
        turn = params.get("turn")
        if isinstance(turn, dict):
            turn_id = turn.get("id")
            if isinstance(turn_id, str) and turn_id:
                return turn_id
        return None

    @staticmethod
    async def _consume_items(session: SessionState, items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, dict):
                (await AppServerManager._consume_item(session, item))

    @staticmethod
    async def _consume_item(session: SessionState, item: dict[str, Any]) -> None:
        AppServerManager._track_subagent_thread(session, item)
        item_type = item.get("type")
        if item_type == "agentMessage":
            text = item.get("text")
            if isinstance(text, str):
                session.agent_message = text
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    item_id = "__current__"
                session.progress_items[item_id] = text
                session.set_progress(text)
        elif item_type == "plan":
            text = item.get("text")
            if isinstance(text, str):
                session.plan = [{"text": text, "status": "completed"}]
        elif item_type == "fileChange":
            session.diff_changed = True
        elif item_type == "commandExecution":
            command = item.get("command")
            output = item.get("aggregatedOutput")
            if item.get("exitCode") == 0 and isinstance(command, str) and isinstance(output, str):
                try:
                    arguments = shlex.split(command)
                except ValueError:
                    return
                if len(arguments) >= 3 and Path(arguments[0]).name == "timeout":
                    arguments = arguments[2:]
                if (
                    len(arguments) == 3
                    and Path(arguments[0]).name in {"bash", "sh", "dash", "zsh"}
                    and arguments[1] in {"-c", "-lc"}
                ):
                    try:
                        arguments = shlex.split(arguments[2])
                    except ValueError:
                        return
                    if len(arguments) >= 3 and Path(arguments[0]).name == "timeout":
                        arguments = arguments[2:]
                if is_agents_wait_command(arguments):
                    await wait_output_tracking.consume_agents_wait_output_async(session, output)
        elif item_type == "mcpToolCall" and item.get("server") == "agents_server":
            arguments = item.get("arguments")
            result = item.get("result")
            structured = result.get("structuredContent") if isinstance(result, dict) else None
            if isinstance(arguments, dict) and isinstance(structured, dict):
                consume_agents_server_tool_result(session, str(item.get("tool", "")), arguments, structured)

    async def close(self) -> None:
        """自身が起動した全てのApp Server接続と子孫を終了させる。

        全接続の終了を試みた後、終了を確認できない対象が残れば`ResourceReleaseError`を送出する。
        """
        task = self._close_task
        if task is None or task.done() and (task.cancelled() or task.exception() is not None):
            task = self._close_task = asyncio.create_task(self._close())
        await finish_task(task)

    async def _close(self) -> None:
        current_task = asyncio.current_task()
        tasks = tuple(task for task in self._background_tasks if task is not current_task)
        # 監視taskや先行clientの待機に依存させず、所有する全接続の停止を開始する。
        releases = [
            asyncio.create_task(self._discard_client(client)) for client in tuple(self._unassigned_clients.values())
        ] + [asyncio.create_task(self._release_owned_client(session_id)) for session_id in tuple(self._clients)]
        for task in tasks:
            if not task.done():
                task.cancel()
        outcomes = await lifetime.wait_for_shutdown((*tasks, *releases))
        failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
        for session in self._codex_sessions():
            try:
                await session.drain_registration()
            except Exception as error:
                failures.append(error)
        if failures:
            for other in failures[1:]:
                failures[0].add_note(str(other))
            raise failures[0]


def _public_turn_status(status: Any) -> str:
    if status == "inProgress":
        return "running"
    if status in TERMINAL_STATUSES:
        return status
    return "failed"
