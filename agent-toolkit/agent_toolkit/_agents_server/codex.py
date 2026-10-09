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
import typing
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any

from agent_toolkit._agents_server import (
    codex_providers,
    compaction_metrics,  # pylint: disable=wrong-import-position
    engine_availability,
    result_projection,
    resume_waits,
    session_errors,
    shared_roots,  # pylint: disable=wrong-import-position
    wait_output_tracking,
)
from agent_toolkit._agents_server import (
    plugin_root as plugin_roots,
)
from agent_toolkit._agents_server import state as shared_state  # pylint: disable=wrong-import-position
from agent_toolkit._agents_server.input_validation import validate_cwd, validate_model_effort, validate_prompt
from agent_toolkit._agents_server.launch_prompts import (
    AUTO_RESUME_NOTICE,
    LAUNCH_SYSTEM_PROMPTS,
    LIGHTWEIGHT_LAUNCH_KINDS,
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
)
from agent_toolkit._agents_server.wait_output_tracking import consume_agents_server_tool_result
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import (
    codex_models,
    process_tree,  # pylint: disable=wrong-import-position
)
from agent_toolkit._common import delegated_session as _delegated_session
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.codex")

APP_SERVER_COMMAND = ("codex", "app-server", "--stdio")
# Codexホストでは親の作業ディレクトリが版数付きplugin cacheとなり、
# plugin更新で消失すると生存中のApp Serverが設定を読み込めないため継承しない。
APP_SERVER_WORKING_DIRECTORY = str(Path.home())
DEFAULT_WAIT_TIMEOUT = 300.0
# App Serverのstdio用StreamReader buffer上限兼、stdoutを1回に読み取るchunk長（バイト）。
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
    ファイルが無いか読めない場合は空集合を返し、`~/.claude/rules/`配下の全ファイルを渡す側へ倒す。
    """
    agents_md = _host_homes.codex_home() / "AGENTS.md"
    try:
        text = agents_md.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
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
    """委譲先の役割、このprocessで安定化した配布物root、ユーザーが置いた規範ファイルの本文を一体で返す。"""
    plugin_root = plugin_roots.SERVER_PLUGIN_ROOT
    runtime = f"\n{python_runtime_instructions()}" if launch_kind in LIGHTWEIGHT_LAUNCH_KINDS else ""
    return (
        f"{LAUNCH_SYSTEM_PROMPTS[launch_kind]}\n{AUTO_RESUME_NOTICE}{runtime}\n\n"
        f"agent-toolkit plugin root: {plugin_root}\n"
        "agent-toolkitのskillとplugin内部資源は、この実在する絶対パスを起点に読む。"
        "`<役割名>.subagent.md`や別hostのcache版数から別のplugin rootを組み立てない。"
        f"{_user_rules_instructions()}"
    )


class AppServerError(session_errors.DelegateBackendError):
    """App Serverとの通信または要求検証に失敗した。"""


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


class JsonRpcProcess:
    """stdio App Serverとの要求・応答を多重化するクライアント。"""

    def __init__(
        self,
        on_notification: Callable[[dict[str, Any]], Awaitable[None]],
        on_server_request: Callable[[dict[str, Any]], Awaitable[None]],
        on_failure: Callable[[BaseException], Awaitable[None]] | None = None,
        *,
        root_session_id: str | None = None,
    ) -> None:
        self._on_notification = on_notification
        self._on_server_request = on_server_request
        self._on_failure = on_failure
        self._root_session_id = root_session_id
        self.process: asyncio.subprocess.Process | None = None
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

    def initialization_diagnostic(self) -> dict[str, Any]:
        """初期化の到達段階と子プロセスの有界な診断値を返す。"""
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
            environment = os.environ.copy()
            environment.pop(_delegated_session.DELEGATED_SESSION_ENV, None)
            owner_session_id = self._root_session_id
            if owner_session_id is not None:
                environment[_delegated_session.OWNER_SESSION_ENV] = owner_session_id
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
        except Exception as exc:
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
        """子プロセス終了時の診断情報を有界に収集する。"""
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

    async def close(self) -> None:
        """自身が起動した子プロセスと、その子孫プロセスを終了し、関連taskを回収する。

        対象は自身が起動したプロセスからの子孫関係だけで特定する。
        子孫の回収に失敗しても終端自体は完了させ、回収できなかった対象は診断へ残す。
        """
        if self._closed and self.process is None:
            return
        self._closed = True
        process = self.process
        # 子孫の列挙は終了要求より前に行う。App Serverが終了すると子孫の親が変わり、
        # 保持しているPIDを起点に辿れなくなるためである。
        descendants = process_tree.collect_descendants(None if process is None else process.pid)
        tasks = tuple(
            task for task in (self._reader_task, self._stderr_task) if task is not None and task is not asyncio.current_task()
        )
        self._reader_task = None
        self._stderr_task = None
        for task in tasks:
            if not task.done():
                task.cancel()
        if process is not None:
            with contextlib.suppress(OSError, ProcessLookupError):
                if process.returncode is None:
                    process.terminate()
            if process.returncode is None:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=5)
            if process.returncode is None:
                with contextlib.suppress(OSError, ProcessLookupError):
                    process.kill()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=5)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if process is not None:
            _LOG.info("Codex App Serverを終了しました: returncode=%s", process.returncode)
        residual = await asyncio.to_thread(process_tree.reclaim_descendants, descendants)
        process_tree.log_residual(residual, context="codex app-server")
        self.process = None
        error = AppServerError("Codex App Server client closed")
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()


class AppServerManager:
    """Codex App Serverと共有session状態を管理する。"""

    START_FAILURE_EXCLUDES_CANDIDATE: typing.ClassVar[bool] = False
    """起動の例外は候補を変えても結果が変わらないため、次の候補へ進まずに委譲元へ返す。"""
    INTERRUPT_REQUIRES_TURN_ID: typing.ClassVar[bool] = True
    """`turn/interrupt`はturnの識別子を要するため、中断の要求はturnの開始を待ってから送る。"""
    ORPHAN_TAKEOVER: typing.ClassVar[bool] = True
    """所有者のいない`running`の登録簿の記録を、`thread/read`の結果から終端として引き継げる。"""

    @staticmethod
    def unavailable_reason(session: SessionState) -> str | None:
        """終端したsessionがengineの可用性を理由に失敗した場合、その除外理由を返す。

        接続先がモデルIDを受け付けなかった失敗も、別の候補なら結果が変わるため除外理由にする。
        """

        def model_rejected(error: Mapping[str, Any]) -> str | None:
            if engine_availability.rejected_parameter(error) == "model":
                return engine_availability.ENGINE_MODEL_REJECTED_REASON
            return None

        return engine_availability.unavailable_reason(session, other_reason=model_rejected)

    @staticmethod
    def excludes_with_recorded_reason(reason: str) -> bool:
        """利用上限の旧記録だけは、設定したAPIと現在の利用可否で判定し直す。"""
        return reason != "usageLimitExceeded" or not codex_providers.has_provider_configuration()

    def __init__(
        self,
        sessions: dict[str, SessionState] | None = None,
        condition: asyncio.Condition | None = None,
        publish_registry: bool = False,
        *,
        root_session_id: str | None = None,
    ) -> None:
        self.client: JsonRpcProcess | None = None
        self.sessions = sessions if sessions is not None else {}
        self._condition = condition if condition is not None else asyncio.Condition()
        self._publish_registry = publish_registry
        self._root_session_id = root_session_id
        self._lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._writer_session_ids: dict[str, str] = {}
        self._resume_close_events: dict[str, asyncio.Event] = {}

    @staticmethod
    def _agents_server_config(owner_session_id: str, writer_session_id: str) -> dict[str, Any]:
        """内側のMCPサーバーへ状態ファイルの書込主体を配送する設定を返す。"""
        plugin_root = plugin_roots.SERVER_PLUGIN_ROOT
        return {
            "mcp_servers": {
                "agents_server": {
                    "command": "uv",
                    "args": [
                        "run",
                        "--project",
                        str(plugin_root),
                        "--locked",
                        "--no-default-groups",
                        str(plugin_root / "agent_toolkit" / "agents_server_mcp.py"),
                    ],
                    "env": {
                        _delegated_session.OWNER_SESSION_ENV: owner_session_id,
                        "AGENT_TOOLKIT_STATUS_HOST_SESSION": writer_session_id,
                    },
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
        この制約は`launch_kind`に依存しないため、軽量起動と通常の委譲で分けない。
        `bypass_hook_trust`は`codex app-server`のコマンドラインオプションとしても`-c`による設定上書きとしても受理されず、
        `thread/start`系リクエストの`config`だけが受理する。
        """
        config: dict[str, Any] = {"bypass_hook_trust": True}
        if lightweight:
            config["project_doc_max_bytes"] = 0
        return config

    def _thread_config(
        self, session_id: str | None = None, *, lightweight: bool
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
        config.update(self._agents_server_config(owner_session_id, writer_session_id))
        return config, owner_session_id, writer_session_id

    def _schedule(self, awaitable: Coroutine[Any, Any, None]) -> None:
        """同じイベントループで回収する管理対象taskを登録する。"""
        task: asyncio.Task[None] = asyncio.create_task(awaitable)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    @staticmethod
    async def _restrict_explore_config(client: Any, config: dict[str, Any], cwd: str) -> None:
        """sandbox外で動く外部toolを実効設定から除く。取得不能時は起動しない。"""
        response = await client.request("config/read", {"cwd": cwd, "includeLayers": False})
        effective = response.get("config")
        if not isinstance(effective, dict):
            raise AppServerError("exploreの外部tool制限に必要な実効設定を取得できません")
        servers = effective.get("mcp_servers", {})
        if not isinstance(servers, dict):
            raise AppServerError("exploreの実効mcp_serversが不正です")
        config["mcp_servers"] = {name: {"enabled": False} for name in servers}
        config["features"] = {"apps": False, "plugins": False, "multi_agent": False}

    async def _ensure_client(self) -> JsonRpcProcess:
        async with self._lock:
            if self.client is not None and not self.client.closed and self.client.reader_failure is None:
                return self.client
            old_client = self.client
            self.client = None
            if old_client is not None:
                await old_client.close()
            client = JsonRpcProcess(
                self._handle_notification,
                self._handle_server_request,
                self._handle_client_failure,
                root_session_id=self._root_session_id,
            )
            self.client = client
            try:
                await client.start()
            except Exception:
                if self.client is client:
                    self.client = None
                raise
            return client

    async def read_thread_turns(self, session_id: str) -> list[tuple[str, str]]:
        """保存済みthreadの全turnを、識別子と状態（`inProgress`・`completed`・`failed`・`interrupted`）の組で時系列順に返す。

        再起動前のプロセスが終端を公開できなかった記録について、turnが終わったかを委譲先CLIの記録で確かめるために使う。
        応答がthreadとturnの一覧を持たない場合は`AppServerError`を送出する。
        """
        client = await self._ensure_client()
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
        """既存のApp Server接続から表示対象の全モデルページを取得する。"""
        client = await self._ensure_client()
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
        client = await self._ensure_client()
        selection = None
        if codex_providers.has_provider_configuration():
            selection = await codex_providers.select(client.request, cwd)
            if not codex_providers.has_provider_configuration():
                selection = None
        params: dict[str, Any] = {
            "cwd": cwd,
            "approvalPolicy": "never",
            "sandbox": "read-only" if launch_kind == "explore" else "danger-full-access",
            "serviceTier": _current_service_tier(),
        }
        if model is not None:
            params["model"] = model
        config, owner_session_id, writer_session_id = self._thread_config(lightweight=launch_kind in LIGHTWEIGHT_LAUNCH_KINDS)
        if launch_kind == "explore":
            await self._restrict_explore_config(client, config, cwd)
            owner_session_id = writer_session_id = None
        params["config"] = config
        params["developerInstructions"] = _developer_instructions(launch_kind)
        attempted: set[str] = set()
        try:
            async with asyncio.timeout(shared_state.SESSION_INITIALIZATION_TIMEOUT):
                thread_response = None
                if selection is not None and (selection.primary or selection.candidates):
                    order = (selection.primary, *selection.candidates)
                    if selection.subscription and selection.ordinary_usage_allowed is False:
                        order = selection.candidates
                    for candidate in order:
                        attempted.add(candidate)
                        try:
                            thread_response = await client.request("thread/start", {**params, "modelProvider": candidate})
                        except JsonRpcResponseError:
                            _LOG.info("Codex接続先の開始が拒否されました: provider=%s", candidate)
                            # 通常サブスクの一般的なRPC拒否をAPI課金へ変換しない。
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
        session_id = thread["id"]
        if owner_session_id is not None and writer_session_id is not None:
            shared_roots.write_host_alias(owner_session_id, writer_session_id, session_id)
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
            codex_attempted_provider_ids=attempted,
        )
        self.sessions[session_id] = session
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
            return session
        return session

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
    ) -> SessionState:
        """保存済みthreadを再開して新しいturnを開始する。"""
        validate_cwd(cwd)
        validate_model_effort(model, effort)
        client = await self._ensure_client()
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
        )
        self.sessions[session_id] = session
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
        except Exception as exc:
            if self._turn_start_response_is_ambiguous(client, exc):
                await self._mark_turn_start_ambiguous(session, exc)
            else:
                await self._mark_failed(session, exc, retryable=False)
        return session

    async def _cancel_resume(self, session: SessionState) -> None:
        """再開取消時に配送済みturnを中断し、実作業の終端まで待つ。"""
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
        """実行中turnへ追加指示を送り、終端競合時は同じthreadのreplyを開始する。"""
        validate_prompt(prompt)
        async with session.turn_control_lock:
            if session.awaiting_auto_resume and session.auto_resume_deadline is None and session.pending_result is not None:
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
            client = self.client
            if client is None or getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
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
        """親と子孫threadの購読を外し、ロード状態とthreadごとのMCPサーバーを解放する。

        backendの共有App Serverは止めない。解放後のsend_messageは既存のthread/resumeで会話を復元する。
        notLoaded・notSubscribedの応答と接続閉鎖は既に解放済みのため、そのまま成功とする。
        """
        client = self.client
        if client is None or getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
            session.codex_subagent_thread_ids.clear()
            return
        for thread_id in (*sorted(session.codex_subagent_thread_ids), session.session_id):
            try:
                await client.request("thread/unsubscribe", {"threadId": thread_id})
            except AppServerError:
                if not getattr(client, "closed", False) and getattr(client, "reader_failure", None) is None:
                    raise
                session.codex_subagent_thread_ids.clear()
                return
            session.codex_subagent_thread_ids.discard(thread_id)

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
        client = self.client
        if client is None or getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
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
        session.codex_provider_resume_pending = False
        previous_status = session.status
        switched = False
        try:
            if switch_needed:
                # cold resumeも追送の排他区間。kill/stopへ終端結果として渡さない。
                session.status = "starting"
                session.touch()
            client = await self._ensure_client()
            if switch_needed:
                switched = await self._switch_provider(session, client)
                if not switched or session.interrupt_requested:
                    if held is not None and not session.interrupt_requested:
                        resume_waits.finalize_pending_result(session)
                    elif not session.interrupt_requested:
                        session.status = previous_status
                    session.touch()
                    return "reply_failed", session.public_status(), None
            begin_reply(session, preserve_waits=held is not None)
            writer_session_id = self._writer_session_ids.get(session.session_id)
            if not switched:
                writer_session_id = await self._resume_thread(session, client, writer_session_id)
            if writer_session_id is not None:
                self._writer_session_ids[session.session_id] = writer_session_id
        except asyncio.CancelledError:
            if held is not None:
                held.restore(session)
            elif switch_needed:
                session.status = previous_status
                session.touch()
            raise
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
        """終端した利用上限と、移行済みAPIの不受理だけを代替試行へ接続する。"""
        if not session.codex_model_provider or not session.turn_completed or session.turn_start_ambiguous:
            return False
        if session.codex_provider_resume_pending:
            return True
        result = session.pending_result
        status = result["status"] if result is not None else session.status
        if error is None:
            error = result["error"] if result is not None else session.error
        return status == "failed" and codex_providers.fallback_failure(
            error, subscription=session.codex_model_provider == session.codex_subscription_provider
        )

    async def _switch_provider(self, session: SessionState, client: Any) -> bool:
        """新しいturnを開始する前に、同じthreadを独立API認証の次候補へcold resumeする。"""
        if session.turn_start_ambiguous or session.turn_start_sent and not session.turn_completed:
            return False
        selection = await codex_providers.select(client.request, session.cwd, primary=session.codex_model_provider)
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
            except (AppServerError, TimeoutError, OSError):
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
        closed = asyncio.Event()
        self._resume_close_events[session.session_id] = closed
        try:
            async with asyncio.timeout(DEFAULT_WAIT_TIMEOUT):
                response = await client.request("thread/unsubscribe", {"threadId": session.session_id})
                if response.get("status") != "unsubscribed":
                    return
                async with self._condition:
                    await self._condition.wait_for(
                        lambda: (
                            closed.is_set()
                            or session.interrupt_requested
                            or getattr(client, "closed", False)
                            or getattr(client, "reader_failure", None) is not None
                        )
                    )
                if not closed.is_set() and not session.interrupt_requested:
                    raise AppServerError("thread/unsubscribe did not complete the connection closure")
        finally:
            self._resume_close_events.pop(session.session_id, None)

    async def _resume_thread(
        self,
        session: SessionState,
        client: Any,
        writer_session_id: str | None = None,
    ) -> str | None:
        """保存済みCodex threadを現在の実行条件で再開する。"""
        resume_params: dict[str, Any] = {
            "threadId": session.session_id,
            "cwd": session.cwd,
            "approvalPolicy": "never",
            "sandbox": "read-only" if session.launch_kind == "explore" else "danger-full-access",
            "serviceTier": _current_service_tier(),
        }
        if session.model is not None:
            resume_params["model"] = session.model
        if session.codex_model_provider is not None:
            resume_params["modelProvider"] = session.codex_model_provider
        config = AppServerManager._base_thread_config(lightweight=session.launch_kind in LIGHTWEIGHT_LAUNCH_KINDS)
        owner_session_id = self._root_session_id
        if owner_session_id is not None:
            writer_session_id = writer_session_id or uuid.uuid4().hex
            config.update(AppServerManager._agents_server_config(owner_session_id, writer_session_id))
        if session.launch_kind == "explore":
            await self._restrict_explore_config(client, config, session.cwd)
            owner_session_id = writer_session_id = None
        resume_params["config"] = config
        resume_params["developerInstructions"] = _developer_instructions(session.launch_kind)
        resume_response = await self._request_thread_resume(client, resume_params)
        resumed_thread = resume_response.get("thread")
        if not isinstance(resumed_thread, dict) or resumed_thread.get("id") != session.session_id:
            raise AppServerError("thread/resume returned an unexpected thread.id")
        if session.codex_model_provider is not None and resume_response.get("modelProvider") != session.codex_model_provider:
            raise AppServerError("thread/resume did not confirm the requested connection")
        if owner_session_id is not None and writer_session_id is not None:
            shared_roots.write_host_alias(owner_session_id, writer_session_id, session.session_id)
        return writer_session_id

    async def _request_thread_resume(self, client: Any, params: dict[str, Any]) -> dict[str, Any]:
        """unsubscribe後の閉鎖と再開が競合した場合だけ、閉鎖通知を待って再送する。"""
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
        """steer拒否後に終端競合だけを待ち、優先順位付きの判定結果を返す。"""
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
        """要求開始の失敗を終端状態へ反映し、待機者を起床する。

        `retryable`が真の場合だけ、同じreplyを再試行できる内部状態にする。
        """
        session.turn_id = ""
        session.status = "failed"
        session.plan = []
        session.record_current_item_start(None)
        session.commentary = ""
        session.diff_changed = False
        session.error = {"message": str(error) or error.__class__.__name__}
        failure = session.error.copy()
        if isinstance(error, JsonRpcResponseError) and isinstance(error.data, dict):
            # RPCの追加dataは内部の切替判定だけへ渡し、公開結果へ保存しない。
            failure.update(error.data)
        if session.codex_model_provider and session.codex_model_provider != session.codex_subscription_provider:
            session.error = {"message": "Codex connection rejected the request"}
        session.agent_message = ""
        session.protocol_warnings = []
        session.reply_turn_started = False
        session.reply_retryable = retryable
        session.turn_start_ambiguous = False
        session.interrupt_requested = False
        session.turn_completed = True
        session.failure_pending_completion = False
        self._hold_provider_failure(session, failure)
        session.touch()
        await self._notify_waiters()

    async def _mark_turn_start_ambiguous(self, session: SessionState, error: BaseException) -> None:
        """turn/start応答喪失を非終端状態へ反映する。"""
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
        if isinstance(error, JsonRpcResponseError):
            return False
        if bool(getattr(client, "closed", False)) or getattr(client, "reader_failure", None) is not None:
            return False
        process = getattr(client, "process", None)
        return process is None or getattr(process, "returncode", None) is None

    async def _start_turn(self, session: SessionState, prompt: str, client: JsonRpcProcess | None = None) -> None:
        if client is None:
            client = await self._ensure_client()
        params: dict[str, Any] = {
            "threadId": session.session_id,
            "input": [{"type": "text", "text": prompt}],
            "cwd": session.cwd,
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly"} if session.launch_kind == "explore" else {"type": "dangerFullAccess"},
            "serviceTier": _current_service_tier(),
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
                self._track_subagent_thread(owner, params.get("item"))
                turn = params.get("turn")
                if isinstance(turn, dict) and isinstance(turn.get("items"), list):
                    for item in turn["items"]:
                        self._track_subagent_thread(owner, item)
            return
        notification_turn_id = self._notification_turn_id(params)
        if notification_turn_id is not None and session.turn_id and notification_turn_id != session.turn_id:
            return
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
            failure_pending_completion = session.failure_pending_completion
            if isinstance(turn, dict):
                turn_id = turn.get("id")
                if isinstance(turn_id, str):
                    session.turn_id = turn_id
                if not failure_pending_completion:
                    session.status = _public_turn_status(turn.get("status"))
                turn_error = turn.get("error")
                if turn_error is not None or not failure_pending_completion:
                    session.error = turn_error
                self._consume_items(session, turn.get("items"))
                session.turn_start_ambiguous = False
            else:
                session.status = "failed"
                session.error = "turn/completed did not contain turn"
                session.turn_start_ambiguous = False
            session.turn_completed = True
            session.failure_pending_completion = False
            if session.status not in TERMINAL_STATUSES:
                session.status = "failed"
            if session.status == "completed":
                session.codex_attempted_provider_ids = {session.codex_model_provider} if session.codex_model_provider else set()
            # 背景実行の`atk agents wait`で回収済みの孫sessionは、保留の判定より前に追跡から外す。
            wait_output_tracking.consume_agents_wait_background_outputs(session)
            if resume_waits.has_pending_auto_resume_targets(session) and not session.auto_resume_consumed:
                # 未観測の孫sessionが残るturnは、待機表明を完了報告として公開せずに結果を保留する。
                # 監査記録は`docs/development/audit-records.md`の
                # 「agent-toolkit/agent_toolkit/_agents_server/codex.py：孫sessionの待機表明と自動再開：2026年10月1日」にある。
                # MCP層の常駐監視（`_monitor_auto_resume`）が孫の終端を観測し、同じsessionを一度だけ再開する。
                # 期限到来・追跡先の喪失・再開失敗の確定と未観測識別子の記録も、同じ監視が既存の診断で行う。
                resume_waits.begin_auto_resume_wait(
                    session, {"status": session.status, "agent_message": session.agent_message, "error": session.error}
                )
                session.status = "running"
            elif session.live_child_session_ids:
                unobserved_session_ids = set(session.live_child_session_ids)
                session.live_child_session_ids.clear()
                resume_waits.record_unobserved_sessions(session, unobserved_session_ids)
            elif self._hold_provider_failure(session):
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
                self._consume_item(session, item)
                item_id = item.get("id")
                completed_at_ms = params.get("completedAtMs")
                if (
                    item.get("type") == "contextCompaction"
                    and isinstance(item_id, str)
                    and item_id in session.compaction_started_at_ms
                    and isinstance(completed_at_ms, int)
                    and not isinstance(completed_at_ms, bool)
                ):
                    compaction_metrics.append_compaction_record(
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
    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        request_id = message.get("id")
        if not isinstance(method, str):
            return
        client = self.client
        if client is None:
            return
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            # IDなしの異常なserver requestへはJSON-RPC応答を返せないため、
            # 接続上のactive turnをfailedへ遷移させ、turn完了通知を待つ。
            await self._fail_for_request(params, method)
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
        await self._fail_for_request(params, method)
        await client.send(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unsupported non-interactive server request: {method}",
                },
            }
        )

    async def _fail_for_request(self, params: Any, method: str) -> None:
        session = self._find_session(params)
        if session is not None:
            sessions = [session] if not session.terminal else []
        else:
            sessions = [item for item in self._codex_sessions() if not item.terminal]
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
            if self.client is not None:
                self._schedule(self._interrupt(session_id, turn_id))

    async def _interrupt(self, session_id: str, turn_id: str) -> None:
        client = self.client
        if client is None or client.closed:
            return
        try:
            await client.request("turn/interrupt", {"threadId": session_id, "turnId": turn_id})
        except JsonRpcResponseError as exc:
            await self._handle_interrupt_response_error(session_id, turn_id, exc)
        except Exception as exc:
            await self._handle_client_failure(exc)

    async def _handle_interrupt_response_error(self, session_id: str, turn_id: str, error: JsonRpcResponseError) -> None:
        """turn/interruptのJSON-RPC errorを対象turnだけへ記録する。"""
        session = self._find_session({"threadId": session_id})
        if session is None or session.turn_completed or session.turn_id != turn_id:
            return
        session.error = {"message": str(error) or error.__class__.__name__}
        session.protocol_warnings.append(f"turn/interrupt failed: {error}")
        session.touch()
        await self._notify_waiters()

    async def _handle_client_failure(self, error: BaseException) -> None:
        """reader異常時に全active turnをfailedへ遷移させて待機者を起こす。"""
        detail = str(error) or error.__class__.__name__
        changed = False
        for session in self._codex_sessions():
            if not session.terminal or session.failure_pending_completion:
                session.status = "failed"
                if not session.failure_pending_completion:
                    session.error = {"message": f"Codex App Server stopped: {detail}"}
                session.interrupt_requested = False
                session.turn_start_ambiguous = False
                session.turn_completed = True
                session.failure_pending_completion = False
                session.touch()
                changed = True
        if changed:
            await self._notify_waiters()

    def _codex_sessions(self) -> list[SessionState]:
        """managerと共有する一覧から、このbackendが状態を更新できるsessionだけを返す。"""
        return [session for session in self.sessions.values() if session.engine == "codex"]

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
    def _track_subagent_thread(session: SessionState, item: Any) -> None:
        """開始・再操作で購読したsubagentを記録し、親だけのunsubscribeで残る資源を追跡する。"""
        if isinstance(item, dict) and item.get("type") == "subAgentActivity":
            thread_id = item.get("agentThreadId")
            if isinstance(thread_id, str) and thread_id and thread_id != session.session_id:
                session.codex_subagent_thread_ids.add(thread_id)

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
    def _consume_items(session: SessionState, items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, dict):
                AppServerManager._consume_item(session, item)

    @staticmethod
    def _consume_item(session: SessionState, item: dict[str, Any]) -> None:
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
                if len(arguments) >= 3 and Path(arguments[0]).name == "atk" and arguments[1:3] == ["agents", "wait"]:
                    wait_output_tracking.consume_agents_wait_output(session, output)
        elif item_type == "mcpToolCall" and item.get("server") == "agents_server":
            arguments = item.get("arguments")
            result = item.get("result")
            structured = result.get("structuredContent") if isinstance(result, dict) else None
            if isinstance(arguments, dict) and isinstance(structured, dict):
                consume_agents_server_tool_result(session, str(item.get("tool", "")), arguments, structured)

    async def close(self) -> None:
        """自身が起動したApp Server接続を終了する。"""
        current_task = asyncio.current_task()
        tasks = tuple(task for task in self._background_tasks if task is not current_task)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        client, self.client = self.client, None
        if client is not None:
            await client.close()


def _public_turn_status(status: Any) -> str:
    if status == "inProgress":
        return "running"
    if status in TERMINAL_STATUSES:
        return status
    return "failed"
