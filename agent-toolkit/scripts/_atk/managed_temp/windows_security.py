# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
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

from _atk import help_text as _atk_help


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _atk.managed_temp.registry import (
        MAX_AGE_DAYS,
        ManagedTempError,
        _MARKER_NAME,
        _ManagedTempEntry,
        _PREFIX_RE,
        _PREFIX_RULES,
        _SCHEMA_VERSION,
        _UTC_ISO8601_RE,
        _ValidatedRoot,
        _ValidatedTemp,
        _WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
        _WINDOWS_ACCESS_DENIED_ACE_TYPE,
        _WINDOWS_ACL_REVISION,
        _WINDOWS_CONTAINER_INHERIT_ACE,
        _WINDOWS_DACL_SECURITY_INFORMATION,
        _WINDOWS_ERROR_ACCESS_DENIED,
        _WINDOWS_EXTERNAL_WRITER_ACCESS,
        _WINDOWS_FILE_ALL_ACCESS,
        _WINDOWS_FILE_ATTRIBUTE_DIRECTORY,
        _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS,
        _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        _WINDOWS_FILE_SHARE_ALL,
        _WINDOWS_OBJECT_INHERIT_ACE,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_OWNER_SECURITY_INFORMATION,
        _WINDOWS_PROTECTED_DACL_SECURITY_INFORMATION,
        _WINDOWS_READ_ATTRIBUTES,
        _WINDOWS_READ_CONTROL,
        _WINDOWS_REPARSE_POINT,
        _WINDOWS_SE_DACL_PROTECTED,
        _WINDOWS_SE_FILE_OBJECT,
        _WINDOWS_SYNCHRONIZE,
        _WINDOWS_WRITE_DAC,
        _WINDOWS_WRITE_OWNER,
        _WindowsApiError,
        _WindowsHandleOpenError,
        _awis_are_valid,
        _is_utc_iso8601,
        _load_marker,
        _load_private_json,
        _owner_record,
        _path_identity,
        _record,
        _record_base,
        _record_mismatch_error,
        _records_match,
        _registry_name,
        _registry_path,
        _state_root,
        _state_root_path,
        _temp_root,
        _validate_root,
        _write_marker,
        _write_private_json,
    )
    from _atk.managed_temp.creation import (
        _invalid_prefix_error,
        _remove_created_target,
        _validate_path_shape,
        _validate_posix,
        _validate_windows,
        create_managed_temp,
        is_valid_prefix,
        prefix_violation,
        validate_managed_temp,
    )
    from _atk.managed_temp.inventory import (
        _QuarantineJudgement,
        _QuarantineState,
        _classify_quarantine,
        _cleanup_missing_registered_temp,
        _cleanup_posix,
        _cleanup_quarantine,
        _cleanup_windows,
        _clear_directory,
        _consume_registry,
        _consuming_registry_path,
        _entity_absence_is_confirmed,
        _lstat_or_none,
        _marker_recovery_is_accepted,
        _report_unregistered_candidates,
        _restore_cleanup_marker,
        _restore_cleanup_state,
        _restore_interrupted_consume,
        _restore_posix_quarantine,
        _restore_registry,
        _tree_snapshot,
        _unregistered_candidates,
        cleanup_managed_temp,
        count_unregistered_candidates,
        is_missing_registered_temp,
        list_managed_temp,
        sweep_expired_managed_temp,
    )
    from _atk.managed_temp.cli import build_parser, dispatch, main


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
    *,
    allow_reparse: bool = False,
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
        if not allow_reparse and information.attributes & _WINDOWS_REPARSE_POINT:
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


def _windows_reparse_identity(path: pathlib.Path) -> tuple[int, int]:
    """Reparse point自体のWindowsハンドルからボリュームとファイルのIDを返す。"""
    with _windows_path_handle(path, _WINDOWS_READ_ATTRIBUTES, allow_reparse=True) as (_, information):
        return _windows_information_identity(information)
