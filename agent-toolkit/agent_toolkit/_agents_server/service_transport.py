"""同じOSアカウントだけが使う管理サービスのローカル通信。"""

from __future__ import annotations

# 同期転送側は軽量に保ち、asyncio・worker・Win32は使う分岐だけで読み込む。
# pylint: disable=import-outside-toplevel
import contextlib
import hashlib
import json
import os
import pathlib
import socket
import struct
import threading
from collections.abc import Callable, Generator
from multiprocessing import connection
from typing import TYPE_CHECKING, Any

from agent_toolkit._common import runtime_environment, state_paths

if TYPE_CHECKING:
    import asyncio

_DISCONNECT_LOCK = threading.Lock()


class AsyncPeer:
    """長命な読取でファイル操作のexecutorを占有せず、取消時に所有I/Oを回収する。"""

    def __init__(self, peer: connection.Connection) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self.peer = peer
        # 読取が待機していても同じ接続への応答を送れるよう、双方向の枠を分ける。
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agents-server-peer")
        self._pending: set[asyncio.Future[Any]] = set()
        self._closing: asyncio.Task[None] | None = None

    async def __aenter__(self) -> AsyncPeer:
        return self

    async def __aexit__(self, *_error: object) -> None:
        await self.close()

    async def call(self, function: Callable[..., Any], *arguments: Any) -> Any:
        """JSON frameとOS接続の処理は既存のConnectionへ任せる。"""
        import asyncio

        if self._closing is not None:
            raise EOFError("管理サービスの接続は終了しています")
        future = asyncio.get_running_loop().run_in_executor(self._executor, function, self.peer, *arguments)
        self._pending.add(future)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            disconnect(self.peer)
            await asyncio.gather(future, return_exceptions=True)
            raise
        finally:
            self._pending.discard(future)

    async def close(self) -> None:
        """handleを閉じて読取を解除し、workerの終了まで回収する。"""
        import asyncio

        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self._closing)
        except asyncio.CancelledError:
            await asyncio.gather(self._closing, return_exceptions=True)
            raise

    async def _close(self) -> None:
        import asyncio

        try:
            disconnect(self.peer)
        finally:
            await asyncio.gather(*self._pending, return_exceptions=True)
            self._executor.shutdown(wait=True)


class _SocketListener:
    def __init__(self, stream: socket.socket) -> None:
        self._stream = stream

    def accept(self) -> connection.Connection:
        stream, _address = self._stream.accept()
        return connection.Connection(stream.detach())

    def close(self) -> None:
        self._stream.close()


def _state_directory(caller: runtime_environment.CallerContext) -> pathlib.Path:
    with runtime_environment.bind(caller):
        return state_paths.state_dir()


def namespace(caller: runtime_environment.CallerContext) -> pathlib.Path:
    """CLIと同じ状態領域を選び、明示的に分離した管理領域を混ぜない。"""
    base = _state_directory(caller) / "services" / "agents-server"
    if os.name == "nt":
        from agent_toolkit._agents_server.windows_service_transport import current_process_elevated

        # 従来の共通索引にはどちらの権限の世代も残り得るため、両方を分ける。
        suffix = "elevated" if current_process_elevated() else "standard"
        return base.with_name(f"{base.name}-{suffix}")
    return base


def isolated_state(caller: runtime_environment.CallerContext) -> bool:
    """呼出元の状態保存先を、指定がない場合の保存先と比べる。"""
    key = "LOCALAPPDATA" if os.name == "nt" else "XDG_STATE_HOME"
    default = runtime_environment.CallerContext(
        {name: value for name, value in caller.environment.items() if name != key},
        caller.cwd,
    )
    return _state_directory(caller) != _state_directory(default)


def endpoint(directory: pathlib.Path, generation: str) -> str:
    """長い状態パスも、OSのsocket名上限より短いローカル名へ対応付ける。"""
    identity = json.dumps([str(directory.resolve()), generation], ensure_ascii=False).encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()
    if os.name == "nt":
        return rf"\\.\pipe\agent-toolkit-{suffix}"
    return "\0agent-toolkit-" + suffix


def process_identity(pid: int) -> str:
    """PID再利用後の別プロセスを、発見記録のサービスと取り違えない。"""
    if os.name == "nt":
        from agent_toolkit._agents_server import windows_service_transport

        return windows_service_transport.process_identity(pid)
    # commには空白と丸括弧を含められるので、最後の閉じ括弧から固定欄を読む。
    record = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return f"{pid}:{record.rsplit(')', 1)[1].split()[19]}"


def process_alive(identity: object) -> bool:
    """発見記録の起動単位（`pid:開始時刻`）のプロセスがまだ生存しているかを返す。"""
    try:
        pid = int(str(identity).split(":", 1)[0])
        try:
            return process_identity(pid) == identity
        except PermissionError:
            # 自身のアカウントの/procは常に読めるため、Linuxでの拒否は別アカウントのプロセスを示す。
            # Windowsでは同じアカウントでも、非昇格のプロセスは別ログオンの昇格プロセス（SSH経由で起動した世代など）を開けない。
            if os.name != "nt":
                return False
            from agent_toolkit._agents_server import windows_service_transport

            return windows_service_transport.listed_process_alive(str(identity))
    except FileNotFoundError, ProcessLookupError, ValueError:
        return False


def authenticate(peer: connection.Connection, *, server: bool, expected_process: str | None = None) -> int:
    """環境・操作本文の送受信前に、OSが与える相手のアカウントが自身と一致するか確かめる。"""
    if os.name == "nt":
        from agent_toolkit._agents_server import windows_service_transport

        return windows_service_transport.authenticate(peer, server=server, expected_process=expected_process)
    with socket.socket(fileno=os.dup(peer.fileno())) as stream:
        pid, uid, _gid = struct.unpack("3i", stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    if uid != os.geteuid():
        raise PermissionError("管理サービスの接続先が同じOSアカウントではありません")
    if expected_process is not None and process_identity(pid) != expected_process:
        raise PermissionError("管理サービスの接続先プロセスが発見記録と一致しません")
    return pid


@contextlib.contextmanager
def listen(address: str) -> Generator[Any]:
    """受理した接続の寿命を呼出側へ渡し、listener自身は確実に閉じる。"""
    if os.name == "nt":
        from agent_toolkit._agents_server import windows_service_transport

        with windows_service_transport.listen(address) as listener:
            yield listener
        return
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.bind(address)
        stream.listen()
        stream.settimeout(0.5)
        yield _SocketListener(stream)


@contextlib.contextmanager
def connect(address: str, expected_process: str) -> Generator[connection.Connection]:
    """同一アカウント・元世代と確かめた接続だけを操作へ渡す。"""
    family = "AF_PIPE" if os.name == "nt" else "AF_UNIX"
    with contextlib.ExitStack() as stack:
        try:
            peer = stack.enter_context(connection.Client(address, family=family, authkey=None))
            authenticate(peer, server=False, expected_process=expected_process)
        except PermissionError as error:
            # 終了済みの世代への認証前の失敗だけを、操作未配送の接続失敗として扱う。
            if process_alive(expected_process):
                raise
            raise FileNotFoundError("発見記録の管理サービス世代が終了しました") from error
        yield peer


def send(peer: connection.Connection, payload: dict[str, Any]) -> None:
    """実行可能なpickleを使わず、JSONだけを送信する。"""
    peer.send_bytes(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def receive(peer: connection.Connection) -> dict[str, Any]:
    """認証済み接続のJSON objectを受け取り、境界で外形を確かめる。"""
    payload = json.loads(peer.recv_bytes())
    if not isinstance(payload, dict):
        raise ValueError("管理サービスのメッセージはJSON objectで指定する")
    return payload


def disconnect(peer: connection.Connection) -> None:
    """終了処理で別threadの読取を解除してから、接続handleを閉じる。"""
    # Connection.closeのclosed判定とCloseHandleは別操作なので、複数threadのcloseを原子的にする。
    # 送信側の状態ロックとは分け、待機中の送信をshutdown/closeで解除できるようにする。
    with _DISCONNECT_LOCK:
        if peer.closed:
            return
        if os.name != "nt":
            with contextlib.suppress(OSError, ValueError), socket.socket(fileno=os.dup(peer.fileno())) as stream:
                stream.shutdown(socket.SHUT_RDWR)
        peer.close()
