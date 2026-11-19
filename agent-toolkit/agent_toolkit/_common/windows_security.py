r"""Windowsのtokenの昇格の判定と、OWNER RIGHTSに依存するACLから継承するACLへの復元。

旧版はWindowsでも`mkdir(mode=0o700)`と`tempfile.mkdtemp`で状態ディレクトリを作成し、
`D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)`の保護付きDACLを付けた（`private_directory`の説明を参照）。
配下へ昇格したプロセスが書いたファイルは所有者が`BUILTIN\Administrators`になり、同じユーザーの非昇格のプロセスが読めない。
`restore_inheritance`は、そのディレクトリを親から継承するACLへ戻し、配下へ継承をやり直させる。
"""

from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import functools
import os
import pathlib
import sys
from collections.abc import Generator
from ctypes import wintypes
from typing import Any

# Windows専用属性はLinuxの型情報に無いため、OS境界内で動的に解決する。
_windows: Any = ctypes

_LEGACY_OWNER_RIGHTS_ACE = "(A;OICI;FA;;;OW)"
"""旧版が明示したOWNER RIGHTS（S-1-3-4）のACE。

継承したACE（`ID`付き）は対象にしない。root内の継承は明示したディレクトリを戻すとOSが計算し直し、
root外の親から継承したACEはroot内の操作では変わらず、毎回の走査で全配下を対象と誤認するためである。
"""
_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
_SDDL_REVISION_1 = 1
_ACL_REVISION = 2
_EMPTY_ACL_SIZE = 8
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_TOKEN_ELEVATION = 20
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_ATTRIBUTE_TAG_INFO = 9
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_UAC_POLICY_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
_BUILTIN_ADMINISTRATOR_RID = "-500"


@dataclasses.dataclass(frozen=True)
class InheritanceRepair:
    """`restore_inheritance`の結果。"""

    restored: tuple[pathlib.Path, ...] = ()
    """継承するACLへ戻したディレクトリ。"""
    blocked: tuple[tuple[pathlib.Path, str], ...] = ()
    """実行中のtokenでは配下まで戻せないため変更しなかったディレクトリと理由。"""


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
        "GetSecurityInfo": (
            [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, pointer, pointer, pointer, pointer, ctypes.POINTER(pointer)],
            wintypes.DWORD,
        ),
        "SetSecurityInfo": (
            [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, pointer, pointer, pointer, pointer],
            wintypes.DWORD,
        ),
        "ConvertSecurityDescriptorToStringSecurityDescriptorW": (
            [pointer, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), pointer],
            wintypes.BOOL,
        ),
        "InitializeAcl": ([pointer, wintypes.DWORD, wintypes.DWORD], wintypes.BOOL),
        "OpenProcessToken": ([wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL),
        "GetTokenInformation": (
            [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        ),
        "ConvertSidToStringSidW": ([pointer, ctypes.POINTER(wintypes.LPWSTR)], wintypes.BOOL),
    }
    kernel_signatures = {
        "CreateFileW": (
            [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, pointer, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE],
            wintypes.HANDLE,
        ),
        "GetFileInformationByHandleEx": ([wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD], wintypes.BOOL),
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


def is_elevated_above_user_processes() -> bool:
    """同じユーザーの通常のプロセスが非昇格で動くWindowsで、昇格したtokenで実行中かを返す。Windows以外では偽を返す。

    UACの管理者承認モードでは、同じユーザーの通常のプロセスは非昇格のtokenで動き、昇格したプロセスを開けない。
    UACが無効な環境と、承認モードを適用しない組み込みAdministrator（GitHub ActionsのWindows runner）では、
    通常のプロセスも昇格したtokenで動く。
    SSHのログオンのtokenの種類から通常のプロセスの昇格を判別できるかは確かめていないため、UACのポリシーから判定する。
    """
    if sys.platform != "win32" or not is_elevated():
        return False
    import winreg  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

    def policy(key: Any, name: str, default: int) -> int:
        try:
            return int(winreg.QueryValueEx(key, name)[0])
        except FileNotFoundError:
            return default

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _UAC_POLICY_KEY) as key:
        if not policy(key, "EnableLUA", 1):
            return False
        builtin_administrator = user_sid().endswith(_BUILTIN_ADMINISTRATOR_RID)
        return not builtin_administrator or bool(policy(key, "FilterAdministratorToken", 0))


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


@contextlib.contextmanager
def _directory_handle(path: pathlib.Path) -> Generator[Any]:
    """リンクとjunctionをたどらずにディレクトリ自身を開き、ACLの読み書きに使うハンドルを返す。

    判定から変更までを同じハンドルで行い、途中でjunctionへ差し替えられても差し替え先のACLを変えない。
    削除の共有を許さず、開いている間の改名と削除も拒む。
    """
    _, kernel = _api()
    handle = kernel.CreateFileW(
        _native(path),
        _READ_CONTROL | _WRITE_DAC,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise _error(_windows.get_last_error(), path)
    try:
        attributes = (wintypes.DWORD * 2)()
        if not kernel.GetFileInformationByHandleEx(handle, _FILE_ATTRIBUTE_TAG_INFO, attributes, ctypes.sizeof(attributes)):
            raise _error(_windows.get_last_error(), path)
        if attributes[0] & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise NotADirectoryError(None, "リンクまたはjunctionのためACLを変更しない", str(path))
        yield handle
    finally:
        kernel.CloseHandle(handle)


def _handle_dacl_sddl(handle: Any, path: pathlib.Path) -> str:
    advapi, kernel = _api()
    descriptor = ctypes.c_void_p()
    status = advapi.GetSecurityInfo(
        handle, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, None, None, ctypes.byref(descriptor)
    )
    if status:
        raise _error(status, path)
    try:
        return _descriptor_sddl(descriptor, path)
    finally:
        kernel.LocalFree(descriptor)


def _inherit(handle: Any, path: pathlib.Path) -> None:
    """明示ACEを除いて保護を外し、親から継承するACLへ戻す。配下の継承ACEはOSが計算し直す。"""
    advapi, _ = _api()
    empty = ctypes.create_string_buffer(_EMPTY_ACL_SIZE)
    if not advapi.InitializeAcl(empty, _EMPTY_ACL_SIZE, _ACL_REVISION):
        raise _error(_windows.get_last_error(), path)
    status = advapi.SetSecurityInfo(
        handle, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION | _UNPROTECTED_DACL_SECURITY_INFORMATION, None, None, empty, None
    )
    if status:
        raise _error(status, path)


def _entries(directory: pathlib.Path) -> list[tuple[pathlib.Path, bool]]:
    r"""リンクとjunctionを除いた直下の項目と、ディレクトリかどうかを返す。

    MAX_PATHを超える配下も列挙できるよう`\\?\`付きのパスで開く。OSの継承の再計算もリンク先へは及ばない。
    """
    with os.scandir(_native(directory)) as entries:
        return [
            (directory / entry.name, entry.is_dir(follow_symlinks=False))
            for entry in entries
            if not entry.is_symlink() and not entry.is_junction()
        ]


def _first_inaccessible(directory: pathlib.Path) -> str | None:
    """配下でACLを読めない項目を探し、最初の1件の説明を返す。

    非昇格のtokenで保護を外すと、OSはACLを書けない配下（昇格したプロセスが作成した項目）を更新しないまま成功を返す。
    更新されなかった項目は親の保護が外れた後の走査で見つけられなくなるため、配下を全て書き換えられる場合だけ戻す。
    """
    pending = [directory]
    while pending:
        current = pending.pop()
        try:
            entries = _entries(current)
        except FileNotFoundError:
            continue
        except OSError as error:
            return f"{current}: {error.strerror}"
        for path, is_directory in entries:
            try:
                dacl_sddl(path)
            except FileNotFoundError:
                continue
            except OSError as error:
                return f"{path}: {error.strerror}"
            if is_directory:
                pending.append(path)
    return None


def restore_inheritance(root: pathlib.Path | str) -> InheritanceRepair:
    """root配下（root自身を含む）で旧版のOWNER RIGHTSのACEを明示したディレクトリを、親から継承するACLへ戻す。

    配下のいずれかのACLを実行中のtokenで読めないディレクトリは変更せず、理由とともに`blocked`へ返す。
    リンクとjunctionはたどらない。何度実行しても、戻せる範囲を戻した同じ状態に収束する。
    Windows以外では何もしない。
    """
    if os.name != "nt":
        return InheritanceRepair()
    restored: list[pathlib.Path] = []
    blocked: list[tuple[pathlib.Path, str]] = []
    pending = [pathlib.Path(os.path.abspath(root))]
    while pending:
        directory = pending.pop()
        try:
            # 走査中の大半を占める対象外のディレクトリは、ACLを読むだけで判定する。
            if _LEGACY_OWNER_RIGHTS_ACE in dacl_sddl(directory):
                with _directory_handle(directory) as handle:
                    if _LEGACY_OWNER_RIGHTS_ACE in _handle_dacl_sddl(handle, directory):
                        reason = _first_inaccessible(directory)
                        if reason is not None:
                            blocked.append((directory, reason))
                            continue
                        _inherit(handle, directory)
                        restored.append(directory)
            pending.extend(path for path, is_directory in _entries(directory) if is_directory)
        except FileNotFoundError:
            continue
        except OSError as error:
            blocked.append((directory, error.strerror or str(error)))
    return InheritanceRepair(restored=tuple(restored), blocked=tuple(blocked))
