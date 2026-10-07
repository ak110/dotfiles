"""managed-tempのディレクトリの真正性（パスの形、所有者と権限、マーカーと登録簿の一致）の検証。"""

from __future__ import annotations

import os
import pathlib
import stat
import typing

from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._atk.managed_temp.registry import (
    _MARKER_NAME,
    _load_marker,
    _load_private_json,
    _path_identity,
    _record_mismatch_error,
    _records_match,
    _registry_path,
    _validate_root,
    _ValidatedRoot,
    _ValidatedTemp,
)
from agent_toolkit._atk.managed_temp.windows_security import (
    _WINDOWS_REPARSE_POINT,
    _validate_windows_managed_root_security,
    _windows_identity,
)


def _validate_path_shape(path_arg: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    if not path_arg.is_absolute():
        raise ManagedTempError(f"pathは絶対パスで指定する: {path_arg}")
    path = pathlib.Path(os.path.abspath(path_arg))
    root = path.parent
    return root, path


def _validate_posix(path_arg: pathlib.Path | str, *, registry_fallback: bool = False) -> _ValidatedTemp:
    if os.name != "posix":
        raise ManagedTempError(
            "Windowsの所有者・ACL検証はWindows実機で確定する必要がある", next_action=ManagedTempError.UNSUPPORTED_NEXT_ACTION
        )
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
            raise ManagedTempError(
                f"管理対象rootが検証中に置換または変更された: {root}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        before = os.stat(path.name, dir_fd=root_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            raise ManagedTempError(
                f"管理対象が通常ディレクトリではない: {path}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        if before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o700:
            raise ManagedTempError(
                f"管理対象の所有者・権限またはroot直下の条件が不正: {path}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        target_descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_descriptor,
        )
        opened = os.fstat(target_descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ManagedTempError(f"管理対象が検証中に置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION)
        marker = _load_marker(target_descriptor, path)
        after_root = os.fstat(root_descriptor)
        after_root_state = _ValidatedRoot(
            after_root.st_dev,
            after_root.st_ino,
            after_root.st_uid,
            stat.S_IMODE(after_root.st_mode),
        )
        if after_root_state != root_state:
            raise ManagedTempError(
                f"管理対象rootが検証中に置換または変更された: {root}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        if (opened.st_dev, opened.st_ino) != _path_identity(path):
            raise ManagedTempError(f"管理対象が検証中に置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION)
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
        raise ManagedTempError(
            f"管理対象が通常ディレクトリではない: {path}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
        )
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
        raise ManagedTempError(f"管理対象が検証中に置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION)
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
    raise ManagedTempError(f"未対応platform: {os.name}", next_action=ManagedTempError.UNSUPPORTED_NEXT_ACTION)
