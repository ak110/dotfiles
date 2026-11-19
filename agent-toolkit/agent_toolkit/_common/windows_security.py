r"""Windowsのtokenの昇格とACLを調べる。

Windowsの`mkdir(mode=0o700)`と`tempfile.mkdtemp`は`D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)`の保護付きDACLを付ける。
配下へ昇格したプロセスが書いたファイルは所有者が`BUILTIN\Administrators`になり、同じユーザーの非昇格のプロセスが読めない。
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import os
import pathlib
from collections.abc import Generator
from ctypes import wintypes
from typing import Any

# Windows専用属性はLinuxの型情報に無いため、OS境界内で動的に解決する。
_windows: Any = ctypes

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_TOKEN_ELEVATION = 20


@functools.cache
def _api() -> tuple[Any, Any]:
    advapi = _windows.WinDLL("advapi32", use_last_error=True)
    kernel = _windows.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    advapi_signatures = {
        "GetNamedSecurityInfoW": (
            [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, pointer, pointer, pointer, pointer, ctypes.POINTER(pointer)],
            wintypes.DWORD,
        ),
        "ConvertSecurityDescriptorToStringSecurityDescriptorW": (
            [pointer, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), pointer],
            wintypes.BOOL,
        ),
        "OpenProcessToken": ([wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL),
        "GetTokenInformation": (
            [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        ),
        "ConvertSidToStringSidW": ([pointer, ctypes.POINTER(wintypes.LPWSTR)], wintypes.BOOL),
    }
    kernel_signatures = {
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "LocalFree": ([pointer], pointer),
    }
    for library, signatures in ((advapi, advapi_signatures), (kernel, kernel_signatures)):
        for name, (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
    return advapi, kernel


def _error(code: int, path: pathlib.Path | str) -> OSError:
    # winerrorから組み込みの派生例外（アクセス拒否ならPermissionError）を選ばせる。
    return OSError(None, _windows.FormatError(code), str(path), code)


def _native(path: pathlib.Path | str) -> str:
    r"""MAX_PATHを超える深い配下も扱えるよう、ローカルの絶対パスへ`\\?\`を付ける。"""
    text = os.path.abspath(path)
    return text if text.startswith("\\\\") else "\\\\?\\" + text


@contextlib.contextmanager
def _process_token() -> Generator[Any]:
    advapi, kernel = _api()
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _windows.WinError(_windows.get_last_error())
    try:
        yield token
    finally:
        kernel.CloseHandle(token)


def is_elevated() -> bool:
    """Windowsで管理者として昇格したtokenで実行中かを返す。Windows以外では偽を返す。"""
    if os.name != "nt":
        return False
    advapi, _ = _api()
    with _process_token() as token:
        elevation = wintypes.DWORD(0)
        size = wintypes.DWORD(0)
        if not advapi.GetTokenInformation(
            token, _TOKEN_ELEVATION, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(size)
        ):
            raise _windows.WinError(_windows.get_last_error())
        return bool(elevation.value)


def user_sid() -> str:
    """実行中のプロセスのユーザーSIDを文字列で返す。"""
    advapi, kernel = _api()
    with _process_token() as token:
        size = wintypes.DWORD(0)
        advapi.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, _TOKEN_USER, buffer, size, ctypes.byref(size)):
            raise _windows.WinError(_windows.get_last_error())
    sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    text = wintypes.LPWSTR()
    if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise _windows.WinError(_windows.get_last_error())
    try:
        return str(text.value)
    finally:
        kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def _descriptor_sddl(descriptor: Any, path: pathlib.Path | str) -> str:
    advapi, kernel = _api()
    text = wintypes.LPWSTR()
    if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        descriptor, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION, ctypes.byref(text), None
    ):
        raise _error(_windows.get_last_error(), path)
    try:
        return text.value or ""
    finally:
        kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def dacl_sddl(path: pathlib.Path | str) -> str:
    """対象のDACLをSDDLで返す。READ_CONTROLを持たないtokenでは`PermissionError`を送出する。"""
    advapi, kernel = _api()
    descriptor = ctypes.c_void_p()
    status = advapi.GetNamedSecurityInfoW(
        _native(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, None, None, ctypes.byref(descriptor)
    )
    if status:
        raise _error(status, path)
    try:
        return _descriptor_sddl(descriptor, path)
    finally:
        kernel.LocalFree(descriptor)
