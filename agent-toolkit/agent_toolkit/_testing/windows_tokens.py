r"""Windowsの試験で、同じユーザーの非昇格のプロセスから見たアクセスを再現する。

昇格したtokenから`BUILTIN\Administrators`を拒否専用にしたtokenを作り、試験のスレッドだけで偽装する。
UACの非昇格のtokenと同じく、Administrators宛てのACEとOWNER RIGHTS（所有者がAdministratorsの場合）がアクセスを許可しない。
"""

from __future__ import annotations

import contextlib
import ctypes
import pathlib
from collections.abc import Generator
from ctypes import wintypes
from typing import Any

from agent_toolkit._common import windows_security

_windows: Any = ctypes
_ADMINISTRATORS = "S-1-5-32-544"
_TOKEN_QUERY = 0x0008
_TOKEN_DUPLICATE = 0x0002
_TOKEN_ASSIGN_PRIMARY = 0x0001
_TOKEN_IMPERSONATE = 0x0004
_GENERIC_WRITE = 0x40000000
_CREATE_NEW = 1
_FILE_ATTRIBUTE_NORMAL = 0x80
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", wintypes.BOOL)]


def _api() -> tuple[Any, Any]:
    advapi = _windows.WinDLL("advapi32", use_last_error=True)
    kernel = _windows.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    advapi_signatures = {
        "OpenProcessToken": ([wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL),
        "ConvertStringSidToSidW": ([wintypes.LPCWSTR, ctypes.POINTER(pointer)], wintypes.BOOL),
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": (
            [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), pointer],
            wintypes.BOOL,
        ),
        "CreateRestrictedToken": (
            [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, pointer, wintypes.DWORD, pointer, wintypes.DWORD, pointer]
            + [ctypes.POINTER(wintypes.HANDLE)],
            wintypes.BOOL,
        ),
        "ImpersonateLoggedOnUser": ([wintypes.HANDLE], wintypes.BOOL),
        "RevertToSelf": ([], wintypes.BOOL),
    }
    kernel_signatures = {
        "CreateDirectoryW": ([wintypes.LPCWSTR, pointer], wintypes.BOOL),
        "CreateFileW": (
            [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, pointer, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE],
            wintypes.HANDLE,
        ),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "LocalFree": ([pointer], pointer),
    }
    for library, signatures in ((advapi, advapi_signatures), (kernel, kernel_signatures)):
        for name, (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
    return advapi, kernel


def _check(result: Any) -> Any:
    if not result:
        raise _windows.WinError(_windows.get_last_error())
    return result


@contextlib.contextmanager
def _process_token() -> Generator[Any]:
    advapi, kernel = _api()
    token = wintypes.HANDLE()
    access = _TOKEN_QUERY | _TOKEN_DUPLICATE | _TOKEN_ASSIGN_PRIMARY | _TOKEN_IMPERSONATE
    _check(advapi.OpenProcessToken(kernel.GetCurrentProcess(), access, ctypes.byref(token)))
    try:
        yield token
    finally:
        kernel.CloseHandle(token)


@contextlib.contextmanager
def _security_attributes(sddl: str) -> Generator[Any]:
    advapi, kernel = _api()
    descriptor = ctypes.c_void_p()
    _check(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None))
    attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
    try:
        yield ctypes.byref(attributes)
    finally:
        kernel.LocalFree(descriptor)


def create_directory(path: pathlib.Path, sddl: str) -> None:
    """SDDLで指定したセキュリティ記述子を付けてディレクトリを作成する。"""
    _, kernel = _api()
    with _security_attributes(sddl) as attributes:
        _check(kernel.CreateDirectoryW(str(path), attributes))


def create_file(path: pathlib.Path, sddl: str) -> None:
    """SDDLで指定したセキュリティ記述子を付けて空のファイルを作成する。DACLを省いたSDDLでは親から継承する。"""
    _, kernel = _api()
    with _security_attributes(sddl) as attributes:
        handle = kernel.CreateFileW(str(path), _GENERIC_WRITE, 0, attributes, _CREATE_NEW, _FILE_ATTRIBUTE_NORMAL, None)
        if handle == _INVALID_HANDLE_VALUE:
            raise _windows.WinError(_windows.get_last_error())
    kernel.CloseHandle(handle)


def create_profile_directory(path: pathlib.Path) -> None:
    r"""`%LOCALAPPDATA%`と同じく、SYSTEM・Administrators・ユーザーへ継承するACLのディレクトリを作成する。"""
    create_directory(path, f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{windows_security.user_sid()})")


@contextlib.contextmanager
def without_administrators() -> Generator[None]:
    """実行中のスレッドを、Administratorsを拒否専用にした同じユーザーのtokenで偽装する。"""
    advapi, kernel = _api()
    administrators = ctypes.c_void_p()
    _check(advapi.ConvertStringSidToSidW(_ADMINISTRATORS, ctypes.byref(administrators)))
    try:
        disabled = (_SidAndAttributes * 1)(_SidAndAttributes(administrators, 0))
        limited = wintypes.HANDLE()
        with _process_token() as token:
            _check(advapi.CreateRestrictedToken(token, 0, 1, disabled, 0, None, 0, None, ctypes.byref(limited)))
        try:
            _check(advapi.ImpersonateLoggedOnUser(limited))
            try:
                yield
            finally:
                _check(advapi.RevertToSelf())
        finally:
            kernel.CloseHandle(limited)
    finally:
        kernel.LocalFree(administrators)


def readable_without_administrators(path: pathlib.Path) -> bool:
    """同じユーザーの非昇格のプロセスがファイルを読めるかを返す。"""
    with without_administrators():
        try:
            path.read_bytes()
        except PermissionError:
            return False
    return True
