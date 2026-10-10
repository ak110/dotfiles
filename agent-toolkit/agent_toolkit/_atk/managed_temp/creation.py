"""managed-tempのディレクトリの作成（マーカーと登録簿の記録、所有者と権限の設定、失敗時の除去）。"""

from __future__ import annotations

# 他の処理では不要な依存を、使用時まで遅延する。旧Pythonでは通常のimportとなる。
__lazy_modules__ = {"agent_toolkit._atk.managed_temp.windows_security"}

import contextlib
import datetime
import os
import pathlib
import secrets
import stat
import tempfile

from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._atk.managed_temp.inventory import list_managed_temp
from agent_toolkit._atk.managed_temp.registry import (
    _MARKER_NAME,
    _awis_are_valid,
    _invalid_prefix_error,
    _path_identity,
    _record,
    _registry_path,
    _temp_root,
    _validate_root,
    _write_marker,
    _write_private_json,
    is_valid_prefix,
)
from agent_toolkit._atk.managed_temp.validation import _validate_posix, _validate_windows, validate_managed_temp
from agent_toolkit._atk.managed_temp.windows_security import _windows_identity, _windows_secure_path

SESSION_TEMP_PREFIX = "session"
"""SessionStartが会話ごとに作成するセッションのmanaged-tempの接頭辞。

フックとagents_serverの双方が同じ領域を解決するため、両者より前の層のこのモジュールが持つ。
"""


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
                raise ManagedTempError(
                    f"管理対象rootが作成中に置換された: {root_path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                )
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
                raise ManagedTempError(
                    f"管理対象が作成中に置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                )
            os.fchmod(target_descriptor, 0o700)
            _validate_root(root_path, explicit=explicit_root, expected=validated_root)
        elif os.name == "nt":
            _windows_secure_path(path, directory=True)
            created_identity = _windows_identity(path)
        else:
            raise ManagedTempError(f"未対応platform: {os.name}", next_action=ManagedTempError.UNSUPPORTED_NEXT_ACTION)
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


def create_session_temp(prefix: str, session_root: pathlib.Path | str) -> pathlib.Path:
    """登録済みのセッションのmanaged-temp直下へ、個別登録を持たない作業ディレクトリを作成する。"""
    if not is_valid_prefix(prefix):
        raise _invalid_prefix_error(prefix)
    root_argument = pathlib.Path(session_root)
    if os.name == "posix":
        validated = _validate_posix(root_argument)
    elif os.name == "nt":
        validated = _validate_windows(root_argument)
    else:
        raise ManagedTempError(f"未対応platform: {os.name}", next_action=ManagedTempError.UNSUPPORTED_NEXT_ACTION)
    if not isinstance(validated.record.get("session_id"), str) or not validated.record["session_id"]:
        raise ManagedTempError(f"session_rootはセッション識別子を持つ管理対象である必要がある: {validated.path}")
    path: pathlib.Path | None = None
    root_descriptor: int | None = None
    created_identity: tuple[int, int] | None = None
    try:
        if os.name == "posix":
            root_descriptor = os.open(validated.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened_root = os.fstat(root_descriptor)
            if (opened_root.st_dev, opened_root.st_ino) != (validated.device, validated.inode):
                raise ManagedTempError(
                    f"session_rootが作成中に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                )
        path = pathlib.Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=validated.path))
        if os.name == "posix":
            assert root_descriptor is not None
            created = os.stat(path.name, dir_fd=root_descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(created.st_mode):
                raise ManagedTempError(
                    f"セッション内管理対象が通常ディレクトリではない: {path}",
                    next_action=ManagedTempError.PERMISSION_NEXT_ACTION,
                )
            created_identity = (created.st_dev, created.st_ino)
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_descriptor,
            )
            try:
                opened = os.fstat(descriptor)
                if created_identity != (opened.st_dev, opened.st_ino):
                    raise ManagedTempError(
                        f"セッション内管理対象が作成中に置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                    )
                os.fchmod(descriptor, 0o700)
            finally:
                os.close(descriptor)
        elif os.name == "nt":
            _windows_secure_path(path, directory=True)
            created_identity = _windows_identity(path)
        else:
            raise ManagedTempError(f"未対応platform: {os.name}", next_action=ManagedTempError.UNSUPPORTED_NEXT_ACTION)
        current = _validate_posix(validated.path) if os.name == "posix" else _validate_windows(validated.path)
        if (current.device, current.inode) != (validated.device, validated.inode):
            raise ManagedTempError(
                f"session_rootが作成中に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        return path
    except (ManagedTempError, OSError) as error:
        if path is not None:
            _remove_created_target(
                path,
                root_descriptor=root_descriptor,
                target_descriptor=None,
                created_identity=created_identity,
            )
        if isinstance(error, ManagedTempError):
            raise
        raise ManagedTempError(f"セッション内管理対象を作成できない: {error}") from error
    finally:
        if root_descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(root_descriptor)
