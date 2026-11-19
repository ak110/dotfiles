r"""Windowsのtokenの昇格の判定と、同じユーザーの通常のプロセスが読めないACLから継承するACLへの復元。

旧版はWindowsでも`mkdir(mode=0o700)`と`tempfile.mkdtemp`で状態ディレクトリを作成し、
`D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)`の保護付きDACLを付けた（`private_directory`の説明を参照）。
配下へ昇格したプロセスが書いたファイルは所有者が`BUILTIN\Administrators`になり、同じユーザーの非昇格のプロセスが読めない。
手作業の復旧などで、SYSTEMとAdministratorsだけを許す保護付きのACLになった項目も同じく読めない。
`restore_inheritance`は、それらの項目を親から継承するACLへ戻し、配下へ継承をやり直させる。
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import ctypes
import dataclasses
import enum
import errno
import functools
import os
import pathlib
import sys
from collections.abc import Callable, Generator
from ctypes import wintypes
from typing import Any, NamedTuple

# Windows専用属性はLinuxの型情報に無いため、OS境界内で動的に解決する。
_windows: Any = ctypes

_LEGACY_OWNER_RIGHTS_ACE = "(A;OICI;FA;;;OW)"
"""旧版が保護付きDACLへ明示したOWNER RIGHTS（S-1-3-4）のACE。

このACEを持つディレクトリは、配下の項目の読取を所有者に依存させ、昇格したプロセスが書いた配下を非昇格のプロセスから閉ざす。
対象は保護付きDACL（`_PROTECTED_DACL`）のディレクトリに限り、このACEを継承した配下は対象にしない。
root内の継承は保護付きのディレクトリを戻すとOSが計算し直し、root外の親から継承したACEはroot内の操作では変わらず、
毎回の走査で全配下を対象と誤認するためである。
OSが記録したACLでは継承したACEにも`ID`が付かない場合があり（2026-11-19にsthenoで観測）、
`ID`の有無では継承したACEと区別できない。
"""
_PROTECTED_DACL = "D:P"
_ADMINISTRATORS = "S-1-5-32-544"
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x1
_GROUP_SECURITY_INFORMATION = 0x2
_DACL_SECURITY_INFORMATION = 0x4
_ACCESS_CHECK_INFORMATION = _OWNER_SECURITY_INFORMATION | _GROUP_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION
"""`AccessCheck`は所有者とグループを含むセキュリティ記述子を要する。"""
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
_SDDL_REVISION_1 = 1
_ACL_REVISION = 2
_EMPTY_ACL_SIZE = 8
_TOKEN_QUERY = 0x0008
_TOKEN_DUPLICATE = 0x0002
_SECURITY_IMPERSONATION = 2
_TOKEN_USER = 1
_TOKEN_ELEVATION = 20
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_FILE_GENERIC_READ = 0x00120089
_FILE_GENERIC_WRITE = 0x00120116
_FILE_GENERIC_EXECUTE = 0x001200A0
_FILE_ALL_ACCESS = 0x001F01FF
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_NO_TOKEN = 1008
_FILE_READ_ATTRIBUTES = 0x0080
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
_FILE_OPEN_REPARSE_POINT = 0x00200000
_OBJ_CASE_INSENSITIVE = 0x40
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_ATTRIBUTE_TAG_INFO = 9
_FILE_STANDARD_INFO = 1
_UAC_POLICY_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
_BUILTIN_ADMINISTRATOR_RID = "-500"
_SCAN_WORKERS = 8
"""ACLを読むスレッドの数。

sthenoの約12万項目の判定は、1スレッドの10.8秒が8スレッドで3.7秒になった（4スレッドで3.9秒、16スレッドで4.1秒）。
残りは項目ごとのPythonの処理がGILを取り合う時間である。
"""


class _GenericMapping(ctypes.Structure):
    _fields_ = [
        ("read", wintypes.DWORD),
        ("write", wintypes.DWORD),
        ("execute", wintypes.DWORD),
        ("all", wintypes.DWORD),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]


class _UnicodeString(ctypes.Structure):
    _fields_ = [("length", wintypes.USHORT), ("maximum_length", wintypes.USHORT), ("buffer", wintypes.LPWSTR)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.ULONG),
        ("root_directory", wintypes.HANDLE),
        ("object_name", ctypes.POINTER(_UnicodeString)),
        ("attributes", wintypes.ULONG),
        ("security_descriptor", ctypes.c_void_p),
        ("security_quality_of_service", ctypes.c_void_p),
    ]


class _IoStatusBlock(ctypes.Structure):
    _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_void_p)]


class _FileStandardInfo(ctypes.Structure):
    _fields_ = [
        ("allocation_size", ctypes.c_longlong),
        ("end_of_file", ctypes.c_longlong),
        ("number_of_links", wintypes.DWORD),
        ("delete_pending", wintypes.BOOLEAN),
        ("directory", wintypes.BOOLEAN),
    ]


_FILE_MAPPING = _GenericMapping(_FILE_GENERIC_READ, _FILE_GENERIC_WRITE, _FILE_GENERIC_EXECUTE, _FILE_ALL_ACCESS)


@dataclasses.dataclass(frozen=True)
class InheritanceRepair:
    """`restore_inheritance`の結果。"""

    restored: tuple[pathlib.Path, ...] = ()
    """継承するACLへ戻した項目。"""
    blocked: tuple[tuple[pathlib.Path, str], ...] = ()
    """継承するACLへ戻せない項目、戻しても通常のプロセスが読めない項目、一覧を取れないディレクトリと、その理由。"""


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
        "GetFileSecurityW": (
            [wintypes.LPCWSTR, wintypes.DWORD, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        ),
        "GetKernelObjectSecurity": (
            [wintypes.HANDLE, wintypes.DWORD, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        ),
        "AccessCheck": (
            [pointer, wintypes.HANDLE, wintypes.DWORD, pointer, pointer]
            + [ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.BOOL)],
            wintypes.BOOL,
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
        "OpenThreadToken": (
            [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)],
            wintypes.BOOL,
        ),
        "CreateRestrictedToken": (
            [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, pointer, wintypes.DWORD, pointer, wintypes.DWORD, pointer]
            + [ctypes.POINTER(wintypes.HANDLE)],
            wintypes.BOOL,
        ),
        "DuplicateToken": ([wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL),
        "GetTokenInformation": (
            [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        ),
        "ConvertSidToStringSidW": ([pointer, ctypes.POINTER(wintypes.LPWSTR)], wintypes.BOOL),
        "ConvertStringSidToSidW": ([wintypes.LPCWSTR, ctypes.POINTER(pointer)], wintypes.BOOL),
    }
    kernel_signatures = {
        "GetFileInformationByHandleEx": ([wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD], wintypes.BOOL),
        "GetFinalPathNameByHandleW": ([wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD], wintypes.DWORD),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "GetCurrentThread": ([], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "LocalFree": ([pointer], pointer),
    }
    for library, signatures in ((advapi, advapi_signatures), (kernel, kernel_signatures)):
        for name, (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
    return advapi, kernel


@functools.cache
def _ntdll() -> Any:
    ntdll = _windows.WinDLL("ntdll")
    ntdll.NtOpenFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    ntdll.NtOpenFile.restype = ctypes.c_long
    ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
    return ntdll


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


def _read_descriptor(read: Callable[[Any, int, Any], Any], path: str) -> bytes:
    """所有者・グループ・DACLを含む自己相対形式のセキュリティ記述子を読む。

    OSが記録した形をそのまま返す。親が自動継承の印（`AI`）を持たないと、継承したACEにも`ID`が付かない。
    """
    size = 512
    while True:
        buffer = ctypes.create_string_buffer(size)
        needed = wintypes.DWORD(0)
        if read(buffer, size, ctypes.byref(needed)):
            return ctypes.string_at(buffer, needed.value)
        code = _windows.get_last_error()
        if code != _ERROR_INSUFFICIENT_BUFFER:
            raise _error(code, path)
        size = needed.value


def _path_descriptor(native: str) -> bytes:
    advapi, _ = _api()
    return _read_descriptor(
        lambda buffer, size, needed: advapi.GetFileSecurityW(native, _ACCESS_CHECK_INFORMATION, buffer, size, needed), native
    )


def _handle_descriptor(handle: Any, native: str) -> bytes:
    advapi, _ = _api()
    return _read_descriptor(
        lambda buffer, size, needed: advapi.GetKernelObjectSecurity(handle, _ACCESS_CHECK_INFORMATION, buffer, size, needed),
        native,
    )


def dacl_sddl(path: pathlib.Path | str) -> str:
    """対象のDACLをSDDLで返す。READ_CONTROLを持たないtokenでは`PermissionError`を送出する。

    継承したACEには、OSの記録に無くても親と比べて`ID`を補う。
    """
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
def _user_token() -> Generator[Any]:
    """同じユーザーの通常のプロセスとして`AccessCheck`へ渡すtokenを返す。

    偽装中のスレッドでは偽装したtokenを基にする。
    UACで通常のプロセスが非昇格で動く環境の昇格した実行では、Administratorsを拒否専用にして非昇格のtokenに近づける。
    整合性レベルとAdministrators以外の管理者向けのグループは昇格したままだが、読取の判定に使うACLがそれらを名指しすることは無い。
    """
    advapi, kernel = _api()
    access = _TOKEN_QUERY | _TOKEN_DUPLICATE
    tokens: list[Any] = []
    try:
        source = wintypes.HANDLE()
        if not advapi.OpenThreadToken(kernel.GetCurrentThread(), access, True, ctypes.byref(source)):
            code = _windows.get_last_error()
            if code != _ERROR_NO_TOKEN:
                raise _windows.WinError(code)
            if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), access, ctypes.byref(source)):
                raise _windows.WinError(_windows.get_last_error())
        tokens.append(source)
        if is_elevated_above_user_processes():
            administrators = ctypes.c_void_p()
            if not advapi.ConvertStringSidToSidW(_ADMINISTRATORS, ctypes.byref(administrators)):
                raise _windows.WinError(_windows.get_last_error())
            try:
                disabled = _SidAndAttributes(administrators, 0)
                restricted = wintypes.HANDLE()
                if not advapi.CreateRestrictedToken(
                    source, 0, 1, ctypes.byref(disabled), 0, None, 0, None, ctypes.byref(restricted)
                ):
                    raise _windows.WinError(_windows.get_last_error())
            finally:
                kernel.LocalFree(administrators)
            tokens.append(restricted)
            source = restricted
        user = wintypes.HANDLE()
        if not advapi.DuplicateToken(source, _SECURITY_IMPERSONATION, ctypes.byref(user)):
            raise _windows.WinError(_windows.get_last_error())
        tokens.append(user)
        yield user
    finally:
        for token in tokens:
            kernel.CloseHandle(token)


class _Verdict(enum.Enum):
    KEEP = enum.auto()
    REPAIR = enum.auto()
    MISSING = enum.auto()


class _UserAccess:
    """同じユーザーの通常のプロセスから見て、項目を継承するACLへ戻す必要があるかを判定する。

    判定はセキュリティ記述子と項目の種類だけで決まるため、同じ記述子は1回だけ判定する。
    """

    def __init__(self, token: Any) -> None:
        self._token = token
        self._verdicts: dict[tuple[bytes, bool], bool] = {}

    def needs_repair(self, descriptor: bytes, is_directory: bool, native: str) -> bool:
        key = (descriptor, is_directory)
        verdict = self._verdicts.get(key)
        if verdict is None:
            verdict = self._verdicts[key] = self._legacy(descriptor, is_directory, native) or not self._readable(descriptor)
        return verdict

    @staticmethod
    def _legacy(descriptor: bytes, is_directory: bool, native: str) -> bool:
        if not is_directory:
            return False
        sddl = _descriptor_sddl(descriptor, native)
        return sddl.startswith(_PROTECTED_DACL) and _LEGACY_OWNER_RIGHTS_ACE in sddl

    def _readable(self, descriptor: bytes) -> bool:
        advapi, _ = _api()
        privileges = ctypes.create_string_buffer(256)
        length = wintypes.DWORD(len(privileges))
        granted = wintypes.DWORD(0)
        status = wintypes.BOOL(0)
        if not advapi.AccessCheck(
            descriptor,
            self._token,
            _FILE_GENERIC_READ,
            ctypes.byref(_FILE_MAPPING),
            privileges,
            ctypes.byref(length),
            ctypes.byref(granted),
            ctypes.byref(status),
        ):
            raise _windows.WinError(_windows.get_last_error())
        return bool(status.value)

    def inspect(self, native: str, is_directory: bool) -> _Verdict:
        try:
            descriptor = _path_descriptor(native)
        except FileNotFoundError:
            return _Verdict.MISSING
        except OSError:
            # 実行中のtokenで記述子を読めなければ通常のプロセスも読めない。戻せるかは変更するハンドルで確かめる。
            return _Verdict.REPAIR
        return _Verdict.REPAIR if self.needs_repair(descriptor, is_directory, native) else _Verdict.KEEP


class _Entry(NamedTuple):
    native: str
    """`_native`で作った走査用のパス。走査の大半を占める判定では`pathlib.Path`を作らない。"""
    is_directory: bool
    verdict: _Verdict

    @property
    def path(self) -> pathlib.Path:
        return pathlib.Path(self.native.removeprefix("\\\\?\\"))


def _open_for_acl(native: str) -> Any:
    """項目自身をリンクとjunctionをたどらずに開き、READ_CONTROL・WRITE_DAC・FILE_READ_ATTRIBUTESだけを持つハンドルを返す。

    `CreateFileW`は要求へSYNCHRONIZEを加えるため（`FILE_FLAG_OVERLAPPED`を付けても同じ）、
    所有者の暗黙の権限（READ_CONTROLとWRITE_DAC）だけを持つ非昇格のプロセスは開けない。
    FILE_READ_ATTRIBUTESは、項目のACLが許可しなくても親ディレクトリを列挙できる呼出元に許可された（WindowsのCIで観測）。
    """
    path = "\\??\\" + (native[4:] if native.startswith("\\\\?\\") else "UNC" + native[1:])
    buffer = ctypes.create_unicode_buffer(path)
    size = len(path.encode("utf-16-le"))
    name = _UnicodeString(size, size + ctypes.sizeof(ctypes.c_wchar), ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = _ObjectAttributes(
        ctypes.sizeof(_ObjectAttributes), None, ctypes.pointer(name), _OBJ_CASE_INSENSITIVE, None, None
    )
    handle = wintypes.HANDLE()
    status = _ntdll().NtOpenFile(
        ctypes.byref(handle),
        _READ_CONTROL | _WRITE_DAC | _FILE_READ_ATTRIBUTES,
        ctypes.byref(attributes),
        ctypes.byref(_IoStatusBlock()),
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        _FILE_OPEN_FOR_BACKUP_INTENT | _FILE_OPEN_REPARSE_POINT,
    )
    if status < 0:
        raise _error(_ntdll().RtlNtStatusToDosError(status), native)
    return handle.value


@contextlib.contextmanager
def _acl_handle(native: str) -> Generator[Any]:
    """リンクとjunctionをたどらずに項目自身を開き、ACLの読み書きに使うハンドルを返す。

    判定から変更までを同じハンドルで行い、途中でjunctionへ差し替えられても差し替え先のACLを変えない。
    非昇格のプロセスがパスの途中へ置いたjunctionやハードリンクの先にある、root外の項目を昇格した実行が変えないよう、
    ハンドルの最終パスが走査したパスと一致し、ほかのハードリンクを持たない項目だけを返す。
    """
    _, kernel = _api()
    handle = _open_for_acl(native)
    try:
        attributes = (wintypes.DWORD * 2)()
        if not kernel.GetFileInformationByHandleEx(handle, _FILE_ATTRIBUTE_TAG_INFO, attributes, ctypes.sizeof(attributes)):
            raise _error(_windows.get_last_error(), native)
        if attributes[0] & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise NotADirectoryError(None, "リンクまたはjunctionのためACLを変更しない", native)
        if _comparable(_final_path(handle, native)) != _comparable(native):
            raise OSError(errno.EXDEV, "パスの途中がリンクまたはjunctionのためACLを変更しない", native)
        standard = _FileStandardInfo()
        if not kernel.GetFileInformationByHandleEx(
            handle, _FILE_STANDARD_INFO, ctypes.byref(standard), ctypes.sizeof(standard)
        ):
            raise _error(_windows.get_last_error(), native)
        if not standard.directory and standard.number_of_links > 1:
            raise OSError(errno.EMLINK, "ほかの場所のハードリンクと実体を共有するためACLを変更しない", native)
        yield handle
    finally:
        kernel.CloseHandle(handle)


def _final_path(handle: Any, native: str) -> str:
    _, kernel = _api()
    size = 512
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        length = kernel.GetFinalPathNameByHandleW(handle, buffer, size, 0)
        if not length:
            raise _error(_windows.get_last_error(), native)
        if length < size:
            return buffer.value
        size = length + 1


def _comparable(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return os.path.normcase("\\\\" + path[8:])
    return os.path.normcase(path.removeprefix("\\\\?\\"))


def _inherit(handle: Any, native: str) -> None:
    """明示ACEを除いて保護を外し、親から継承するACLへ戻す。配下の継承ACEはOSが計算し直す。"""
    advapi, _ = _api()
    empty = ctypes.create_string_buffer(_EMPTY_ACL_SIZE)
    if not advapi.InitializeAcl(empty, _EMPTY_ACL_SIZE, _ACL_REVISION):
        raise _error(_windows.get_last_error(), native)
    status = advapi.SetSecurityInfo(
        handle, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION | _UNPROTECTED_DACL_SECURITY_INFORMATION, None, None, empty, None
    )
    if status:
        raise _error(status, native)


def _repair(entry: _Entry, access: _UserAccess) -> bool:
    """項目を継承するACLへ戻して真を返す。判定の後に戻す必要が無くなっていれば偽を返す。

    実行中のtokenで変更できない場合と、戻しても通常のプロセスが読めない場合は`OSError`を送出する。
    """
    with _acl_handle(entry.native) as handle:
        if not access.needs_repair(_handle_descriptor(handle, entry.native), entry.is_directory, entry.native):
            return False
        _inherit(handle, entry.native)
        if access.needs_repair(_handle_descriptor(handle, entry.native), entry.is_directory, entry.native):
            raise PermissionError(errno.EACCES, "継承するACLへ戻しても同じユーザーの通常のプロセスが読めない", entry.native)
    return True


def _entries(native: str) -> list[tuple[str, bool]]:
    """リンクとjunctionを除いた直下の項目と、ディレクトリかどうかを返す。OSの継承の再計算もリンク先へは及ばない。"""
    with os.scandir(native) as entries:
        return [
            (entry.path, entry.is_dir(follow_symlinks=False))
            for entry in entries
            if not entry.is_symlink() and not entry.is_junction()
        ]


def _inspect_directory(native: str, access: _UserAccess) -> list[_Entry] | OSError:
    try:
        entries = _entries(native)
    except FileNotFoundError:
        return []
    except OSError as error:
        return error
    return [_Entry(path, is_directory, access.inspect(path, is_directory)) for path, is_directory in entries]


def restore_inheritance(root: pathlib.Path | str) -> InheritanceRepair:
    """root配下（root自身を含む）の、同じユーザーの通常のプロセスが読めない項目を継承するACLへ戻す。

    旧版のOWNER RIGHTSのACEを明示したディレクトリも、配下の読取を所有者に依存させるため戻す。
    通常のプロセスは、非昇格の実行では実行中のtokenで、UACで通常のプロセスが非昇格で動く環境の昇格した実行では
    Administratorsを拒否専用にしたtokenで表す。
    所有者は暗黙にDACLを読み書きできるため、所有者がユーザーの項目は非昇格の実行でも戻せる。
    実行中のtokenで戻せない項目（非昇格の実行での所有者がAdministratorsの項目など）は変更せず、
    理由とともに`blocked`へ返して配下へは進まない。
    戻しても通常のプロセスが読めない項目（親から継承するACEがユーザーに許可しない場合）は、戻したうえで`blocked`へ返す。
    各階層のACLを並行して読み、変更は親から順に呼出元のスレッドで行う。
    リンクとjunctionはたどらない。何度実行しても、戻せる範囲を戻した同じ状態に収束する。
    Windows以外では何もしない。
    """
    if os.name != "nt":
        return InheritanceRepair()
    restored: list[pathlib.Path] = []
    blocked: list[tuple[pathlib.Path, str]] = []
    with _user_token() as token, concurrent.futures.ThreadPoolExecutor(_SCAN_WORKERS) as pool:
        access = _UserAccess(token)

        def settle(entry: _Entry) -> bool:
            """必要なら継承するACLへ戻し、配下へ進むかを返す。"""
            if entry.verdict is _Verdict.MISSING:
                return False
            if entry.verdict is _Verdict.REPAIR:
                try:
                    if _repair(entry, access):
                        restored.append(entry.path)
                except FileNotFoundError:
                    return False
                except OSError as error:
                    blocked.append((entry.path, error.strerror or str(error)))
                    return False
            return entry.is_directory

        # root自体のリンクは解決し、その配下で走査したパスと変更するハンドルの最終パスを比べる。
        start = _native(os.path.realpath(root))
        entries = [_Entry(start, True, access.inspect(start, True))]
        while entries:
            directories = [entry for entry in entries if settle(entry)]
            entries = []
            listings = pool.map(functools.partial(_inspect_directory, access=access), [entry.native for entry in directories])
            for directory, listing in zip(directories, listings, strict=True):
                if isinstance(listing, OSError):
                    blocked.append((directory.path, listing.strerror or str(listing)))
                else:
                    entries.extend(listing)
    return InheritanceRepair(restored=tuple(restored), blocked=tuple(blocked))
