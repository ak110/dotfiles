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
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import typing
from ctypes import wintypes

_MARKER_NAME = ".agent-toolkit-managed-temp.json"
_SCHEMA_VERSION = 1
_PREFIX_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_WINDOWS_REPARSE_POINT = 0x400


class ManagedTempError(Exception):
    """利用者が入力または実行環境を修正できる検証エラー。"""


class _ValidatedTemp(typing.NamedTuple):
    path: pathlib.Path
    device: int
    inode: int
    nonce: str
    registry_path: pathlib.Path


def _windows_dll(name: str) -> typing.Any:
    """Windows専用DLLを遅延取得する。"""
    return typing.cast(typing.Any, ctypes).WinDLL(name, use_last_error=True)


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


def _windows_secure_path(path: pathlib.Path, *, directory: bool) -> None:
    """継承ACLを除去し、現在user SIDだけへfull controlを付与する。"""
    sid = _windows_current_sid()
    permission = f"*{sid}:(OI)(CI)F" if directory else f"*{sid}:F"
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", permission],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ManagedTempError(f"Windows ACLを設定できない: {path}: {detail}")


def _windows_security_descriptor(path: pathlib.Path) -> tuple[str, str]:
    """Windows pathのowner SIDとDACLのSDDL表現を返す。"""
    advapi32 = _windows_dll("advapi32")
    kernel32 = _windows_dll("kernel32")
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    owner = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        1,
        0x00000001 | 0x00000004,
        ctypes.byref(owner),
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if result != 0:
        raise ManagedTempError(f"Windows security descriptorを取得できない: {path}: {result}")
    try:
        owner_text = ctypes.c_wchar_p(None)
        if not advapi32.ConvertSidToStringSidW(owner, ctypes.byref(owner_text)):
            raise ManagedTempError(f"Windows owner SIDを文字列化できない: {path}")
        try:
            owner_value = owner_text.value
            if owner_value is None:
                raise ManagedTempError(f"Windows owner SIDが空である: {path}")
            owner_sid = str(owner_value)
        finally:
            kernel32.LocalFree(ctypes.cast(owner_text, ctypes.c_void_p))
        sddl_text = ctypes.c_wchar_p(None)
        length = wintypes.DWORD(0)
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            1,
            0x00000004,
            ctypes.byref(sddl_text),
            ctypes.byref(length),
        ):
            raise ManagedTempError(f"Windows DACLを文字列化できない: {path}")
        try:
            sddl_value = sddl_text.value
            if sddl_value is None:
                raise ManagedTempError(f"Windows security descriptorが空である: {path}")
            return owner_sid, str(sddl_value)
        finally:
            kernel32.LocalFree(ctypes.cast(sddl_text, ctypes.c_void_p))
    finally:
        kernel32.LocalFree(descriptor)


def _validate_windows_security(path: pathlib.Path) -> None:
    sid = _windows_current_sid()
    owner, sddl = _windows_security_descriptor(path)
    trustees = [ace.split(";")[-1] for ace in re.findall(r"\(([^()]*)\)", sddl)]
    if owner != sid or not trustees or set(trustees) != {sid} or sddl.find(";FA;;;") < 0:
        raise ManagedTempError(f"Windows pathのownerまたはACLが不正: {path}")


def _windows_identity(path: pathlib.Path) -> tuple[int, int]:
    """Reparse pointを開かず、Windows handleからvolumeとfile IDを返す。"""

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("access_time", wintypes.FILETIME),
            ("write_time", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

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
    handle = create_file(
        str(path),
        0x0080,
        0x00000001 | 0x00000002,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ManagedTempError(f"Windows path handleを取得できない: {path}")
    try:
        info = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ManagedTempError(f"Windows path identityを取得できない: {path}")
        if info.attributes & _WINDOWS_REPARSE_POINT:
            raise ManagedTempError(f"Windows reparse pointは管理対象にできない: {path}")
        return info.volume, (info.file_index_high << 32) | info.file_index_low
    finally:
        kernel32.CloseHandle(handle)


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


def _record(path: pathlib.Path, nonce: str) -> dict[str, typing.Any]:
    device, inode = _path_identity(path)
    return {
        "schema_version": _SCHEMA_VERSION,
        "path": str(path),
        "platform": os.name,
        "owner": _owner_record(),
        "identity": [device, inode],
        "nonce": nonce,
    }


def _records_match(path: pathlib.Path, marker: dict[str, typing.Any], registry: dict[str, typing.Any]) -> bool:
    nonce = registry.get("nonce")
    return (
        isinstance(nonce, str)
        and re.fullmatch(r"[0-9a-f]{64}", nonce) is not None
        and marker == registry
        and registry == _record(path, nonce)
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


def create_managed_temp(prefix: str) -> pathlib.Path:
    """管理対象一時ディレクトリを作成し、絶対パスを返す。"""
    if _PREFIX_RE.fullmatch(prefix) is None:
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
        record = _record(path, nonce)
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
    _validate_windows_security(path)
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
    if consumed != _record(validated.path, validated.nonce):
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


def main(argv: list[str] | None = None) -> int:
    """CLI引数を解釈して管理対象一時ディレクトリを操作する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create", help="管理対象一時ディレクトリを作成する")
    create_parser.add_argument("--prefix", required=True)
    cleanup_parser = subparsers.add_parser("cleanup", help="管理対象一時ディレクトリを後始末する")
    cleanup_parser.add_argument("--path", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            print(create_managed_temp(args.prefix))
        else:
            cleanup_managed_temp(args.path)
        return 0
    except ManagedTempError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
