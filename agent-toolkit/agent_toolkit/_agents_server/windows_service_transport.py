"""現在のアカウントだけに許可したWindows named pipeと相手世代の検証。"""

from __future__ import annotations

import contextlib
import ctypes
import os
import threading
from ctypes import wintypes as w
from multiprocessing import connection
from typing import Any

# Windows専用属性はLinuxの型情報に無いため、OS境界内で動的に解決する。
_windows: Any = ctypes


def _api() -> tuple[Any, Any]:
    kernel = _windows.WinDLL("kernel32", use_last_error=True)
    security = _windows.WinDLL("advapi32", use_last_error=True)
    signatures = {
        "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
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
def _process(pid: int):
    kernel, _ = _api()
    handle = _check(kernel.OpenProcess(0x1000, False, pid))
    try:
        yield handle
    finally:
        _check(kernel.CloseHandle(handle))


def _identity(handle: int, pid: int) -> str:
    kernel, _ = _api()
    created, exited, system, user = (ctypes.c_uint64(0) for _ in range(4))
    _check(
        kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user))
    )
    return f"{pid}:{created.value}"


def process_identity(pid: int) -> str:
    """保持したprocess handleの作成時刻でPID再利用を区別する。"""
    with _process(pid) as handle:
        return _identity(handle, pid)


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
            raise PermissionError("管理サービスの接続相手の起動世代が一致しません")
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

    def accept(self):
        import _winapi  # pylint: disable=import-error,import-outside-toplevel  # Windowsでだけ呼び出す標準API。

        native: Any = _winapi

        with self.lock:
            if self.closed:
                raise OSError("listenerは終了しています")
            if self.handle is None:
                self._create()
            handle = self.handle
            self.accepting = True
        operation = None
        try:
            operation = native.ConnectNamedPipe(handle, overlapped=True)
            while native.WaitForSingleObject(operation.event, 100) == 258:
                if self.closed:
                    raise OSError("listenerは終了しています")
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
        except BaseException:
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
