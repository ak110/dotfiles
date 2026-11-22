"""現在のアカウントだけに許可したWindows named pipeと相手世代の検証。"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import threading
import time
from ctypes import wintypes as w
from multiprocessing import connection
from typing import Any

# Windows専用属性はLinuxの型情報に無いため、OS境界内で動的に解決する。
_windows: Any = ctypes
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x100000


def _api() -> tuple[Any, Any]:
    kernel = _windows.WinDLL("kernel32", use_last_error=True)
    security = _windows.WinDLL("advapi32", use_last_error=True)
    signatures = {
        "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "GetProcessTimes": ([w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "LocalFree": ([ctypes.c_void_p], ctypes.c_void_p),
        "GetNamedPipeClientProcessId": ([w.HANDLE, ctypes.POINTER(w.ULONG)], w.BOOL),
        "GetNamedPipeServerProcessId": ([w.HANDLE, ctypes.POINTER(w.ULONG)], w.BOOL),
        "CreateNamedPipeW": ([w.LPCWSTR, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p], w.HANDLE),
    }
    for name, (args, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    for name, args in {
        "OpenProcessToken": [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)],
        "GetTokenInformation": [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)],
        "ConvertSidToStringSidW": [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)],
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": [
            w.LPCWSTR,
            w.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        ],
    }.items():
        function = getattr(security, name)
        function.argtypes, function.restype = args, w.BOOL
    return kernel, security


def _check(result: Any) -> Any:
    if not result:
        raise _windows.WinError(_windows.get_last_error())
    return result


@contextlib.contextmanager
def _process(pid: int, *, require_running: bool = False):
    kernel, _ = _api()
    access = _PROCESS_QUERY_LIMITED_INFORMATION
    if require_running:
        access |= _SYNCHRONIZE
    handle = kernel.OpenProcess(access, False, pid)
    if not handle:
        code = _windows.get_last_error()
        error = _windows.WinError(code)
        # 終了済みPIDのOpenProcessはERROR_INVALID_PARAMETERを返す。古い世代を
        # 再利用しない判定へ渡し、アクセス拒否など他のAPI失敗はそのまま保持する。
        if pid > 0 and code == 87:
            raise ProcessLookupError(errno.ESRCH, os.strerror(errno.ESRCH), pid) from error
        raise error
    try:
        if require_running:
            # 他のhandleが残ると、終了済みのプロセスも開けて作成時刻を読める。
            status = kernel.WaitForSingleObject(handle, 0)
            _check(status != 0xFFFFFFFF)  # WAIT_FAILED
            if status == 0:  # WAIT_OBJECT_0: プロセスが終了した。
                raise ProcessLookupError(errno.ESRCH, os.strerror(errno.ESRCH), pid)
        yield handle
    finally:
        _check(kernel.CloseHandle(handle))


def current_process_elevated() -> bool:
    """状態領域の選択に、自分の実トークンの昇格状態を使う。"""
    kernel, security = _api()
    token = w.HANDLE()
    with _process(os.getpid()) as handle:
        _check(security.OpenProcessToken(handle, 0x0008, ctypes.byref(token)))  # TOKEN_QUERY
    try:
        value = w.DWORD(0)
        used = w.DWORD(0)
        _check(security.GetTokenInformation(token, 20, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(used)))
        return bool(value.value)  # TokenElevation
    finally:
        _check(kernel.CloseHandle(token))


def _identity(handle: int, pid: int) -> str:
    kernel, _ = _api()
    created, exited, system, user = (ctypes.c_uint64(0) for _ in range(4))
    _check(
        kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user))
    )
    return f"{pid}:{created.value}"


def process_identity(pid: int) -> str:
    """同じhandleで生存を確認し、作成時刻でPID再利用を区別する。"""
    with _process(pid, require_running=True) as handle:
        return _identity(handle, pid)


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT), ("Buffer", ctypes.c_void_p)]


class _SystemProcessInformation(ctypes.Structure):
    """`NtQuerySystemInformation(SystemProcessInformation)`が返すプロセス1件の先頭部分。"""

    _fields_ = [
        ("NextEntryOffset", w.ULONG),
        ("NumberOfThreads", w.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_int64),
        ("HardFaultCount", w.ULONG),
        ("NumberOfThreadsHighWatermark", w.ULONG),
        ("CycleTime", ctypes.c_uint64),
        ("CreateTime", ctypes.c_int64),
        ("UserTime", ctypes.c_int64),
        ("KernelTime", ctypes.c_int64),
        ("ImageName", _UnicodeString),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
    ]


_SYSTEM_PROCESS_INFORMATION = 5
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_STATUS_BUFFER_TOO_SMALL = 0xC0000023


def _find_create_time(snapshot: Any, pid: int) -> int | None:
    """プロセス一覧のバッファから、指定PIDの作成時刻（FILETIME）を返す。一覧に無ければ`None`を返す。"""
    offset = 0
    while True:
        entry = _SystemProcessInformation.from_buffer(snapshot, offset)
        if entry.UniqueProcessId == pid:
            return entry.CreateTime
        if not entry.NextEntryOffset:
            return None
        offset += entry.NextEntryOffset


def _listed_create_time(pid: int) -> int | None:
    """開く権限の要らないシステムのプロセス一覧から、指定PIDの作成時刻（FILETIME）を返す。"""
    query = _windows.WinDLL("ntdll").NtQuerySystemInformation
    query.argtypes = [ctypes.c_int, ctypes.c_void_p, w.ULONG, ctypes.POINTER(w.ULONG)]
    query.restype = ctypes.c_long
    size = 1 << 20
    while True:
        snapshot = ctypes.create_string_buffer(size)
        needed = w.ULONG(0)
        status = query(_SYSTEM_PROCESS_INFORMATION, snapshot, size, ctypes.byref(needed))
        if status & 0xFFFFFFFF in (_STATUS_INFO_LENGTH_MISMATCH, _STATUS_BUFFER_TOO_SMALL):
            # 一覧は照会ごとに変わるため、返された必要量に余裕を持たせて取り直す。
            # 必要量が返らない（0）場合も取り直しが終わるよう、少なくとも倍へ広げる。
            size = max(needed.value + (1 << 16), size * 2)
            continue
        if status < 0:
            raise OSError(f"プロセス一覧の取得に失敗しました: NTSTATUS=0x{status & 0xFFFFFFFF:08X}")
        return _find_create_time(snapshot, pid)


def listed_process_alive(identity: str) -> bool:
    """開けないプロセスが記録した起動単位のプロセスかを、システムのプロセス一覧の作成時刻で判定する。

    転送側は標準ライブラリだけで動くため、psutilは使わずntdllから一覧を得る。
    一覧の作成時刻は`GetProcessTimes`と同じFILETIMEで、記録した起動単位と一致を比べる。
    PIDが再利用されていれば作成時刻が異なり、一覧に無ければ終了した世代として扱う。
    """
    pid, created = (int(part) for part in identity.split(":", 1))
    return _listed_create_time(pid) == created


def _sid(handle: int) -> str:
    kernel, security = _api()
    token = w.HANDLE(None)
    _check(security.OpenProcessToken(handle, 8, ctypes.byref(token)))
    try:
        size = w.DWORD(0)
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if _windows.get_last_error() != 122:
            raise _windows.WinError(_windows.get_last_error())
        data = ctypes.create_string_buffer(size.value)
        _check(security.GetTokenInformation(token, 1, data, size, ctypes.byref(size)))
        sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
        value = w.LPWSTR()
        _check(security.ConvertSidToStringSidW(sid, ctypes.byref(value)))
        try:
            result = value.value
            if result is None:
                raise OSError("token SIDを文字列へ変換できません")
            return result
        finally:
            kernel.LocalFree(ctypes.cast(value, ctypes.c_void_p))
    finally:
        _check(kernel.CloseHandle(token))


def authenticate(peer: Any, *, server: bool, expected_process: str | None = None) -> int:
    """本文受信前に、pipeが報告したPIDのtoken SIDと元世代が自身の期待値と一致するか確かめる。"""
    kernel, _ = _api()
    pid = w.ULONG(0)
    function = kernel.GetNamedPipeClientProcessId if server else kernel.GetNamedPipeServerProcessId
    _check(function(peer.fileno(), ctypes.byref(pid)))
    with _process(pid.value) as remote, _process(os.getpid()) as local:
        if _sid(remote) != _sid(local):
            raise PermissionError("管理サービスの接続相手のSIDが一致しません")
        if expected_process is not None and _identity(remote, pid.value) != expected_process:
            from agent_toolkit._agents_server.service_transport import ProcessIdentityMismatch  # noqa: PLC0415

            raise ProcessIdentityMismatch("管理サービスの接続相手の起動世代が一致しません")
    return pid.value


class _Attributes(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]


class _Listener:
    def __init__(self, address: str) -> None:
        self.address = address
        self.handle = None
        self.closed = False
        self.lock = threading.Lock()
        self.accepting = False
        # 時間切れで返った受付を次のacceptへ引き継ぐ。取り消すと待受instanceが一時的に消え、
        # 接続側のWaitNamedPipeが即座にFileNotFoundErrorで失敗する。
        self.pending = None
        self._create()

    def _create(self) -> None:
        kernel, security = _api()
        with _process(os.getpid()) as process:
            sid = _sid(process)
        descriptor = ctypes.c_void_p()
        _check(
            security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"D:P(A;;GA;;;{sid})", 1, ctypes.byref(descriptor), None
            )
        )
        try:
            attributes = _Attributes(ctypes.sizeof(_Attributes), descriptor, False)
            # Overlapped duplex message pipe。リモート接続をOSで拒否する。
            flags = 3 | 0x40000000 | (0x00080000 if not hasattr(self, "created") else 0)
            handle = kernel.CreateNamedPipeW(self.address, flags, 4 | 2 | 8, 255, 65536, 65536, 0, ctypes.byref(attributes))
            if handle == ctypes.c_void_p(-1).value:
                raise _windows.WinError(_windows.get_last_error())
            self.handle = handle
            self.created = True
        finally:
            kernel.LocalFree(descriptor)

    def accept(self, timeout: float = 0.5):
        """接続を受理する。`timeout`秒で受理が無ければ受付を保ったまま`TimeoutError`を送出する。

        POSIXのsocket listenerと同じく呼出側へ定期的に戻り、退出条件を再確認させる。
        """
        import _winapi  # pylint: disable=import-error,import-outside-toplevel  # Windowsでだけ呼び出す標準API。

        native: Any = _winapi

        with self.lock:
            if self.closed:
                raise OSError("listenerは終了しています")
            if self.handle is None:
                self._create()
            handle = self.handle
            self.accepting = True
        operation = self.pending
        try:
            if operation is None:
                operation = native.ConnectNamedPipe(handle, overlapped=True)
                self.pending = operation
            deadline = time.monotonic() + timeout
            while native.WaitForSingleObject(operation.event, 100) == 258:
                if self.closed:
                    raise OSError("listenerは終了しています")
                if time.monotonic() >= deadline:
                    with self.lock:
                        self.accepting = False
                    raise TimeoutError("接続待ちの時間切れ")
            self.pending = None
            _, error = operation.GetOverlappedResult(True)
            if error:
                raise _windows.WinError(error)
            with self.lock:
                if self.closed:
                    raise OSError("listenerは終了しています")
                self.handle = None
                self._create()
                self.accepting = False
            pipe_connection: Any = connection
            return pipe_connection.PipeConnection(handle)
        except TimeoutError:
            raise
        except BaseException:
            self.pending = None
            if operation is not None:
                operation.cancel()
                operation.GetOverlappedResult(True)
            with self.lock:
                native.CloseHandle(handle)
                if self.handle == handle:
                    self.handle = None
                self.accepting = False
            raise

    def close(self) -> None:
        with self.lock:
            self.closed = True
            if self.handle is not None and not self.accepting:
                import _winapi  # pylint: disable=import-error,import-outside-toplevel  # Windowsでだけ呼び出す標準API。

                native: Any = _winapi
                if self.pending is not None:
                    self.pending.cancel()
                    self.pending.GetOverlappedResult(True)
                    self.pending = None
                native.CloseHandle(self.handle)
                self.handle = None


@contextlib.contextmanager
def listen(address: str):
    """listener所有handleを例外時も解放し、受理済み接続は呼出側へ渡す。"""
    listener = _Listener(address)
    try:
        yield listener
    finally:
        listener.close()
