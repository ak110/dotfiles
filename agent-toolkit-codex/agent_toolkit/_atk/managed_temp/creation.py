# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
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
from typing import TYPE_CHECKING

from agent_toolkit._atk import help_text as _atk_help

if TYPE_CHECKING:
    from agent_toolkit._atk.managed_temp.cli import build_parser, dispatch, main
    from agent_toolkit._atk.managed_temp.inventory import (
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
        _QuarantineJudgement,
        _QuarantineState,
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
    from agent_toolkit._atk.managed_temp.registry import (
        _MARKER_NAME,
        _PREFIX_RE,
        _PREFIX_RULES,
        _SCHEMA_VERSION,
        _UTC_ISO8601_RE,
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
        MAX_AGE_DAYS,
        ManagedTempError,
        _awis_are_valid,
        _is_utc_iso8601,
        _load_marker,
        _load_private_json,
        _ManagedTempEntry,
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
        _ValidatedRoot,
        _ValidatedTemp,
        _WindowsApiError,
        _WindowsHandleOpenError,
        _write_marker,
        _write_private_json,
    )
    from agent_toolkit._atk.managed_temp.windows_security import (
        _AccessAllowedAce,
        _AceHeader,
        _Acl,
        _AclSizeInformation,
        _ByHandleFileInformation,
        _FileTime,
        _validate_windows_managed_root_security,
        _validate_windows_security,
        _windows_acl_buffer,
        _windows_current_sid,
        _windows_current_user_ace_is_valid,
        _windows_dll,
        _windows_equal_sids,
        _windows_error,
        _windows_external_writer_ace_is_valid,
        _windows_handle_open_error,
        _windows_identity,
        _windows_information_identity,
        _windows_managed_root_security_is_valid,
        _windows_path_handle,
        _windows_replace_security,
        _windows_secure_path,
        _windows_security_base_is_valid,
        _windows_security_descriptor,
        _windows_security_from_handle,
        _windows_security_update_handle,
        _windows_set_security,
        _windows_sid_bytes,
        _WindowsAce,
        _WindowsSecurity,
    )


from agent_toolkit._atk.managed_temp.registry import *  # noqa: F403
from agent_toolkit._atk.managed_temp.windows_security import *  # noqa: F403


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
    session_id: str | None = None,
) -> pathlib.Path:
    """管理対象一時ディレクトリを指定root直下へ作成し、絶対パスを返す。"""
    if not is_valid_prefix(prefix):
        raise _invalid_prefix_error(prefix)
    if not _awis_are_valid(list(awis)):
        raise ManagedTempError("awiはパス区切り文字と制御文字を含まない空でないファイル名で指定する")
    if session_id is not None:
        existing = list_managed_temp(session_id=session_id)
        if existing:
            return pathlib.Path(existing[0]["path"])
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
            session_id=session_id,
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
