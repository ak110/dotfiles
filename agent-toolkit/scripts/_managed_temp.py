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
from ctypes import wintypes

_MARKER_NAME = ".agent-toolkit-managed-temp.json"
_SCHEMA_VERSION = 2
_PREFIX_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
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
    """利用者が入力または実行環境を修正できる検証エラー。"""


class _WindowsApiError(ManagedTempError):
    """Windows APIのerror codeを保持する検証エラー。"""

    def __init__(self, action: str, path: pathlib.Path | None, error_code: int) -> None:
        target = f": {path}" if path is not None else ""
        super().__init__(f"{action}{target}: {error_code}")
        self.error_code = error_code


class _WindowsHandleOpenError(_WindowsApiError):
    """Windows path handleを開けなかったことを示す。"""


class _ValidatedTemp(typing.NamedTuple):
    path: pathlib.Path
    device: int
    inode: int
    nonce: str
    registry_path: pathlib.Path


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
    """最後のWindows error codeを含むhandle取得エラーを作成する。"""
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
    """Reparse pointを追跡しないpath handleと属性を返す。"""
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
        raise _windows_handle_open_error("Windows path handleを取得できない", path)
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
    """Owner更新可否を判定した単一のsecurity更新用handleを返す。"""
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
    """取得済みのWindows handle情報からvolumeとfile IDを返す。"""
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
            raise ManagedTempError(f"Windows ownerを変更できるhandleを取得できない: {path}")
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
    """開いているhandleへOwnerとDACLを設定する。"""
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
    """開いているhandleのsecurity descriptorをraw SIDとACEへ分解して返す。"""
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
    expected_flags = _WINDOWS_OBJECT_INHERIT_ACE | _WINDOWS_CONTAINER_INHERIT_ACE
    current_user_aces = [ace for ace in security.aces if _windows_current_user_ace_is_valid(ace, current_sid, expected_flags)]
    other_aces = [ace for ace in security.aces if not _windows_current_user_ace_is_valid(ace, current_sid, expected_flags)]
    valid_operational_acl = len(current_user_aces) == 1 and (
        not other_aces
        or (len(other_aces) == 1 and _windows_external_writer_ace_is_valid(other_aces[0], current_sid, expected_flags))
    )
    if not security.directory or not _windows_security_base_is_valid(security, current_sid) or not valid_operational_acl:
        raise ManagedTempError(f"Windows pathのownerまたはACLが不正: {path}")


def _windows_identity(path: pathlib.Path) -> tuple[int, int]:
    """Reparse pointを開かず、Windows handleからvolumeとfile IDを返す。"""
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


def _registry_path(path: pathlib.Path) -> pathlib.Path:
    digest = hashlib.sha256(os.fsencode(path)).hexdigest()
    return _state_root() / f"{digest}.json"


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


def _record(
    path: pathlib.Path, nonce: str, *, prefix: str | None = None, created_at: str | None = None
) -> dict[str, typing.Any]:
    device, inode = _path_identity(path)
    record = {
        "schema_version": _SCHEMA_VERSION,
        "path": str(path),
        "platform": os.name,
        "owner": _owner_record(),
        "identity": [device, inode],
        "nonce": nonce,
    }
    if prefix is not None and created_at is not None:
        record["prefix"] = prefix
        record["created_at"] = created_at
    return record


def _is_utc_iso8601(value: object) -> bool:
    """valueがUTC offsetを持つISO 8601日時文字列か返す。"""
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == datetime.timedelta(0)


def _records_match(path: pathlib.Path, marker: dict[str, typing.Any], registry: dict[str, typing.Any]) -> bool:
    nonce = registry.get("nonce")
    schema_version = registry.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        return False
    if schema_version == 1:
        expected = _record(path, typing.cast(str, nonce))
        expected["schema_version"] = 1
    elif schema_version == 2:
        prefix = registry.get("prefix")
        created_at = registry.get("created_at")
        if not isinstance(prefix, str) or not is_valid_prefix(prefix) or not _is_utc_iso8601(created_at):
            return False
        expected = _record(path, typing.cast(str, nonce), prefix=prefix, created_at=typing.cast(str, created_at))
    else:
        return False
    return (
        isinstance(nonce, str)
        and re.fullmatch(r"[0-9a-f]{64}", nonce) is not None
        and marker == registry
        and registry == expected
    )


def _write_marker(path: pathlib.Path, record: dict[str, typing.Any]) -> None:
    marker_path = path / _MARKER_NAME
    if os.name == "posix":
        directory_descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor: int | None = None
        try:
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
            os.close(directory_descriptor)
        return
    _write_private_json(marker_path, record)


def is_valid_prefix(prefix: str) -> bool:
    """prefixが管理対象一時領域の命名規則に一致するか返す。"""
    return _PREFIX_RE.fullmatch(prefix) is not None


def create_managed_temp(prefix: str) -> pathlib.Path:
    """管理対象一時ディレクトリを作成し、絶対パスを返す。"""
    if not is_valid_prefix(prefix):
        raise ManagedTempError("prefixは英小文字・数字・ハイフンだけで指定する")
    root = _temp_root()
    try:
        path = pathlib.Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=root))
        if os.name == "posix":
            os.chmod(path, 0o700)
        elif os.name == "nt":
            _windows_secure_path(path, directory=True)
        else:
            raise ManagedTempError(f"未対応platform: {os.name}")
    except OSError as error:
        raise ManagedTempError(f"管理対象一時ディレクトリを作成できない: {error}") from error

    marker_path = path / _MARKER_NAME
    registry_path: pathlib.Path | None = None
    try:
        registry_path = _registry_path(path)
        nonce = secrets.token_hex(32)
        record = _record(
            path,
            nonce,
            prefix=prefix,
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )
        _write_marker(path, record)
        _write_private_json(registry_path, record)
        validate_managed_temp(path)
        return path
    except (ManagedTempError, OSError) as error:
        if registry_path is not None:
            with contextlib.suppress(OSError):
                registry_path.unlink()
        with contextlib.suppress(OSError):
            marker_path.unlink()
        with contextlib.suppress(OSError):
            path.rmdir()
        if isinstance(error, ManagedTempError):
            raise
        raise ManagedTempError(f"管理情報を作成できない: {error}") from error


def _validate_path_shape(path_arg: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    if not path_arg.is_absolute():
        raise ManagedTempError(f"pathは絶対パスで指定する: {path_arg}")
    root = _temp_root()
    path = pathlib.Path(os.path.abspath(path_arg))
    if path.parent != root:
        raise ManagedTempError(f"管理対象は一時ディレクトリ直下に限る: {path}")
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


def _validate_posix(path_arg: pathlib.Path | str) -> _ValidatedTemp:
    if os.name != "posix":
        raise ManagedTempError("Windowsの所有者・ACL検証はWindows実機で確定する必要がある")
    _, path = _validate_path_shape(pathlib.Path(path_arg))
    descriptor: int | None = None
    try:
        before = path.lstat()
        if not stat.S_ISDIR(before.st_mode):
            raise ManagedTempError(f"管理対象が通常ディレクトリではない: {path}")
        if before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o700:
            raise ManagedTempError(f"管理対象の所有者または権限が不正: {path}")
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ManagedTempError(f"管理対象が検証中に置換された: {path}")
        marker = _load_marker(descriptor, path)
    except OSError as error:
        raise ManagedTempError(f"管理対象を検証できない: {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    registry_path = _registry_path(path)
    registry = _load_private_json(registry_path)
    if not _records_match(path, marker, registry):
        raise ManagedTempError(f"管理情報の内容が一致しない: {path / _MARKER_NAME}")
    return _ValidatedTemp(path, opened.st_dev, opened.st_ino, typing.cast(str, registry["nonce"]), registry_path)


def _validate_windows(path_arg: pathlib.Path | str) -> _ValidatedTemp:
    _, path = _validate_path_shape(pathlib.Path(path_arg))
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
    registry = _load_private_json(registry_path)
    if not _records_match(path, marker, registry):
        raise ManagedTempError(f"管理情報の内容が一致しない: {path / _MARKER_NAME}")
    if _windows_identity(path) != identity:
        raise ManagedTempError(f"管理対象が検証中に置換された: {path}")
    return _ValidatedTemp(path, identity[0], identity[1], typing.cast(str, registry["nonce"]), registry_path)


def validate_managed_temp(path_arg: pathlib.Path | str) -> pathlib.Path:
    """管理対象一時ディレクトリを削除せずに検証する。"""
    if os.name == "posix":
        return _validate_posix(path_arg).path
    if os.name == "nt":
        return _validate_windows(path_arg).path
    raise ManagedTempError(f"未対応platform: {os.name}")


def list_managed_temp(prefix: str | None = None) -> list[dict[str, str | None]]:
    """真正性検証を通過した管理対象を作成時刻順で返す。"""
    if prefix is not None and not is_valid_prefix(prefix):
        raise ManagedTempError("prefixは英小文字・数字・ハイフンだけで指定する")
    entries: list[dict[str, str | None]] = []
    for registry_path in _state_root().glob("*.json"):
        try:
            record = _load_private_json(registry_path)
            path = pathlib.Path(typing.cast(str, record["path"]))
            validate_managed_temp(path)
            item_prefix = record.get("prefix") if record.get("schema_version") == 2 else None
            created_at = record.get("created_at") if record.get("schema_version") == 2 else None
            if not (item_prefix is None or isinstance(item_prefix, str)) or not (
                created_at is None or isinstance(created_at, str)
            ):
                raise ManagedTempError("管理情報のprefix又はcreated_atが不正")
            if prefix is not None and item_prefix != prefix:
                continue
            entries.append({"path": str(path), "prefix": item_prefix, "created_at": created_at})
        except (KeyError, OSError, ValueError, ManagedTempError) as error:
            print(f"warning: 管理対象を列挙できない: {registry_path}: {error}", file=sys.stderr)
    return sorted(entries, key=lambda item: (item["created_at"] is not None, item["created_at"] or "", item["path"] or ""))


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
    if not _records_match(validated.path, consumed, consumed):
        with contextlib.suppress(OSError):
            _restore_registry(consuming, validated.registry_path)
        raise ManagedTempError(f"外部状態が消費時に置換された: {validated.registry_path}")
    return consuming


def _restore_registry(consuming: pathlib.Path, registry: pathlib.Path) -> None:
    if consuming.exists() and not registry.exists():
        os.replace(consuming, registry)


def _cleanup_posix(
    root: pathlib.Path,
    validated: _ValidatedTemp,
    quarantine: pathlib.Path,
    expected_tree: dict[str, tuple[str, int, int]],
) -> None:
    root_descriptor: int | None = None
    target_descriptor: int | None = None
    try:
        root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
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
        _clear_directory(target_descriptor)
        os.rmdir(quarantine.name, dir_fd=root_descriptor)
    except OSError as error:
        raise ManagedTempError(f"管理対象を後始末できない: {validated.path}: {error}") from error
    finally:
        if target_descriptor is not None:
            os.close(target_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)


def _cleanup_windows(
    validated: _ValidatedTemp,
    quarantine: pathlib.Path,
    expected_tree: dict[str, tuple[str, int, int]],
) -> None:
    try:
        os.replace(validated.path, quarantine)
        if _windows_identity(quarantine) != (validated.device, validated.inode):
            raise ManagedTempError(f"管理対象が隔離時に置換された: {validated.path}")
        if _tree_snapshot(quarantine) != expected_tree:
            raise ManagedTempError(f"管理対象の内容が隔離時に置換された: {validated.path}")
        shutil.rmtree(quarantine)
    except OSError as error:
        raise ManagedTempError(f"管理対象を後始末できない: {validated.path}: {error}") from error


def cleanup_managed_temp(path_arg: pathlib.Path | str) -> None:
    """検証済みの管理対象一時ディレクトリだけを後始末する。"""
    root, path = _validate_path_shape(pathlib.Path(path_arg))
    validated = _validate_posix(path) if os.name == "posix" else _validate_windows(path)
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
            _cleanup_windows(validated, quarantine, before)
        else:
            raise ManagedTempError(f"未対応platform: {os.name}")
        consuming.unlink()
    except (ManagedTempError, OSError) as error:
        with contextlib.suppress(OSError):
            if (
                quarantine.exists()
                and not path.exists()
                and _path_identity(quarantine)
                == (
                    validated.device,
                    validated.inode,
                )
            ):
                os.replace(quarantine, path)
        with contextlib.suppress(OSError):
            _restore_registry(consuming, validated.registry_path)
        if isinstance(error, ManagedTempError):
            raise
        raise ManagedTempError(f"管理対象を後始末できない: {path}: {error}") from error


def build_parser(parser: argparse.ArgumentParser, *, command_dest: str = "command") -> None:
    """管理対象一時領域のサブコマンドを登録する。"""
    subparsers = parser.add_subparsers(dest=command_dest, required=True)
    create_parser = subparsers.add_parser("create", help="管理対象一時ディレクトリを作成する")
    create_parser.add_argument("--prefix", required=True)
    cleanup_parser = subparsers.add_parser("cleanup", help="管理対象一時ディレクトリを後始末する")
    cleanup_parser.add_argument("--path", required=True, type=pathlib.Path)
    list_parser = subparsers.add_parser("list", help="管理対象一時ディレクトリを列挙する")
    list_parser.add_argument("--prefix")


def dispatch(args: argparse.Namespace, *, command_dest: str = "command") -> int:
    """解析済み引数に対応する操作を実行し、終了状態を返す。"""
    try:
        if getattr(args, command_dest) == "create":
            print(create_managed_temp(args.prefix))
        elif getattr(args, command_dest) == "cleanup":
            cleanup_managed_temp(args.path)
        else:
            entries = list_managed_temp(args.prefix)
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
