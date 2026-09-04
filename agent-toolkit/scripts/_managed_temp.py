#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""agent-toolkitが所有する一時ディレクトリを作成・検証・後始末する。"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime
import enum
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import sys
import tempfile
import typing
import unicodedata
from ctypes import wintypes

import _atk_help

_MARKER_NAME = ".agent-toolkit-managed-temp.json"
_SCHEMA_VERSION = 4
_PREFIX_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_PREFIX_RULES = (
    ("空にできない", lambda value: value != ""),
    (
        "英小文字・数字・ハイフンだけを\u4f7fえる",
        lambda value: re.fullmatch(r"[a-z0-9-]+", value) is not None,
    ),
    ("先頭と末尾をハイフンにできない", lambda value: not value.startswith("-") and not value.endswith("-")),
    ("ハイフンを連続させられない", lambda value: "--" not in value),
)
"""prefixの受理条件と、条件ごとの説明文。`_PREFIX_RE`と同じ規則を条件単位で表す。"""
_UTC_ISO8601_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00\Z")
MAX_AGE_DAYS = 7
"""管理対象一時領域を自動削除するまでの日数。最終更新日時からの経過で判定する。"""
_WINDOWS_ACCESS_ALLOWED_ACE_TYPE = 0
_WINDOWS_ACCESS_DENIED_ACE_TYPE = 1
_WINDOWS_ACL_REVISION = 2
_WINDOWS_ERROR_ACCESS_DENIED = 5
_WINDOWS_CONTAINER_INHERIT_ACE = 0x02
_WINDOWS_DACL_SECURITY_INFORMATION = 0x00000004
_WINDOWS_EXTERNAL_WRITER_ACCESS = 0x001301BF
_WINDOWS_FILE_ALL_ACCESS = 0x001F01FF
_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x10
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
_WINDOWS_OBJECT_INHERIT_ACE = 0x01
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_OWNER_SECURITY_INFORMATION = 0x00000001
_WINDOWS_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_WINDOWS_READ_ATTRIBUTES = 0x0080
_WINDOWS_READ_CONTROL = 0x00020000
_WINDOWS_REPARSE_POINT = 0x400
_WINDOWS_SE_DACL_PROTECTED = 0x1000
_WINDOWS_SE_FILE_OBJECT = 1
_WINDOWS_SYNCHRONIZE = 0x00100000
_WINDOWS_WRITE_DAC = 0x00040000
_WINDOWS_WRITE_OWNER = 0x00080000


class ManagedTempError(Exception):
    """ユーザーが入力または実行環境を修正できる検証エラー。"""


class _ManagedTempEntry(typing.TypedDict):
    """真正性検証済みの管理対象一時領域を列挙する公開項目。"""

    path: str
    prefix: str | None
    created_at: str | None
    awis: list[str]


class _WindowsApiError(ManagedTempError):
    """Windows APIのerror codeを保持する検証エラー。"""

    def __init__(self, action: str, path: pathlib.Path | None, error_code: int) -> None:
        target = f": {path}" if path is not None else ""
        super().__init__(f"{action}{target}: {error_code}")
        self.error_code = error_code


class _WindowsHandleOpenError(_WindowsApiError):
    """Windowsのパスハンドルを開けなかったことを示す。"""


class _ValidatedTemp(typing.NamedTuple):
    path: pathlib.Path
    device: int
    inode: int
    nonce: str
    registry_path: pathlib.Path
    record: dict[str, typing.Any]
    root_device: int
    root_inode: int
    root_owner: int | None
    root_mode: int | None
    root_security: typing.Any = None


class _ValidatedRoot(typing.NamedTuple):
    device: int
    inode: int
    owner: int | None
    mode: int | None
    security: typing.Any = None


class _AceHeader(ctypes.Structure):
    _fields_ = [("ace_type", ctypes.c_uint8), ("ace_flags", ctypes.c_uint8), ("ace_size", ctypes.c_uint16)]


class _AccessAllowedAce(ctypes.Structure):
    _fields_ = [("header", _AceHeader), ("mask", ctypes.c_uint32), ("sid_start", ctypes.c_uint32)]


class _Acl(ctypes.Structure):
    _fields_ = [
        ("revision", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("ace_count", ctypes.c_uint16),
        ("reserved2", ctypes.c_uint16),
    ]


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [("ace_count", ctypes.c_uint32), ("bytes_in_use", ctypes.c_uint32), ("bytes_free", ctypes.c_uint32)]


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", ctypes.c_uint32),
        ("creation_time", _FileTime),
        ("access_time", _FileTime),
        ("write_time", _FileTime),
        ("volume", ctypes.c_uint32),
        ("size_high", ctypes.c_uint32),
        ("size_low", ctypes.c_uint32),
        ("links", ctypes.c_uint32),
        ("file_index_high", ctypes.c_uint32),
        ("file_index_low", ctypes.c_uint32),
    ]


class _WindowsAce(typing.NamedTuple):
    ace_type: int
    flags: int
    mask: int
    sid: bytes | None


class _WindowsSecurity(typing.NamedTuple):
    owner: bytes
    dacl_present: bool
    protected: bool
    directory: bool
    aces: tuple[_WindowsAce, ...]


def _windows_dll(name: str) -> typing.Any:
    """Windows専用DLLを遅延取得する。"""
    return typing.cast(typing.Any, ctypes).WinDLL(name, use_last_error=True)


def _windows_error(action: str, path: pathlib.Path | None = None) -> _WindowsApiError:
    """最後のWindows error codeを含む検証エラーを作成する。"""
    error_code = typing.cast(typing.Any, ctypes).get_last_error()
    return _WindowsApiError(action, path, error_code)


def _windows_handle_open_error(action: str, path: pathlib.Path) -> _WindowsHandleOpenError:
    """最後のWindowsエラーコードを含むハンドル取得エラーを作成する。"""
    error_code = typing.cast(typing.Any, ctypes).get_last_error()
    return _WindowsHandleOpenError(action, path, error_code)


def _windows_sid_bytes(sid_text: str) -> bytes:
    """文字列表現のSIDをWindows APIが扱うbinary SIDへ変換する。"""
    advapi32 = _windows_dll("advapi32")
    kernel32 = _windows_dll("kernel32")
    advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi32.GetLengthSid.restype = wintypes.DWORD
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    sid = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(sid_text, ctypes.byref(sid)):
        raise _windows_error("Windows SIDを変換できない")
    try:
        length = advapi32.GetLengthSid(sid)
        if length == 0:
            raise _windows_error("Windows SIDの長さを取得できない")
        return ctypes.string_at(sid, length)
    finally:
        kernel32.LocalFree(sid)


def _windows_equal_sids(first: bytes, second: bytes) -> bool:
    """2つのbinary SIDをWindowsのSID比較規則で比較する。"""
    advapi32 = _windows_dll("advapi32")
    advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    advapi32.EqualSid.restype = wintypes.BOOL
    first_buffer = ctypes.create_string_buffer(first)
    second_buffer = ctypes.create_string_buffer(second)
    return bool(advapi32.EqualSid(first_buffer, second_buffer))


@contextlib.contextmanager
def _windows_path_handle(
    path: pathlib.Path,
    access: int,
) -> typing.Iterator[tuple[int, _ByHandleFileInformation]]:
    """再解析ポイントを追跡しないパスハンドルと属性を返す。"""
    kernel32 = _windows_dll("kernel32")
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    effective_access = access | _WINDOWS_READ_ATTRIBUTES | _WINDOWS_SYNCHRONIZE
    handle = create_file(
        str(path),
        effective_access,
        _WINDOWS_FILE_SHARE_ALL,
        None,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise _windows_handle_open_error("Windowsのパスハンドルを取得できない", path)
    try:
        information = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
            raise _windows_error("Windows path属性を取得できない", path)
        if information.attributes & _WINDOWS_REPARSE_POINT:
            raise ManagedTempError(f"Windows reparse pointは管理対象にできない: {path}")
        yield handle, information
    finally:
        kernel32.CloseHandle(handle)


@contextlib.contextmanager
def _windows_security_update_handle(
    path: pathlib.Path,
) -> typing.Iterator[tuple[int, _ByHandleFileInformation, bool]]:
    """所有者更新可否を判定した単一のセキュリティ更新用ハンドルを返す。"""
    full_access = _WINDOWS_READ_CONTROL | _WINDOWS_WRITE_DAC | _WINDOWS_WRITE_OWNER
    minimal_access = _WINDOWS_READ_CONTROL | _WINDOWS_WRITE_DAC
    with contextlib.ExitStack() as stack:
        try:
            handle, information = stack.enter_context(_windows_path_handle(path, full_access))
            can_write_owner = True
        except _WindowsHandleOpenError as error:
            if error.error_code != _WINDOWS_ERROR_ACCESS_DENIED:
                raise
            handle, information = stack.enter_context(_windows_path_handle(path, minimal_access))
            can_write_owner = False
        yield handle, information, can_write_owner


def _windows_current_sid() -> str:
    """現在のWindows process tokenのuser SIDを文字列で返す。"""

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

    class _TokenUser(ctypes.Structure):
        _fields_ = [("user", _SidAndAttributes)]

    advapi32 = _windows_dll("advapi32")
    kernel32 = _windows_dll("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    token = wintypes.HANDLE(None)
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ManagedTempError("Windows process tokenを取得できない")
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ManagedTempError("Windows user SIDを取得できない")
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        string_sid = ctypes.c_wchar_p(None)
        if not advapi32.ConvertSidToStringSidW(token_user.user.sid, ctypes.byref(string_sid)):
            raise ManagedTempError("Windows user SIDを文字列化できない")
        try:
            if string_sid.value is None:
                raise ManagedTempError("Windows user SIDが空である")
            return string_sid.value
        finally:
            kernel32.LocalFree(ctypes.cast(string_sid, ctypes.c_void_p))
    finally:
        kernel32.CloseHandle(token)


def _windows_information_identity(information: _ByHandleFileInformation) -> tuple[int, int]:
    """取得済みのWindowsハンドル情報からボリュームとファイルのIDを返す。"""
    return information.volume, (information.file_index_high << 32) | information.file_index_low


def _windows_secure_path(
    path: pathlib.Path,
    *,
    directory: bool,
    expected_identity: tuple[int, int] | None = None,
) -> None:
    """OwnerとDACLを現在user SIDだけの保護ACLへ完全置換する。"""
    current_sid = _windows_sid_bytes(_windows_current_sid())
    flags = _WINDOWS_OBJECT_INHERIT_ACE | _WINDOWS_CONTAINER_INHERIT_ACE if directory else 0
    ace = _WindowsAce(_WINDOWS_ACCESS_ALLOWED_ACE_TYPE, flags, _WINDOWS_FILE_ALL_ACCESS, current_sid)
    _windows_replace_security(
        path,
        current_sid,
        (ace,),
        directory=directory,
        expected_identity=expected_identity,
    )


def _windows_replace_security(
    path: pathlib.Path,
    owner_sid: bytes,
    aces: tuple[_WindowsAce, ...],
    *,
    directory: bool,
    expected_identity: tuple[int, int] | None = None,
) -> None:
    """Windows pathのDACLと、必要な場合はOwnerを指定値へ完全置換する。"""
    acl_buffer = _windows_acl_buffer(path, aces)
    with _windows_security_update_handle(path) as (handle, information, can_write_owner):
        actual_directory = bool(information.attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY)
        if actual_directory != directory:
            raise ManagedTempError(f"Windows pathの種別が指定と一致しない: {path}")
        if expected_identity is not None and _windows_information_identity(information) != expected_identity:
            raise ManagedTempError(f"管理対象がACL再保護時に置換された: {path}")
        current_owner = _windows_security_from_handle(handle, information, path).owner
        owner_changed = not _windows_equal_sids(current_owner, owner_sid)
        if owner_changed and not can_write_owner:
            raise ManagedTempError(f"Windowsの所有者を変更できるハンドルを取得できない: {path}")
        security_information = _WINDOWS_DACL_SECURITY_INFORMATION | _WINDOWS_PROTECTED_DACL_SECURITY_INFORMATION
        owner_to_set: bytes | None = None
        if owner_changed:
            security_information |= _WINDOWS_OWNER_SECURITY_INFORMATION
            owner_to_set = owner_sid
        _windows_set_security(handle, path, security_information, owner_to_set, acl_buffer)


def _windows_acl_buffer(path: pathlib.Path, aces: tuple[_WindowsAce, ...]) -> typing.Any:
    """指定ACEだけを含むWindows ACL bufferを作成する。"""
    advapi32 = _windows_dll("advapi32")
    advapi32.InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    advapi32.InitializeAcl.restype = wintypes.BOOL
    advapi32.AddAccessAllowedAceEx.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    advapi32.AddAccessAllowedAceEx.restype = wintypes.BOOL
    advapi32.AddAccessDeniedAceEx.argtypes = advapi32.AddAccessAllowedAceEx.argtypes
    advapi32.AddAccessDeniedAceEx.restype = wintypes.BOOL
    sid_lengths = [len(ace.sid) for ace in aces if ace.sid is not None]
    if len(sid_lengths) != len(aces):
        raise ManagedTempError(f"Windows DACLへSIDを持たないACEは設定できない: {path}")
    acl_size = ctypes.sizeof(_Acl) + sum(
        ctypes.sizeof(_AccessAllowedAce) - ctypes.sizeof(ctypes.c_uint32) + size for size in sid_lengths
    )
    acl_buffer = ctypes.create_string_buffer(acl_size)
    if not advapi32.InitializeAcl(acl_buffer, acl_size, _WINDOWS_ACL_REVISION):
        raise _windows_error("Windows DACLを初期化できない", path)
    for ace in aces:
        assert ace.sid is not None
        sid_buffer = ctypes.create_string_buffer(ace.sid)
        if ace.ace_type == _WINDOWS_ACCESS_ALLOWED_ACE_TYPE:
            add_ace = advapi32.AddAccessAllowedAceEx
        elif ace.ace_type == _WINDOWS_ACCESS_DENIED_ACE_TYPE:
            add_ace = advapi32.AddAccessDeniedAceEx
        else:
            raise ManagedTempError(f"Windows DACLへ未対応種別のACEは設定できない: {path}: {ace.ace_type}")
        if not add_ace(acl_buffer, _WINDOWS_ACL_REVISION, ace.flags, ace.mask, sid_buffer):
            raise _windows_error("Windows DACLへACEを追加できない", path)
    return acl_buffer


def _windows_set_security(
    handle: int,
    path: pathlib.Path,
    security_information: int,
    owner_sid: bytes | None,
    acl_buffer: typing.Any,
) -> None:
    """開いているハンドルへ所有者と`DACL`を設定する。"""
    advapi32 = _windows_dll("advapi32")
    advapi32.SetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetSecurityInfo.restype = wintypes.DWORD
    owner_buffer = ctypes.create_string_buffer(owner_sid) if owner_sid is not None else None
    result = advapi32.SetSecurityInfo(
        handle,
        _WINDOWS_SE_FILE_OBJECT,
        security_information,
        owner_buffer,
        None,
        acl_buffer,
        None,
    )
    if result != 0:
        raise _WindowsApiError("Windows ownerまたはDACLを設定できない", path, result)


def _windows_security_descriptor(path: pathlib.Path) -> _WindowsSecurity:
    """Windows pathのsecurity descriptorをraw SIDとACEへ分解して返す。"""
    with _windows_path_handle(path, _WINDOWS_READ_CONTROL) as (handle, information):
        return _windows_security_from_handle(handle, information, path)


def _windows_security_from_handle(
    handle: int,
    information: _ByHandleFileInformation,
    path: pathlib.Path,
) -> _WindowsSecurity:
    """開いているハンドルのセキュリティ記述子をraw SIDとACEへ分解して返す。"""
    advapi32 = _windows_dll("advapi32")
    kernel32 = _windows_dll("kernel32")
    advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.IsValidSid.argtypes = [ctypes.c_void_p]
    advapi32.IsValidSid.restype = wintypes.BOOL
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi32.GetLengthSid.restype = wintypes.DWORD
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = advapi32.GetSecurityInfo(
        handle,
        _WINDOWS_SE_FILE_OBJECT,
        _WINDOWS_OWNER_SECURITY_INFORMATION | _WINDOWS_DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0:
        raise ManagedTempError(f"Windows security descriptorを取得できない: {path}: {result}")
    try:
        if not owner or not advapi32.IsValidSid(owner):
            raise ManagedTempError(f"Windows owner SIDが不正: {path}")
        owner_sid = ctypes.string_at(owner, advapi32.GetLengthSid(owner))
        control = wintypes.WORD(0)
        revision = wintypes.DWORD(0)
        if not advapi32.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise _windows_error("Windows security descriptor controlを取得できない", path)
        aces: list[_WindowsAce] = []
        if dacl:
            acl_information = _AclSizeInformation()
            if not advapi32.GetAclInformation(dacl, ctypes.byref(acl_information), ctypes.sizeof(acl_information), 2):
                raise _windows_error("Windows DACL情報を取得できない", path)
            for index in range(acl_information.ace_count):
                ace_pointer = ctypes.c_void_p()
                if not advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                    raise _windows_error("Windows ACEを取得できない", path)
                ace_address = ace_pointer.value
                if ace_address is None:
                    raise ManagedTempError(f"Windows ACEのaddressが空である: {path}")
                header = ctypes.cast(ace_pointer, ctypes.POINTER(_AceHeader)).contents
                mask = 0
                sid: bytes | None = None
                if header.ace_size >= ctypes.sizeof(_AceHeader) + ctypes.sizeof(wintypes.DWORD):
                    mask = ctypes.c_uint32.from_address(ace_address + ctypes.sizeof(_AceHeader)).value
                if header.ace_type in (_WINDOWS_ACCESS_ALLOWED_ACE_TYPE, _WINDOWS_ACCESS_DENIED_ACE_TYPE):
                    sid_pointer = ctypes.c_void_p(ace_address + _AccessAllowedAce.sid_start.offset)
                    if not advapi32.IsValidSid(sid_pointer):
                        raise ManagedTempError(f"Windows ACEのSIDが不正: {path}")
                    sid_length = advapi32.GetLengthSid(sid_pointer)
                    if _AccessAllowedAce.sid_start.offset + sid_length > header.ace_size:
                        raise ManagedTempError(f"Windows ACEのSID長が不正: {path}")
                    sid = ctypes.string_at(sid_pointer, sid_length)
                aces.append(_WindowsAce(header.ace_type, header.ace_flags, mask, sid))
        return _WindowsSecurity(
            owner_sid,
            bool(dacl),
            bool(control.value & _WINDOWS_SE_DACL_PROTECTED),
            bool(information.attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY),
            tuple(aces),
        )
    finally:
        kernel32.LocalFree(descriptor)


def _windows_security_base_is_valid(security: _WindowsSecurity, current_sid: bytes) -> bool:
    """Ownerと保護DACLが現在利用者の管理下にあるか返す。"""
    return security.dacl_present and security.protected and _windows_equal_sids(security.owner, current_sid)


def _windows_current_user_ace_is_valid(ace: _WindowsAce, current_sid: bytes, expected_flags: int) -> bool:
    """ACEが現在利用者のFullControlを表すか返す。"""
    return (
        ace.ace_type == _WINDOWS_ACCESS_ALLOWED_ACE_TYPE
        and ace.flags == expected_flags
        and ace.mask == _WINDOWS_FILE_ALL_ACCESS
        and ace.sid is not None
        and _windows_equal_sids(ace.sid, current_sid)
    )


def _windows_external_writer_ace_is_valid(ace: _WindowsAce, current_sid: bytes, expected_flags: int) -> bool:
    """ACEが管理対象rootで実測した外部書込主体の権限形に一致するか返す。"""
    return (
        ace.ace_type == _WINDOWS_ACCESS_ALLOWED_ACE_TYPE
        and ace.flags == expected_flags
        and ace.mask == _WINDOWS_EXTERNAL_WRITER_ACCESS
        and ace.sid is not None
        and not _windows_equal_sids(ace.sid, current_sid)
    )


def _validate_windows_security(path: pathlib.Path) -> None:
    """内部真正性状態に現在利用者だけの厳格なACLを要求する。"""
    current_sid = _windows_sid_bytes(_windows_current_sid())
    security = _windows_security_descriptor(path)
    expected_flags = _WINDOWS_OBJECT_INHERIT_ACE | _WINDOWS_CONTAINER_INHERIT_ACE if security.directory else 0
    valid_ace = len(security.aces) == 1 and _windows_current_user_ace_is_valid(security.aces[0], current_sid, expected_flags)
    if not _windows_security_base_is_valid(security, current_sid) or not valid_ace:
        raise ManagedTempError(f"Windows pathのownerまたはACLが不正: {path}")


def _validate_windows_managed_root_security(path: pathlib.Path) -> None:
    """管理対象rootでは厳格ACLと実測済みの追加ACE 1件だけを受理する。"""
    current_sid = _windows_sid_bytes(_windows_current_sid())
    security = _windows_security_descriptor(path)
    if not _windows_managed_root_security_is_valid(security, current_sid):
        raise ManagedTempError(f"Windows pathのownerまたはACLが不正: {path}")


def _windows_managed_root_security_is_valid(security: _WindowsSecurity, current_sid: bytes) -> bool:
    """厳格ACLと実測済みの追加ACE 1件だけを受理する。"""
    expected_flags = _WINDOWS_OBJECT_INHERIT_ACE | _WINDOWS_CONTAINER_INHERIT_ACE
    current_user_aces = [ace for ace in security.aces if _windows_current_user_ace_is_valid(ace, current_sid, expected_flags)]
    other_aces = [ace for ace in security.aces if not _windows_current_user_ace_is_valid(ace, current_sid, expected_flags)]
    valid_operational_acl = len(current_user_aces) == 1 and (
        not other_aces
        or (len(other_aces) == 1 and _windows_external_writer_ace_is_valid(other_aces[0], current_sid, expected_flags))
    )
    return security.directory and _windows_security_base_is_valid(security, current_sid) and valid_operational_acl


def _windows_identity(path: pathlib.Path) -> tuple[int, int]:
    """Reparse pointを開かず、WindowsハンドルからボリュームとファイルのIDを返す。"""
    with _windows_path_handle(path, _WINDOWS_READ_ATTRIBUTES) as (_, information):
        return _windows_information_identity(information)


def _temp_root() -> pathlib.Path:
    try:
        root = pathlib.Path(tempfile.gettempdir()).resolve(strict=True)
    except OSError as error:
        raise ManagedTempError(f"一時ディレクトリのルートを解決できない: {error}") from error
    if not root.is_dir():
        raise ManagedTempError(f"一時ディレクトリのルートがディレクトリではない: {root}")
    if os.name == "nt" and getattr(root.lstat(), "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT:
        raise ManagedTempError(f"一時ディレクトリのルートがreparse pointである: {root}")
    return root


def _validate_root(
    root: pathlib.Path,
    *,
    explicit: bool = False,
    expected: _ValidatedRoot | None = None,
) -> _ValidatedRoot:
    """管理対象の親rootを検証し、操作中に比較するidentityと属性を返す。"""
    if not root.is_absolute():
        raise ManagedTempError(f"rootは絶対パスで指定する: {root}")
    if os.name == "posix":
        try:
            metadata = root.lstat()
        except OSError as error:
            raise ManagedTempError(f"管理対象rootを検証できない: {root}: {error}") from error
        mode = stat.S_IMODE(metadata.st_mode)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ManagedTempError(f"管理対象rootが通常ディレクトリではない: {root}")
        if mode & (stat.S_ISUID | stat.S_ISGID):
            raise ManagedTempError(f"管理対象rootの特殊権限が不正: {root}")
        if (mode & stat.S_IWUSR) == 0 or (mode & stat.S_IXUSR) == 0:
            raise ManagedTempError(f"管理対象rootの所有者権限が不正: {root}")
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            if explicit or mode != 0o1777:
                raise ManagedTempError(f"管理対象rootの権限が不安全: {root}")
        elif metadata.st_uid != os.geteuid():
            raise ManagedTempError(f"管理対象rootの所有者が現在の利用者ではない: {root}")
        current = _ValidatedRoot(metadata.st_dev, metadata.st_ino, metadata.st_uid, mode)
    elif os.name == "nt":
        try:
            metadata = root.lstat()
        except OSError as error:
            raise ManagedTempError(f"管理対象rootを検証できない: {root}: {error}") from error
        if not stat.S_ISDIR(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT:
            raise ManagedTempError(f"管理対象rootが通常ディレクトリではない: {root}")
        identity = _windows_identity(root)
        current_sid = _windows_sid_bytes(_windows_current_sid())
        security = _windows_security_descriptor(root)
        if not security.directory or not security.dacl_present:
            raise ManagedTempError(f"Windows pathのownerまたはACLが不正: {root}")
        if explicit and not _windows_managed_root_security_is_valid(security, current_sid):
            raise ManagedTempError(f"Windows pathのownerまたはACLが不正: {root}")
        after_identity = _windows_identity(root)
        after_security = _windows_security_descriptor(root)
        if after_identity != identity:
            raise ManagedTempError(f"管理対象rootが検証中に置換された: {root}")
        if after_security != security:
            raise ManagedTempError(f"管理対象rootが検証中に変更された: {root}")
        current = _ValidatedRoot(after_identity[0], after_identity[1], None, None, after_security)
    else:
        raise ManagedTempError(f"未対応platform: {os.name}")
    if expected is not None and current != expected:
        raise ManagedTempError(f"管理対象rootが検証中に置換または変更された: {root}")
    return current


def _owner_record() -> dict[str, str | int]:
    if os.name == "posix":
        return {"kind": "uid", "id": os.geteuid()}
    if os.name == "nt":
        return {"kind": "sid", "id": _windows_current_sid()}
    raise ManagedTempError(f"未対応platform: {os.name}")


def _state_root_path() -> pathlib.Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise ManagedTempError("LOCALAPPDATAが設定されていない")
        return pathlib.Path(base) / "agent-toolkit" / "managed-temp"
    base = os.environ.get("XDG_STATE_HOME")
    state_home = pathlib.Path(base) if base else pathlib.Path.home() / ".local" / "state"
    return state_home / "agent-toolkit" / "managed-temp"


def _state_root() -> pathlib.Path:
    root = _state_root_path()
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            metadata = root.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise ManagedTempError(f"外部状態ディレクトリの所有者または種別が不正: {root}")
            root.chmod(0o700)
            if stat.S_IMODE(root.stat().st_mode) != 0o700:
                raise ManagedTempError(f"外部状態ディレクトリの権限が不正: {root}")
        elif os.name == "nt":
            _windows_secure_path(root, directory=True)
            _validate_windows_security(root)
        else:
            raise ManagedTempError(f"未対応platform: {os.name}")
    except OSError as error:
        raise ManagedTempError(f"外部状態ディレクトリを準備できない: {root}: {error}") from error
    return root


def _registry_name(path: pathlib.Path) -> str:
    """管理対象pathに対応する登録ファイル名を返す。"""
    return f"{hashlib.sha256(os.fsencode(path)).hexdigest()}.json"


def _registry_path(path: pathlib.Path) -> pathlib.Path:
    return _state_root() / _registry_name(path)


def _write_private_json(path: pathlib.Path, value: dict[str, typing.Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n").encode()
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if os.name == "posix":
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if os.name == "nt":
            _windows_secure_path(path, directory=False)
    except OSError as error:
        raise ManagedTempError(f"外部状態を書き込めない: {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _load_private_json(path: pathlib.Path) -> dict[str, typing.Any]:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ManagedTempError(f"外部状態が通常ファイルではない: {path}")
        if os.name == "posix":
            if before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600:
                raise ManagedTempError(f"外部状態の所有者または権限が不正: {path}")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "r", encoding="utf-8") as source:
                opened = os.fstat(source.fileno())
                value = json.load(source)
            after = path.lstat()
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or (
                after.st_dev,
                after.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                raise ManagedTempError(f"外部状態が検証中に置換された: {path}")
        else:
            _validate_windows_security(path)
            identity = _windows_identity(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            if _windows_identity(path) != identity:
                raise ManagedTempError(f"外部状態が検証中に置換された: {path}")
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManagedTempError(f"外部状態を検証できない: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ManagedTempError(f"外部状態はJSON objectである必要がある: {path}")
    return value


def _path_identity(path: pathlib.Path) -> tuple[int, int]:
    if os.name == "nt":
        return _windows_identity(path)
    metadata = path.lstat()
    return metadata.st_dev, metadata.st_ino


def _record_base(
    path: pathlib.Path,
    nonce: str,
    *,
    identity: tuple[int, int] | None = None,
) -> dict[str, typing.Any]:
    device, inode = identity if identity is not None else _path_identity(path)
    return {
        "path": str(path),
        "platform": os.name,
        "owner": _owner_record(),
        "identity": [device, inode],
        "nonce": nonce,
    }


def _record(
    path: pathlib.Path,
    nonce: str,
    *,
    prefix: str,
    created_at: str,
    awis: tuple[str, ...],
    identity: tuple[int, int] | None = None,
) -> dict[str, typing.Any]:
    record = _record_base(path, nonce, identity=identity)
    record.update(
        {
            "schema_version": _SCHEMA_VERSION,
            "prefix": prefix,
            "created_at": created_at,
            "awis": list(awis),
        }
    )
    return record


def _is_utc_iso8601(value: object) -> bool:
    """valueがUTC offsetを持つISO 8601日時文字列か返す。"""
    if not isinstance(value, str) or _UTC_ISO8601_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == datetime.timedelta(0)


def _awis_are_valid(value: object) -> bool:
    """対応AWI名の記録形式が安全なファイル名のリストか返す。"""
    return isinstance(value, list) and all(
        isinstance(awi, str)
        and bool(awi)
        and "/" not in awi
        and "\\" not in awi
        and all(unicodedata.category(character) != "Cc" for character in awi)
        for awi in value
    )


def _records_match(
    path: pathlib.Path,
    marker: dict[str, typing.Any],
    registry: dict[str, typing.Any],
    *,
    identity: tuple[int, int] | None = None,
) -> bool:
    if identity is not None:
        try:
            if _path_identity(path) != identity:
                return False
        except (OSError, ManagedTempError):
            return False
    nonce = registry.get("nonce")
    schema_version = registry.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        return False
    if schema_version == 1:
        expected = _record_base(path, typing.cast(str, nonce), identity=identity)
        expected["schema_version"] = schema_version
    elif schema_version == 2:
        prefix = registry.get("prefix")
        created_at = registry.get("created_at")
        if not isinstance(prefix, str) or not is_valid_prefix(prefix) or not _is_utc_iso8601(created_at):
            return False
        expected = _record_base(path, typing.cast(str, nonce), identity=identity)
        expected.update(
            {
                "schema_version": schema_version,
                "prefix": prefix,
                "created_at": typing.cast(str, created_at),
            }
        )
    elif schema_version in (3, 4):
        prefix = registry.get("prefix")
        created_at = registry.get("created_at")
        # 版数3は改名前のキー名で保存された既存の登録を読み取るための互換分岐とする。
        awis = registry.get("awis") if schema_version == 4 else registry.get("feedbacks")
        if (
            not isinstance(prefix, str)
            or not is_valid_prefix(prefix)
            or not _is_utc_iso8601(created_at)
            or not _awis_are_valid(awis)
        ):
            return False
        expected = _record(
            path,
            typing.cast(str, nonce),
            prefix=prefix,
            created_at=typing.cast(str, created_at),
            awis=tuple(typing.cast(list[str], awis)),
            identity=identity,
        )
        expected["schema_version"] = schema_version
        if schema_version == 3:
            expected["feedbacks"] = expected.pop("awis")
    else:
        return False
    return (
        isinstance(nonce, str)
        and re.fullmatch(r"[0-9a-f]{64}", nonce) is not None
        and marker == registry
        and registry == expected
    )


def _record_mismatch_error(marker_path: pathlib.Path, *, recovered_from_marker: bool) -> ManagedTempError:
    """管理情報の不一致を、登録をマーカーで代替したかに応じた対処付きで返す。"""
    if not recovered_from_marker:
        return ManagedTempError(f"管理情報の内容が一致しない: {marker_path}")
    return ManagedTempError(
        f"管理情報の内容が一致しない: {marker_path}。"
        "登録が無いためマーカーから復元しようとしたが、マーカーが現在の管理情報として成立しない。"
        "内容を確認して実体を直接削除する"
    )


def _write_marker(
    path: pathlib.Path,
    record: dict[str, typing.Any],
    *,
    directory_descriptor: int | None = None,
) -> None:
    marker_path = path / _MARKER_NAME
    if os.name == "posix":
        owns_directory_descriptor = directory_descriptor is None
        if owns_directory_descriptor:
            directory_descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor: int | None = None
        try:
            assert directory_descriptor is not None
            descriptor = os.open(
                _MARKER_NAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_descriptor,
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as marker:
                descriptor = None
                marker.write((json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n").encode())
                marker.flush()
                os.fsync(marker.fileno())
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if owns_directory_descriptor:
                assert directory_descriptor is not None
                os.close(directory_descriptor)
        return
    _write_private_json(marker_path, record)


def prefix_violation(prefix: str) -> str | None:
    """prefixが違反した最初の条件の説明を返す。違反が無ければNoneを返す。"""
    for description, satisfied in _PREFIX_RULES:
        if not satisfied(prefix):
            return description
    return None


def is_valid_prefix(prefix: str) -> bool:
    """prefixが管理対象一時領域の命名規則に一致するか返す。"""
    return prefix_violation(prefix) is None


def _invalid_prefix_error(prefix: str) -> ManagedTempError:
    """違反した条件と拒否値を示すprefix検証エラーを返す。"""
    violation = prefix_violation(prefix)
    assert violation is not None
    return ManagedTempError(f"prefixが条件を満たしていません（{violation}）: {prefix}")


def _remove_created_target(
    path: pathlib.Path,
    *,
    root_descriptor: int | None,
    target_descriptor: int | None,
    created_identity: tuple[int, int] | None,
) -> None:
    """作成処理が所有する空の対象だけを、保持したroot境界から除去する。"""
    if target_descriptor is not None:
        try:
            target_metadata = os.fstat(target_descriptor)
        except OSError:
            target_metadata = None
        if target_metadata is not None and created_identity == (
            target_metadata.st_dev,
            target_metadata.st_ino,
        ):
            with contextlib.suppress(OSError):
                os.unlink(_MARKER_NAME, dir_fd=target_descriptor)
    if root_descriptor is not None:
        try:
            current = os.stat(path.name, dir_fd=root_descriptor, follow_symlinks=False)
        except OSError:
            return
        if (
            created_identity is None
            or (current.st_dev, current.st_ino) != created_identity
            or not stat.S_ISDIR(current.st_mode)
        ):
            return
        with contextlib.suppress(OSError):
            os.rmdir(path.name, dir_fd=root_descriptor)
        return
    if created_identity is None:
        return
    try:
        if _path_identity(path) != created_identity:
            return
    except (OSError, ManagedTempError):
        return
    with contextlib.suppress(OSError):
        path.rmdir()


def create_managed_temp(
    prefix: str,
    root: pathlib.Path | str | None = None,
    awis: tuple[str, ...] = (),
) -> pathlib.Path:
    """管理対象一時ディレクトリを指定root直下へ作成し、絶対パスを返す。"""
    if not is_valid_prefix(prefix):
        raise _invalid_prefix_error(prefix)
    if not _awis_are_valid(list(awis)):
        raise ManagedTempError("awiはパス区切り文字と制御文字を含まない空でないファイル名で指定する")
    explicit_root = root is not None
    if root is None:
        root_path = _temp_root()
    else:
        root_argument = pathlib.Path(root)
        if not root_argument.is_absolute():
            raise ManagedTempError(f"rootは絶対パスで指定する: {root_argument}")
        root_path = pathlib.Path(os.path.abspath(root_argument))
    validated_root = _validate_root(root_path, explicit=explicit_root)
    path: pathlib.Path | None = None
    root_descriptor: int | None = None
    target_descriptor: int | None = None
    created_identity: tuple[int, int] | None = None
    marker_path: pathlib.Path | None = None
    registry_path: pathlib.Path | None = None
    try:
        if os.name == "posix":
            root_descriptor = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened_root = os.fstat(root_descriptor)
            if (opened_root.st_dev, opened_root.st_ino) != (validated_root.device, validated_root.inode):
                raise ManagedTempError(f"管理対象rootが作成中に置換された: {root_path}")
        path = pathlib.Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=root_path))
        if root_descriptor is not None:
            created_metadata = os.stat(path.name, dir_fd=root_descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(created_metadata.st_mode):
                raise ManagedTempError(f"管理対象が作成中に通常ディレクトリではなくなった: {path}")
            created_identity = (created_metadata.st_dev, created_metadata.st_ino)
        _validate_root(root_path, explicit=explicit_root, expected=validated_root)
        if os.name == "posix":
            assert root_descriptor is not None
            target_descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_descriptor,
            )
            opened_target = os.fstat(target_descriptor)
            if created_identity is None or created_identity != (opened_target.st_dev, opened_target.st_ino):
                raise ManagedTempError(f"管理対象が作成中に置換された: {path}")
            os.fchmod(target_descriptor, 0o700)
            _validate_root(root_path, explicit=explicit_root, expected=validated_root)
        elif os.name == "nt":
            _windows_secure_path(path, directory=True)
            created_identity = _windows_identity(path)
        else:
            raise ManagedTempError(f"未対応platform: {os.name}")
        assert path is not None
        marker_path = path / _MARKER_NAME
        registry_path = _registry_path(path)
        nonce = secrets.token_hex(32)
        record = _record(
            path,
            nonce,
            prefix=prefix,
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
            awis=awis,
            identity=created_identity,
        )
        _validate_root(root_path, explicit=explicit_root, expected=validated_root)
        _write_marker(path, record, directory_descriptor=target_descriptor)
        _validate_root(root_path, explicit=explicit_root, expected=validated_root)
        _write_private_json(registry_path, record)
        _validate_root(root_path, explicit=explicit_root, expected=validated_root)
        validate_managed_temp(path)
        return path
    except (ManagedTempError, OSError) as error:
        if registry_path is not None:
            with contextlib.suppress(OSError):
                registry_path.unlink()
        if marker_path is not None and target_descriptor is None:
            with contextlib.suppress(OSError):
                marker_path.unlink()
        if path is not None:
            _remove_created_target(
                path,
                root_descriptor=root_descriptor,
                target_descriptor=target_descriptor,
                created_identity=created_identity,
            )
        if isinstance(error, ManagedTempError):
            raise
        raise ManagedTempError(f"管理情報を作成できない: {error}") from error
    finally:
        if target_descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(target_descriptor)
        if root_descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(root_descriptor)


def _validate_path_shape(path_arg: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    if not path_arg.is_absolute():
        raise ManagedTempError(f"pathは絶対パスで指定する: {path_arg}")
    path = pathlib.Path(os.path.abspath(path_arg))
    root = path.parent
    return root, path


def _load_marker(directory_descriptor: int, path: pathlib.Path) -> dict[str, typing.Any]:
    descriptor: int | None = None
    try:
        before = os.stat(_MARKER_NAME, dir_fd=directory_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ManagedTempError(f"管理情報が通常ファイルではない: {path / _MARKER_NAME}")
        if before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600:
            raise ManagedTempError(f"管理情報の所有者または権限が不正: {path / _MARKER_NAME}")
        descriptor = os.open(_MARKER_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ManagedTempError(f"管理情報が検証中に置換された: {path / _MARKER_NAME}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as marker:
            descriptor = None
            value = json.load(marker)
        after = os.stat(_MARKER_NAME, dir_fd=directory_descriptor, follow_symlinks=False)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManagedTempError(f"管理情報を検証できない: {path / _MARKER_NAME}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
        raise ManagedTempError(f"管理情報が検証中に置換された: {path / _MARKER_NAME}")
    if not isinstance(value, dict):
        raise ManagedTempError(f"管理情報はJSON objectである必要がある: {path / _MARKER_NAME}")
    return value


def _validate_posix(path_arg: pathlib.Path | str, *, registry_fallback: bool = False) -> _ValidatedTemp:
    if os.name != "posix":
        raise ManagedTempError("Windowsの所有者・ACL検証はWindows実機で確定する必要がある")
    root, path = _validate_path_shape(pathlib.Path(path_arg))
    root_state = _validate_root(root)
    root_descriptor: int | None = None
    target_descriptor: int | None = None
    try:
        root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened_root = os.fstat(root_descriptor)
        opened_root_state = _ValidatedRoot(
            opened_root.st_dev,
            opened_root.st_ino,
            opened_root.st_uid,
            stat.S_IMODE(opened_root.st_mode),
        )
        if opened_root_state != root_state:
            raise ManagedTempError(f"管理対象rootが検証中に置換または変更された: {root}")
        before = os.stat(path.name, dir_fd=root_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            raise ManagedTempError(f"管理対象が通常ディレクトリではない: {path}")
        if before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o700:
            raise ManagedTempError(f"管理対象の所有者・権限またはroot直下の条件が不正: {path}")
        target_descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_descriptor,
        )
        opened = os.fstat(target_descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ManagedTempError(f"管理対象が検証中に置換された: {path}")
        marker = _load_marker(target_descriptor, path)
        after_root = os.fstat(root_descriptor)
        after_root_state = _ValidatedRoot(
            after_root.st_dev,
            after_root.st_ino,
            after_root.st_uid,
            stat.S_IMODE(after_root.st_mode),
        )
        if after_root_state != root_state:
            raise ManagedTempError(f"管理対象rootが検証中に置換または変更された: {root}")
        if (opened.st_dev, opened.st_ino) != _path_identity(path):
            raise ManagedTempError(f"管理対象が検証中に置換された: {path}")
    except OSError as error:
        raise ManagedTempError(f"管理対象を検証できない: {path}: {error}") from error
    finally:
        if target_descriptor is not None:
            os.close(target_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)
    registry_path = _registry_path(path)
    recovered_from_marker = registry_fallback and not os.path.lexists(registry_path)
    registry = marker if recovered_from_marker else _load_private_json(registry_path)
    if not _records_match(path, marker, registry, identity=(opened.st_dev, opened.st_ino)):
        raise _record_mismatch_error(path / _MARKER_NAME, recovered_from_marker=recovered_from_marker)
    _validate_root(root, expected=root_state)
    return _ValidatedTemp(
        path,
        opened.st_dev,
        opened.st_ino,
        typing.cast(str, registry["nonce"]),
        registry_path,
        registry,
        root_state.device,
        root_state.inode,
        root_state.owner,
        root_state.mode,
    )


def _validate_windows(path_arg: pathlib.Path | str, *, registry_fallback: bool = False) -> _ValidatedTemp:
    root, path = _validate_path_shape(pathlib.Path(path_arg))
    root_state = _validate_root(root)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ManagedTempError(f"管理対象を検証できない: {path}: {error}") from error
    if not stat.S_ISDIR(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT:
        raise ManagedTempError(f"管理対象が通常ディレクトリではない: {path}")
    _validate_windows_managed_root_security(path)
    identity = _windows_identity(path)
    marker = _load_private_json(path / _MARKER_NAME)
    registry_path = _registry_path(path)
    recovered_from_marker = registry_fallback and not os.path.lexists(registry_path)
    registry = marker if recovered_from_marker else _load_private_json(registry_path)
    if not _records_match(path, marker, registry, identity=identity):
        raise _record_mismatch_error(path / _MARKER_NAME, recovered_from_marker=recovered_from_marker)
    _validate_root(root, expected=root_state)
    if _windows_identity(path) != identity:
        raise ManagedTempError(f"管理対象が検証中に置換された: {path}")
    return _ValidatedTemp(
        path,
        identity[0],
        identity[1],
        typing.cast(str, registry["nonce"]),
        registry_path,
        registry,
        root_state.device,
        root_state.inode,
        root_state.owner,
        root_state.mode,
        root_state.security,
    )


def validate_managed_temp(path_arg: pathlib.Path | str) -> pathlib.Path:
    """管理対象一時ディレクトリを削除せずに検証する。"""
    if os.name == "posix":
        return _validate_posix(path_arg).path
    if os.name == "nt":
        return _validate_windows(path_arg).path
    raise ManagedTempError(f"未対応platform: {os.name}")


def is_missing_registered_temp(path_arg: pathlib.Path | str) -> bool:
    """登録だけが残り実体を失った管理対象であるかを判定する。

    真を返す条件は、登録されたroot直下の絶対パスであること、当該pathを記録した登録ファイルが
    存在すること、実体が存在しないことの3つをすべて満たす場合とする。
    実体を失った領域には当該領域を使用中の主体が存在しないため、この条件に限り
    真正性検証（所有者・権限・マーカー）を経ずに登録の消滅として扱う。
    実体が残る管理対象は本判定の対象外とし、従来どおり真正性検証で扱う。
    """
    try:
        _, path = _validate_path_shape(pathlib.Path(path_arg))
        registry = _load_private_json(_registry_path(path))
    except (OSError, ValueError, ManagedTempError):
        return False
    if registry.get("path") != str(path) or os.path.lexists(path):
        return False
    if path.parent.exists():
        try:
            _validate_root(path.parent)
        except (OSError, ValueError, ManagedTempError):
            return False
    return True


def _cleanup_missing_registered_temp(path: pathlib.Path) -> None:
    """実体不在の登録と、同じnonceの消費途中状態を順に除去する。"""
    registry_path = _registry_path(path)
    registry = _load_private_json(registry_path)
    if registry.get("path") != str(path) or os.path.lexists(path):
        raise ManagedTempError(f"実体不在の管理対象として再検証できない: {path}")
    nonce = registry.get("nonce")
    if isinstance(nonce, str) and re.fullmatch(r"[0-9a-f]{64}", nonce) is not None:
        consuming = registry_path.with_name(f"{registry_path.name}.consuming-{nonce}")
        consuming.unlink(missing_ok=True)
    registry_path.unlink(missing_ok=True)


def _entity_absence_is_confirmed(record: dict[str, typing.Any], path: pathlib.Path) -> bool:
    """記録された実体が現在の実行文脈で確実に失われているかを返す。"""
    if record.get("platform") != os.name or record.get("owner") != _owner_record():
        return False
    identity = record.get("identity")
    if not (
        isinstance(identity, list)
        and len(identity) == 2
        and all(isinstance(value, int) and not isinstance(value, bool) for value in identity)
    ):
        return False
    try:
        root = _validate_root(path.parent)
        absent = _lstat_or_none(path) is None
    except (OSError, ValueError, ManagedTempError):
        return False
    return root.device == identity[0] and absent


def _consuming_registry_path(registry_path: pathlib.Path) -> pathlib.Path | None:
    """同じ登録に対する消費途中状態が1件だけ残っている場合にそのパスを返す。"""
    candidates = sorted(registry_path.parent.glob(f"{registry_path.name}.consuming-*"))
    return candidates[0] if len(candidates) == 1 else None


class _QuarantineState(enum.Enum):
    """中断した後始末の隔離途中状態に対する判定結果。"""

    ABSENT = "absent"
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNVERIFIABLE = "unverifiable"


class _QuarantineJudgement(typing.NamedTuple):
    """判定結果と、一致した場合に後始末する隔離先を保持する。"""

    state: _QuarantineState
    quarantine: pathlib.Path | None = None
    identity: tuple[int, int] | None = None
    reason: str = ""


def _lstat_or_none(path: pathlib.Path) -> os.stat_result | None:
    """存在しない場合だけNoneを返す。検査できない場合は例外を送出する。"""
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _classify_quarantine(root: pathlib.Path, path: pathlib.Path) -> _QuarantineJudgement:
    """中断した後始末の隔離途中状態を4値のいずれかへ判定する。"""
    registry_path = _registry_path(path)
    try:
        if _lstat_or_none(registry_path) is None:
            return _QuarantineJudgement(_QuarantineState.ABSENT)
    except OSError as error:
        return _QuarantineJudgement(_QuarantineState.UNVERIFIABLE, reason=f"登録の実在を確認できない: {registry_path}: {error}")
    try:
        registry = _load_private_json(registry_path)
    except (OSError, ValueError, ManagedTempError) as error:
        return _QuarantineJudgement(_QuarantineState.UNVERIFIABLE, reason=f"登録を取得できない: {error}")
    nonce = registry.get("nonce")
    identity = registry.get("identity")
    if registry.get("path") != str(path):
        return _QuarantineJudgement(_QuarantineState.ABSENT)
    if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{64}", nonce) is None:
        return _QuarantineJudgement(_QuarantineState.ABSENT)
    if not (
        isinstance(identity, list)
        and len(identity) == 2
        and all(isinstance(value, int) and not isinstance(value, bool) for value in identity)
    ):
        return _QuarantineJudgement(_QuarantineState.ABSENT)
    expected = (identity[0], identity[1])
    quarantine = root / f".agent-toolkit-cleanup-{nonce}"
    try:
        if _lstat_or_none(path) is not None:
            return _QuarantineJudgement(_QuarantineState.ABSENT)
        metadata = _lstat_or_none(quarantine)
        if metadata is None:
            return _QuarantineJudgement(_QuarantineState.ABSENT)
        if os.name == "nt" and getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT:
            return _QuarantineJudgement(_QuarantineState.MISMATCHED, quarantine)
        if not stat.S_ISDIR(metadata.st_mode):
            return _QuarantineJudgement(_QuarantineState.MISMATCHED, quarantine)
        if _path_identity(quarantine) != expected:
            return _QuarantineJudgement(_QuarantineState.MISMATCHED, quarantine)
    except (OSError, ManagedTempError) as error:
        return _QuarantineJudgement(_QuarantineState.UNVERIFIABLE, reason=f"隔離途中状態を検査できない: {error}")
    return _QuarantineJudgement(_QuarantineState.MATCHED, quarantine, expected)


def _restore_interrupted_consume(registry_path: pathlib.Path) -> bool:
    """消費途中状態だけが残る場合に登録を取り戻して真を返す。"""
    try:
        if _lstat_or_none(registry_path) is not None:
            return False
        consuming = _consuming_registry_path(registry_path)
        if consuming is None:
            return False
        os.replace(consuming, registry_path)
    except OSError as error:
        raise ManagedTempError(
            f"中断した後始末の登録を復元できない: {registry_path}: {error}。"
            "管理情報を保持したため、原因を除去した後に同じcleanupを再試行できる"
        ) from error
    return True


def _cleanup_quarantine(root: pathlib.Path, quarantine: pathlib.Path, identity: tuple[int, int]) -> None:
    """一致した隔離先について、中断した削除を最後まで実行する。"""
    try:
        _validate_root(root)
        if os.name == "posix":
            if not shutil.rmtree.avoids_symlink_attacks:
                raise ManagedTempError("symlink attack耐性を持つ後始末手段を利用できない")
            descriptor = os.open(quarantine, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != identity:
                    raise ManagedTempError(f"隔離先が再開時に置換された: {quarantine}")
                _clear_directory(descriptor)
            finally:
                os.close(descriptor)
            os.rmdir(quarantine)
        else:
            shutil.rmtree(quarantine)
    except OSError as error:
        raise ManagedTempError(
            f"中断した後始末の隔離先を後始末できない: {quarantine}: {error}。"
            "管理情報と隔離先を保持したため、原因を除去した後に同じcleanupを再試行できる"
        ) from error


def _unregistered_candidates(prefix: str | None) -> list[pathlib.Path]:
    """既定の一時root直下で、マーカーだけが残る管理対象の絶対パスを返す。

    本関数は`atk`の`managed-temp`以外の全サブコマンドの前段から呼ばれ、対話シェルの起動ごとに
    発火する経路を持つ。一時ディレクトリ直下の項目ごとに外部状態ディレクトリを解決すると、
    当該項目数に比例した待ち時間が対話シェルの起動へ生じる。判定を追加する場合も、
    項目の種別とマーカーの有無で候補を限定した後に外部状態を解決する評価順序を維持する。
    """
    root = _temp_root()
    with os.scandir(root) as entries:
        names = sorted(entry.name for entry in entries if entry.is_dir(follow_symlinks=False))
    candidates: list[pathlib.Path] = []
    for name in names:
        if prefix is not None and not name.startswith(f"{prefix}-"):
            continue
        child = root / name
        if not os.path.lexists(child / _MARKER_NAME) or os.path.lexists(_registry_path(child)):
            continue
        candidates.append(child.absolute())
    return candidates


def count_unregistered_candidates(prefix: str | None = None) -> int:
    """登録を持たない管理対象の件数を返す。"""
    return len(_unregistered_candidates(prefix))


def _marker_recovery_is_accepted(path_arg: pathlib.Path | str) -> bool:
    """実体側マーカーだけから登録を復元できる管理対象かを返す。

    `atk managed-temp list`が`--recover-registry`を案内する条件と、
    `atk managed-temp cleanup --recover-registry`がマーカーを登録として受理する条件を
    同じ判定へ依存させる。案内した回収手段が同じ管理対象で失敗する事態を防ぐためである。
    マーカーを取得できない場合と記録が一致しない場合は、いずれも復元できないものとして扱う。
    """
    _, path = _validate_path_shape(pathlib.Path(path_arg))
    try:
        identity = _path_identity(path)
        if os.name == "posix":
            directory_descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                marker = _load_marker(directory_descriptor, path)
            finally:
                os.close(directory_descriptor)
        else:
            marker = _load_private_json(path / _MARKER_NAME)
    except (OSError, ManagedTempError):
        return False
    return _records_match(path, marker, marker, identity=identity)


def _report_unregistered_candidates(prefix: str | None) -> None:
    """既定の一時root直下で、マーカーだけが残る管理対象を報告する。"""
    try:
        candidates = _unregistered_candidates(prefix)
    except (OSError, ManagedTempError) as error:
        print(f"warning: 登録を持たない管理対象を探索できない: {error}", file=sys.stderr)
        return
    for child in candidates:
        registry_path = _registry_path(child)
        if _consuming_registry_path(registry_path) is not None:
            print(
                f"warning: 後始末が中断した可能性がある管理対象があります: {child}"
                f"（回収する場合は atk managed-temp cleanup --path {child}）",
                file=sys.stderr,
            )
            continue
        if not _marker_recovery_is_accepted(child):
            print(
                f"warning: マーカーから登録を復元できない管理対象があります: {child}"
                "（`--recover-registry`では回収できません。内容を確認して実体を直接削除してください）",
                file=sys.stderr,
            )
            continue
        print(
            f"warning: 登録を持たない管理対象があります: {child}"
            f"（回収する場合は atk managed-temp cleanup --path {child} --recover-registry）",
            file=sys.stderr,
        )


def list_managed_temp(prefix: str | None = None, *, report_recovery_candidates: bool = False) -> list[_ManagedTempEntry]:
    """真正性検証を通過した管理対象を作成時刻順で返す。

    実体に依存しない検証（`path`欄の型と登録ファイル名との対応）と`prefix`による限定を通過した
    登録のうち、実体の消滅を確定できたものは登録ファイルごと削除し、削除を警告として報告する。
    実体を失った登録には当該領域を使用中の主体が存在しないため、登録ファイルの削除が
    利用中の管理対象へ影響しない。消滅を確定できない登録は削除せず列挙対象から外す。
    記録時と列挙時で一時領域の設定が異なる登録も回収対象へ含めるため、現在の一時領域直下で
    あることは条件としない。
    `report_recovery_candidates`が真の場合だけ、削除せず保持した登録と、既定の一時root直下で
    登録を持たない管理対象を警告として報告する。回収候補の報告を`atk managed-temp list`に
    限ることで、全コマンドの起動時に実行する掃引が利用者の操作と無関係な警告を出力しない。
    """
    if prefix is not None and not is_valid_prefix(prefix):
        raise _invalid_prefix_error(prefix)
    entries: list[_ManagedTempEntry] = []
    for registry_path in _state_root().glob("*.json"):
        recorded_path: object = None
        try:
            record = _load_private_json(registry_path)
            recorded_path = record["path"]
            if not isinstance(recorded_path, str):
                raise ManagedTempError("管理情報のpathが文字列ではない")
            path = pathlib.Path(recorded_path)
            if _registry_name(path) != registry_path.name:
                raise ManagedTempError(f"登録ファイル名が管理情報のpathと対応しない: {path}")
            schema_version = record.get("schema_version")
            item_prefix = record.get("prefix") if schema_version in (2, 3, 4) else None
            created_at = record.get("created_at") if schema_version in (2, 3, 4) else None
            awis = record.get("awis") if schema_version == 4 else record.get("feedbacks") if schema_version == 3 else []
            if (
                not (item_prefix is None or isinstance(item_prefix, str))
                or not (created_at is None or isinstance(created_at, str))
                or not _awis_are_valid(awis)
            ):
                raise ManagedTempError("管理情報のprefix、created_at又はawisが不正")
            if prefix is not None and item_prefix != prefix:
                continue
            if not os.path.lexists(path):
                if _entity_absence_is_confirmed(record, path):
                    registry_path.unlink(missing_ok=True)
                    print(f"warning: 実体が失われた管理対象の登録を回収しました: {path}", file=sys.stderr)
                elif report_recovery_candidates:
                    print(
                        f"warning: 実体へ到達できないため登録を保持しました: {path}"
                        "（同じ絶対パスへ到達できる実行文脈で atk managed-temp list を実行すると回収されます）",
                        file=sys.stderr,
                    )
                continue
            validate_managed_temp(path)
            entries.append(
                {
                    "path": str(path),
                    "prefix": item_prefix,
                    "created_at": created_at,
                    "awis": typing.cast(list[str], awis),
                }
            )
        except (KeyError, OSError, ValueError, ManagedTempError) as error:
            if report_recovery_candidates:
                recorded_target = f": {recorded_path}" if isinstance(recorded_path, str) else ""
                recovery = (
                    f"。後始末する場合は atk managed-temp cleanup --path {recorded_path} を実行できます。"
                    "実体を削除した場合は、次回の atk managed-temp list で登録を回収します"
                    if isinstance(recorded_path, str)
                    else ""
                )
                print(
                    f"warning: 管理対象を列挙できない: {registry_path}{recorded_target}: {error}{recovery}",
                    file=sys.stderr,
                )
    if report_recovery_candidates:
        _report_unregistered_candidates(prefix)
    return sorted(entries, key=lambda item: (item["created_at"] is not None, item["created_at"] or "", item["path"] or ""))


def sweep_expired_managed_temp(
    *,
    now: datetime.datetime,
    max_age_days: int = MAX_AGE_DAYS,
) -> list[pathlib.Path]:
    """最終更新から`max_age_days`を超えた管理対象一時領域を削除し、削除したパスを返す。"""
    reference = now if now.tzinfo is not None else now.astimezone()
    cutoff = (reference - datetime.timedelta(days=max_age_days)).astimezone(datetime.UTC)
    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
    elapsed = cutoff - epoch
    cutoff_ns = (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000_000 + elapsed.microseconds * 1_000
    deleted: list[pathlib.Path] = []
    for entry in list_managed_temp():
        path = pathlib.Path(entry["path"])
        try:
            latest_mtime_ns = path.stat().st_mtime_ns
            if latest_mtime_ns >= cutoff_ns:
                continue
            contains_git = False
            pending = [path]
            while pending:
                directory = pending.pop()
                with os.scandir(directory) as children:
                    for child in children:
                        metadata = child.stat(follow_symlinks=False)
                        latest_mtime_ns = max(latest_mtime_ns, metadata.st_mtime_ns)
                        if child.name == ".git":
                            contains_git = True
                        if stat.S_ISDIR(metadata.st_mode):
                            pending.append(pathlib.Path(child.path))
            if latest_mtime_ns >= cutoff_ns or contains_git:
                continue
            cleanup_managed_temp(path)
        except (ManagedTempError, OSError) as error:
            print(f"warning: 管理対象一時領域を自動削除できませんでした: {path}: {error}", file=sys.stderr)
            continue
        deleted.append(path)
        print(
            f"note: 最終更新から{max_age_days}日を超えた管理対象一時領域を削除しました: {path}",
            file=sys.stderr,
        )
    return deleted


def _clear_directory(descriptor: int) -> None:
    """開いたディレクトリだけを起点に、リンク参照を避けて内容を除去する。"""
    for name in os.listdir(descriptor):
        before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            os.unlink(name, dir_fd=descriptor)
            continue
        child_descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=descriptor,
        )
        try:
            opened = os.fstat(child_descriptor)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ManagedTempError(f"管理対象の子ディレクトリが後始末中に置換された: {name}")
            _clear_directory(child_descriptor)
            os.rmdir(name, dir_fd=descriptor)
        finally:
            os.close(child_descriptor)


def _restore_posix_quarantine(
    root_descriptor: int,
    quarantine: pathlib.Path,
    target_name: str,
    expected_identity: tuple[int, int],
) -> None:
    """失敗時に、保持したroot descriptorから隔離対象を元の名前へ戻す。"""
    quarantine_metadata = os.stat(quarantine.name, dir_fd=root_descriptor, follow_symlinks=False)
    if (quarantine_metadata.st_dev, quarantine_metadata.st_ino) != expected_identity:
        return
    try:
        os.stat(target_name, dir_fd=root_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        os.rename(quarantine.name, target_name, src_dir_fd=root_descriptor, dst_dir_fd=root_descriptor)


def _tree_snapshot(root: pathlib.Path) -> dict[str, tuple[str, int, int]]:
    """cleanup開始前のtree identityを取得し、reparse pointを拒否する。"""
    snapshot: dict[str, tuple[str, int, int]] = {}
    pending = [root]
    while pending:
        parent = pending.pop()
        for entry in os.scandir(parent):
            path = pathlib.Path(entry.path)
            metadata = path.lstat()
            if os.name == "nt" and getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT:
                raise ManagedTempError(f"Windows reparse pointは後始末できない: {path}")
            kind = "dir" if stat.S_ISDIR(metadata.st_mode) else "leaf"
            device, inode = _path_identity(path)
            relative = str(path.relative_to(root))
            snapshot[relative] = (kind, device, inode)
            if kind == "dir":
                pending.append(path)
    return snapshot


def _consume_registry(validated: _ValidatedTemp) -> pathlib.Path:
    consuming = validated.registry_path.with_name(f"{validated.registry_path.name}.consuming-{validated.nonce}")
    try:
        os.replace(validated.registry_path, consuming)
    except OSError as error:
        raise ManagedTempError(f"外部状態を原子的に消費できない: {validated.registry_path}: {error}") from error
    consumed = _load_private_json(consuming)
    if not _records_match(validated.path, consumed, consumed, identity=(validated.device, validated.inode)):
        with contextlib.suppress(OSError):
            _restore_registry(consuming, validated.registry_path)
        raise ManagedTempError(f"外部状態が消費時に置換された: {validated.registry_path}")
    return consuming


def _restore_registry(consuming: pathlib.Path, registry: pathlib.Path) -> None:
    if consuming.exists() and not registry.exists():
        os.replace(consuming, registry)


def _restore_cleanup_marker(validated: _ValidatedTemp) -> None:
    """検証済みidentityを持つ実体へ欠落したmarkerだけを復元する。"""
    marker = validated.path / _MARKER_NAME
    if os.name == "posix":
        descriptor = os.open(validated.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) != (validated.device, validated.inode):
                raise ManagedTempError(f"管理対象が復元中に置換された: {validated.path}")
            try:
                os.stat(_MARKER_NAME, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                _write_marker(validated.path, validated.record, directory_descriptor=descriptor)
        finally:
            os.close(descriptor)
        return
    if _path_identity(validated.path) != (validated.device, validated.inode):
        raise ManagedTempError(f"管理対象が復元中に置換された: {validated.path}")
    if not os.path.lexists(marker):
        _write_marker(validated.path, validated.record)


def _restore_cleanup_state(
    validated: _ValidatedTemp,
    consuming: pathlib.Path,
    quarantine: pathlib.Path,
) -> None:
    """検証済みrecordを復元し、同じcleanupを再試行できる状態か検証する。"""
    if not os.path.lexists(validated.registry_path):
        _write_private_json(validated.registry_path, validated.record)
    if os.path.lexists(quarantine):
        raise ManagedTempError(f"隔離対象を元のpathへ復元できない: {quarantine}")
    if not os.path.lexists(validated.path):
        if not is_missing_registered_temp(validated.path):
            raise ManagedTempError(f"実体不在時の登録を復元できない: {validated.path}")
    else:
        _restore_cleanup_marker(validated)
        restored = _validate_posix(validated.path) if os.name == "posix" else _validate_windows(validated.path)
        if (restored.device, restored.inode) != (validated.device, validated.inode):
            raise ManagedTempError(f"復元した管理対象のidentityが一致しない: {validated.path}")
    with contextlib.suppress(OSError):
        consuming.unlink(missing_ok=True)


def _cleanup_posix(
    root: pathlib.Path,
    validated: _ValidatedTemp,
    quarantine: pathlib.Path,
    expected_tree: dict[str, tuple[str, int, int]],
) -> None:
    expected_root = _ValidatedRoot(
        validated.root_device,
        validated.root_inode,
        validated.root_owner,
        validated.root_mode,
        validated.root_security,
    )
    root_descriptor: int | None = None
    target_descriptor: int | None = None
    try:
        root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _validate_root(root, expected=expected_root)
        opened_root = os.fstat(root_descriptor)
        if (
            _ValidatedRoot(
                opened_root.st_dev,
                opened_root.st_ino,
                opened_root.st_uid,
                stat.S_IMODE(opened_root.st_mode),
            )
            != expected_root
        ):
            raise ManagedTempError(f"管理対象rootが隔離時に置換または変更された: {root}")
        current = os.stat(validated.path.name, dir_fd=root_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            validated.device,
            validated.inode,
        ):
            raise ManagedTempError(f"管理対象が隔離時に置換された: {validated.path}")
        os.rename(validated.path.name, quarantine.name, src_dir_fd=root_descriptor, dst_dir_fd=root_descriptor)
        current = os.stat(quarantine.name, dir_fd=root_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            validated.device,
            validated.inode,
        ):
            raise ManagedTempError(f"管理対象が隔離時に置換された: {validated.path}")
        target_descriptor = os.open(
            quarantine.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_descriptor,
        )
        opened = os.fstat(target_descriptor)
        if (opened.st_dev, opened.st_ino) != (validated.device, validated.inode):
            raise ManagedTempError(f"管理対象が隔離時に置換された: {validated.path}")
        if _tree_snapshot(quarantine) != expected_tree:
            raise ManagedTempError(f"管理対象の内容が隔離時に置換された: {validated.path}")
        _validate_root(root, expected=expected_root)
        _clear_directory(target_descriptor)
        _validate_root(root, expected=expected_root)
        os.rmdir(quarantine.name, dir_fd=root_descriptor)
    except (ManagedTempError, OSError) as error:
        if root_descriptor is not None:
            with contextlib.suppress(OSError):
                _restore_posix_quarantine(
                    root_descriptor,
                    quarantine,
                    validated.path.name,
                    (validated.device, validated.inode),
                )
        if isinstance(error, ManagedTempError):
            raise
        raise ManagedTempError(f"管理対象を後始末できない: {validated.path}: {error}") from error
    finally:
        if target_descriptor is not None:
            os.close(target_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)


def _cleanup_windows(
    root: pathlib.Path,
    validated: _ValidatedTemp,
    quarantine: pathlib.Path,
    expected_tree: dict[str, tuple[str, int, int]],
) -> None:
    expected_root = _ValidatedRoot(
        validated.root_device,
        validated.root_inode,
        validated.root_owner,
        validated.root_mode,
        validated.root_security,
    )
    try:
        _validate_root(root, expected=expected_root)
        os.replace(validated.path, quarantine)
        _validate_root(root, expected=expected_root)
        if _windows_identity(quarantine) != (validated.device, validated.inode):
            raise ManagedTempError(f"管理対象が隔離時に置換された: {validated.path}")
        if _tree_snapshot(quarantine) != expected_tree:
            raise ManagedTempError(f"管理対象の内容が隔離時に置換された: {validated.path}")
        _validate_root(root, expected=expected_root)
        shutil.rmtree(quarantine)
    except OSError as error:
        raise ManagedTempError(f"管理対象を後始末できない: {validated.path}: {error}") from error


def cleanup_managed_temp(path_arg: pathlib.Path | str, *, recover_registry: bool = False) -> None:
    """検証済みの管理対象一時ディレクトリだけを後始末する。

    実体を失った管理対象は、登録ファイルの削除だけで整合させる。ただし元pathの不在が
    後始末の隔離によるものである場合は、登録が記録するnonceとidentityへ一致する隔離先に
    限って中断した削除を最後まで実行してから、登録を削除する。
    登録が消費途中状態としてだけ残る管理対象は、実体の有無にかかわらず中断した後始末の
    再開として消費途中状態から登録を原子的に取り戻してから、実体が残る場合は通常の後始末へ、
    実体が不在の場合は実体を失った管理対象の整合へ合流する。消費途中状態は管理側の状態
    ディレクトリにあり、作成処理が書いた登録と同じ信頼水準を持つため明示指定を要さない。
    `recover_registry`が真の場合だけ、消費途中状態も持たず登録だけを失った管理対象について、
    実体側マーカーが記録した絶対パスと実体のidentityへ一致することを確認して登録を復元する。
    マーカーは実体側にあり、作成処理が書いたものと後から置かれたものを内容だけでは区別できない。
    この復元は利用者の明示指定を第二の信頼根拠として要求し、既定では行わない。
    """
    root, path = _validate_path_shape(pathlib.Path(path_arg))
    registry_path = _registry_path(path)
    if _restore_interrupted_consume(registry_path):
        print(f"warning: 中断した後始末の登録を復元しました: {registry_path}", file=sys.stderr)
    judgement = _classify_quarantine(root, path)
    if judgement.state is _QuarantineState.UNVERIFIABLE:
        raise ManagedTempError(
            f"中断した後始末の状態を判定できない: {path}: {judgement.reason}。"
            "管理情報と隔離先を保持したため、原因を除去した後に同じcleanupを再試行できる"
        )
    if judgement.state is _QuarantineState.MATCHED:
        if judgement.quarantine is None or judgement.identity is None:
            raise AssertionError("一致した隔離途中状態に後始末情報がない")
        _cleanup_quarantine(root, judgement.quarantine, judgement.identity)
        print(f"warning: 中断した後始末の隔離先を後始末しました: {path}", file=sys.stderr)
    if is_missing_registered_temp(path):
        _cleanup_missing_registered_temp(path)
        return
    validated = (
        _validate_posix(path, registry_fallback=recover_registry)
        if os.name == "posix"
        else _validate_windows(path, registry_fallback=recover_registry)
    )
    if _lstat_or_none(validated.registry_path) is None:
        _write_private_json(validated.registry_path, validated.record)
        print(f"warning: 欠落した登録をマーカーから復元しました: {validated.registry_path}", file=sys.stderr)
    if os.name == "nt":
        # 利用中に追加された受理済みACEを除去し、隔離以降を現在利用者だけのDACLで実行する。
        _windows_secure_path(
            path,
            directory=True,
            expected_identity=(validated.device, validated.inode),
        )
    before = _tree_snapshot(path)
    consuming = _consume_registry(validated)
    quarantine = root / f".agent-toolkit-cleanup-{validated.nonce}"
    try:
        if quarantine.exists() or quarantine.is_symlink():
            raise ManagedTempError(f"隔離先が既に存在する: {quarantine}")
        if _tree_snapshot(path) != before:
            raise ManagedTempError(f"管理対象の内容が後始末開始前に置換された: {path}")
        if os.name == "posix":
            if not shutil.rmtree.avoids_symlink_attacks:
                raise ManagedTempError("symlink attack耐性を持つ後始末手段を利用できない")
            _cleanup_posix(root, validated, quarantine, before)
        elif os.name == "nt":
            _cleanup_windows(root, validated, quarantine, before)
        else:
            raise ManagedTempError(f"未対応platform: {os.name}")
        consuming.unlink()
    except (ManagedTempError, OSError) as error:
        recovery_error: ManagedTempError | OSError | None = None
        try:
            if (
                os.path.lexists(quarantine)
                and not os.path.lexists(path)
                and _path_identity(quarantine)
                == (
                    validated.device,
                    validated.inode,
                )
            ):
                os.replace(quarantine, path)
            _restore_cleanup_state(validated, consuming, quarantine)
        except (ManagedTempError, OSError) as restore_error:
            recovery_error = restore_error
        failure = str(error) if isinstance(error, ManagedTempError) else f"管理対象を後始末できない: {path}: {error}"
        if recovery_error is None:
            raise ManagedTempError(
                f"{failure}。管理情報の復元を検証したため、原因を除去した後に同じcleanupを再試行できる"
            ) from error
        raise ManagedTempError(
            f"{failure}。管理情報の復元を検証できないため、同じcleanupを再試行できない: {recovery_error}"
        ) from error


def build_parser(parser: argparse.ArgumentParser, *, command_dest: str = "command") -> None:
    """管理対象一時領域のサブコマンドを登録する。"""
    subparsers = _atk_help.add_subcommands(
        parser,
        dest=command_dest,
        required=False,
        show_help_when_missing=True,
    )
    create_parser = _atk_help.add_command(subparsers, "create", **_atk_help.HELP["atk managed-temp create"])
    create_parser.add_argument(
        "--prefix",
        required=True,
        help="作成するディレクトリ名の先頭へ付ける用途識別子。"
        + "。".join(description for description, _satisfied in _PREFIX_RULES)
        + "。",
    )
    create_parser.add_argument(
        "--root",
        type=pathlib.Path,
        help=("作成先の一時root。別のnamespaceからも同じ絶対パスで到達できる既存ディレクトリを指定する場合だけ使う。"),
    )
    create_parser.add_argument(
        "--awi",
        action="append",
        help="この領域が対応するAWIのファイル名。複数回指定できる",
    )
    cleanup_parser = _atk_help.add_command(subparsers, "cleanup", **_atk_help.HELP["atk managed-temp cleanup"])
    cleanup_parser.add_argument(
        "--path",
        required=True,
        type=pathlib.Path,
        help="後始末する管理対象一時ディレクトリの絶対パス。作成時に出力された値を指定する。",
    )
    cleanup_parser.add_argument(
        "--recover-registry",
        action="store_true",
        help="登録を失った管理対象を、実体側マーカーの検証を通過した場合に限り復元して後始末する",
    )
    list_parser = _atk_help.add_command(subparsers, "list", **_atk_help.HELP["atk managed-temp list"])
    list_parser.add_argument("--prefix", help="列挙する領域を用途識別子で限定する。")


def dispatch(args: argparse.Namespace, *, command_dest: str = "command") -> int:
    """解析済み引数に対応する操作を実行し、終了状態を返す。"""
    try:
        if getattr(args, command_dest) == "create":
            print(
                create_managed_temp(
                    args.prefix,
                    getattr(args, "root", None),
                    tuple(getattr(args, "awi", None) or ()),
                )
            )
        elif getattr(args, command_dest) == "cleanup":
            cleanup_managed_temp(args.path, recover_registry=getattr(args, "recover_registry", False))
        else:
            entries = list_managed_temp(args.prefix, report_recovery_candidates=True)
            for entry in entries:
                print(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            return 0 if entries else 1
        return 0
    except ManagedTempError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    """CLI引数を解釈して管理対象一時ディレクトリを操作する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    build_parser(parser)
    return dispatch(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
