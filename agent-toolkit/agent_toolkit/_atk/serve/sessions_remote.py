"""セッション画面のリモート接続と応答の正規化。"""

import asyncio
import asyncio.subprocess as _async_subprocess
import json
import logging
import pathlib
import typing

from agent_toolkit._atk import session_record_format
from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve.sessions_events import _relay_remote_notification
from agent_toolkit._atk.serve.sessions_models import (
    _NO_FALLBACK_ON_RPC_TIMEOUT,
    BACKOFF_INITIAL_SEC,
    BACKOFF_MAX_SEC,
    OPERATION_TIMEOUT_SEC,
    REMOTE_BOOTSTRAP,
    REMOTE_STREAM_LIMIT_BYTES,
    SSH_BASE_OPTIONS,
    SSH_WATCH_OPTIONS,
    SessionsContext,
    SessionsState,
    SessionSummary,
    _isoformat,
)
from agent_toolkit._common import stream_lines

logger = logging.getLogger("agent_toolkit._atk.serve.sessions")


# 単発SSHの失敗の表現と標準エラー出力の整形は計画ファイル画面と共通の契約とする。
RemoteHelperError = _atk_serve_remote.RemoteHelperError


_stderr_excerpt = _atk_serve_remote.stderr_excerpt


async def default_ssh_runner(host: str, op: str, args: list[str]) -> str:
    """SSH経由でこの画面のリモートヘルパーを単発実行し、stdoutをUTF-8文字列で返す。

    `list`には常駐接続と同じ接続確立の打ち切りと生存確認を付け、上限が長くても
    到達できないホストの失敗を接続確立の段階で数秒のうちに返す。
    """
    ssh_options = (*SSH_BASE_OPTIONS, *SSH_WATCH_OPTIONS) if op == "list" else SSH_BASE_OPTIONS
    return await _atk_serve_remote.run_remote_helper(
        REMOTE_BOOTSTRAP, host, op, args, ssh_options=ssh_options, timeout=OPERATION_TIMEOUT_SEC[op]
    )


class RemoteSessionClient:
    """1ホスト分の常駐SSH接続とRPCを担う。

    `run()`は接続を維持し、切断時はバックオフして再接続する。
    RPCを送れない状態では呼び出し元が単発SSHへ切り替える。
    """

    def __init__(self, host: str, state: SessionsState) -> None:
        self.host = host
        self.state = state
        self._proc: _async_subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[dict[str, typing.Any]]] = {}
        self._next_request_id = 1
        self._send_lock = asyncio.Lock()
        self._connected = False
        self._backoff = BACKOFF_INITIAL_SEC
        self._stderr_task: asyncio.Task[None] | None = None

    def is_connected(self) -> bool:
        """RPCを送信可能な状態かを返す。"""
        if not self._connected:
            return False
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        return not proc.stdin.is_closing()

    async def request(self, op: str, args: dict[str, typing.Any] | None = None) -> dict[str, typing.Any]:
        """常駐SSH接続経由でRPCリクエストを送信し、応答辞書を返す。

        応答が操作ごとの上限（`OPERATION_TIMEOUT_SEC`）までに届かない場合は、操作名と上限秒数を含む`TimeoutError`を送出する。
        """
        proc = self._proc
        if not self.is_connected() or proc is None or proc.stdin is None or proc.stdin.is_closing():
            raise RuntimeError(f"session helper not connected: host={self.host}")
        loop = asyncio.get_running_loop()
        req_id = self._next_request_id
        self._next_request_id += 1
        future: asyncio.Future[dict[str, typing.Any]] = loop.create_future()
        self._pending[req_id] = future
        line = json.dumps({"id": req_id, "op": op, **(args or {})}, ensure_ascii=False) + "\n"
        try:
            return await _atk_serve_remote.send_rpc_request(
                proc, self._send_lock, line, future, op=op, timeout=OPERATION_TIMEOUT_SEC[op]
            )
        finally:
            self._pending.pop(req_id, None)

    async def run(self) -> None:
        """接続→応答処理→バックオフ→再接続を繰り返す。"""
        while True:
            await self._set_status("connecting")
            proc: _async_subprocess.Process | None = None
            try:
                proc = await self._connect()
                self._proc = proc
                assert proc.stdout is not None
                await self._process_stream(proc.stdout)
                await self._set_status("disconnected")
            except asyncio.CancelledError:
                self._fail_pending(asyncio.CancelledError("session client cancelled"))
                raise
            except Exception as error:  # noqa: BLE001
                logger.warning("セッション記録の常駐接続に失敗 host=%s: %s", self.host, error)
                await self._set_status("disconnected")
            finally:
                self._fail_pending(ConnectionError(f"session helper disconnected: host={self.host}"))
                stderr_task, self._stderr_task = self._stderr_task, None
                self._connected = False
                # 停止を確認するまで参照を保持し、確認できない間は次の接続を起動しない。
                await _atk_serve_remote.stop_resident_helper_until_confirmed(proc, stderr_task, backoff=self._backoff)
                self._proc = None
            _atk_serve_remote.raise_if_cancelling()
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, BACKOFF_MAX_SEC)

    async def _connect(self) -> _async_subprocess.Process:
        proc, self._stderr_task = await _atk_serve_remote.start_resident_helper(
            REMOTE_BOOTSTRAP,
            self.host,
            ssh_options=(*SSH_BASE_OPTIONS, *SSH_WATCH_OPTIONS),
            stream_limit=REMOTE_STREAM_LIMIT_BYTES,
            logger=logger,
            label="セッション記録の常駐接続",
        )
        return proc

    async def _process_stream(self, stream: asyncio.StreamReader) -> None:
        """行ストリームを読み、`ready`で接続確立、`response`でRPCを解決し、変更通知をSSEへ中継する。"""
        async for chunk in stream_lines.iter_lines(stream):
            line = chunk.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                logger.warning("セッション記録の応答を解析できません host=%s: %s", self.host, error)
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "ready":
                self._connected = True
                self._backoff = BACKOFF_INITIAL_SEC
                await self._set_status("connected")
                continue
            if event.get("type") == "response":
                self._resolve_response(event)
                continue
            await _relay_remote_notification(self.state, self.host, event)

    def _resolve_response(self, event: dict[str, typing.Any]) -> None:
        req_id = event.get("id")
        if not isinstance(req_id, int):
            return
        future = self._pending.get(req_id)
        if future is None or future.done():
            return
        future.set_result(dict(event))

    def _fail_pending(self, exc: BaseException) -> None:
        if not self._pending:
            return
        pending = self._pending
        self._pending = {}
        for future in pending.values():
            if not future.done():
                future.set_exception(exc)

    async def _set_status(self, status: str) -> None:
        async with self.state.lock:
            self.state.host_status[self.host] = status


def is_safe_remote_record_path(raw: str) -> bool:
    """リモートへ渡す前に記録のパスを検証する。

    上位ディレクトリへの参照と対象外の接尾辞を拒否する。
    リモート側でも同じ検証を行うが、サーバー側で先に拒否することで不要なSSH呼び出しを避ける。
    """
    if not raw or not raw.endswith(session_record_format.RECORD_SUFFIX):
        return False
    path = pathlib.PureWindowsPath(raw) if "\\" in raw else pathlib.PurePosixPath(raw)
    return path.is_absolute() and ".." not in path.parts


async def _remote_call(context: SessionsContext, host: str, op: str, args: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """常駐RPCを優先し、未接続・失敗と、`_NO_FALLBACK_ON_RPC_TIMEOUT`以外の期限超過では単発SSHへ切り替える。"""
    client = context.state.clients.get(host)
    if client is not None and client.is_connected():
        try:
            response = await client.request(op, args)
        except Exception as error:  # noqa: BLE001
            if isinstance(error, TimeoutError) and op in _NO_FALLBACK_ON_RPC_TIMEOUT:
                raise
            logger.warning("セッション記録のRPCに失敗 host=%s op=%s: %s（単発SSHへ）", host, op, error)
        else:
            if response.get("ok"):
                return response
            logger.warning(
                "セッション記録のRPCがエラーを返した host=%s op=%s: %s（単発SSHへ）",
                host,
                op,
                response.get("error"),
            )
    argv = [str(args["path"])] if op == "read" else []
    raw = await context.runner(host, op, argv)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"リモート応答の形式が不正です: host={host}")
    return payload


async def _remote_sessions(context: SessionsContext, host: str) -> tuple[list[SessionSummary], dict[str, str] | None]:
    """1台のリモートホストの一覧を取得する。取得できない場合は失敗の内容を警告として返す。"""
    try:
        payload = await _remote_call(context, host, "list", {})
    except Exception as error:  # noqa: BLE001
        logger.warning("リモートのセッション一覧を取得できません host=%s: %s", host, error)
        return [], {"host": host, "reason": f"記録を取得できません: {error}"}
    entries: list[SessionSummary] = []
    for item in payload.get("entries", []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        entries.append(
            SessionSummary(
                engine=str(item.get("engine", "")),
                host=host,
                cwd=item.get("cwd") if isinstance(item.get("cwd"), str) else None,
                first_user_message=(
                    item.get("first_user_message") if isinstance(item.get("first_user_message"), str) else None
                ),
                session_id=str(item.get("session_id", "")),
                path=str(item["path"]),
                started_at=item.get("started_at") if isinstance(item.get("started_at"), str) else None,
                updated_at=_isoformat(item["updated_at"]) if isinstance(item.get("updated_at"), (int, float)) else None,
                size=item.get("size") if isinstance(item.get("size"), int) else None,
                warning=item.get("warning") if isinstance(item.get("warning"), str) else None,
                parent_path=item.get("parent_path") if isinstance(item.get("parent_path"), str) else None,
            )
        )
    return entries, None


def _remote_subagents(engine: str, payload: dict[str, typing.Any]) -> tuple[list[dict[str, typing.Any]] | None, bool]:
    """リモートの読み取り応答から、サブエージェント一覧と判定不能かどうかを返す。

    リモートホストのdotfilesが古く、サブエージェント一覧を返さない版のヘルパーが動いている場合は、
    読み取り自体が成功したままその欄だけが欠ける。サブエージェントが無い場合と区別するため、
    欄が無い応答は判定不能として扱う。Codexの記録はサブエージェントを持たないため判定不能としない。
    """
    if engine != "claude":
        return None, False
    found = payload.get("subagents")
    if not isinstance(found, list):
        return None, True
    # ローカルと同じく、0件は`None`で表して「サブエージェントが無い」ことを示す。
    return found or None, False
