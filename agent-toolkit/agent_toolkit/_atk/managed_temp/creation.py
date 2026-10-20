"""managed-tempのディレクトリの作成（マーカーと登録簿の記録、所有者と権限の設定、失敗時の除去）。"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import os
import pathlib
import secrets
import stat
import tempfile

from agent_toolkit._atk.managed_temp import permissions
from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._atk.managed_temp.inventory import list_managed_temp
from agent_toolkit._atk.managed_temp.registry import (
    _MARKER_NAME,
    _awis_are_valid,
    _invalid_prefix_error,
    _load_private_json,
    _path_identity,
    _record,
    _registry_path,
    _state_root,
    _temp_root,
    _validate_root,
    _write_marker,
    _write_private_json,
    is_valid_prefix,
)
from agent_toolkit._atk.managed_temp.validation import _validate_posix, _validate_windows, validate_managed_temp
from agent_toolkit._common import file_lock

# 他の処理で使わない依存は、使用時まで読み込まない。
lazy from agent_toolkit._atk.managed_temp.windows_security import _windows_identity, _windows_secure_path

SESSION_TEMP_PREFIX = "session"
"""会話ごとに再利用するセッションのmanaged-tempの接頭辞。

フックとagents_serverの双方が同じ領域を求めるため、両者が読み込む下位の層にあるこのモジュールで定義する。
"""


def _remove_created_target(
    path: pathlib.Path,
    *,
    root_descriptor: int | None,
    target_descriptor: int | None,
    created_identity: tuple[int, int] | None,
) -> None:
    """作成処理が作成した空の対象だけを、保持したrootのファイル記述子を基準に除去する。"""
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
    except OSError, ManagedTempError:
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
        return resolve_session_temp(session_id, prefix=prefix, root=root, awis=awis)
    return _create_managed_temp(prefix, root, awis, session_id)


def resolve_session_temp(
    session_id: str,
    *,
    prefix: str = SESSION_TEMP_PREFIX,
    root: pathlib.Path | str | None = None,
    awis: tuple[str, ...] = (),
) -> pathlib.Path:
    """会話別の参照を検証して親領域を解決し、同時作成を同じ領域へ収束させる。"""
    path = _resolve_session_temp(session_id, prefix=prefix, root=root, awis=awis, create=True)
    assert path is not None
    return path


def find_session_temp(session_id: str) -> pathlib.Path | None:
    """既存の会話領域を直接解決し、領域を作成せずに返す。"""
    return _resolve_session_temp(session_id, prefix=SESSION_TEMP_PREFIX, root=None, awis=(), create=False)


def _resolve_session_temp(
    session_id: str,
    *,
    prefix: str,
    root: pathlib.Path | str | None,
    awis: tuple[str, ...],
    create: bool,
) -> pathlib.Path | None:
    directory = _state_root() / ".sessions"
    directory.mkdir(mode=0o700, exist_ok=True)
    key = hashlib.sha256(session_id.encode()).hexdigest()
    reference = directory / key
    with (directory / f"{key}.lock").open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle)
        try:
            try:
                record = _load_private_json(reference)
                path = pathlib.Path(record["path"])
                validated = _validate_posix(path) if os.name == "posix" else _validate_windows(path)
                if validated.record == record and record.get("session_id") == session_id:
                    return path
            except KeyError, TypeError, OSError, ManagedTempError:
                pass
            existing = list_managed_temp(session_id=session_id)
            if not existing and not create:
                return None
            path = pathlib.Path(existing[0]["path"]) if existing else _create_managed_temp(prefix, root, awis, session_id)
            record = _load_private_json(_registry_path(path))
            temporary = directory / f"{key}.{secrets.token_hex(8)}"
            try:
                _write_private_json(temporary, record)
                os.replace(temporary, reference)
            finally:
                temporary.unlink(missing_ok=True)
            return path
        finally:
            file_lock.release_lock(handle)


def create_output_temp(prefix: str) -> pathlib.Path:
    """会話が分かる保存物をセッションの子へ、端末の保存物を独立領域へ作成する。"""
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")
    if session_id:
        return create_session_temp(prefix, resolve_session_temp(session_id))
    return create_managed_temp(prefix)


def _create_managed_temp(
    prefix: str,
    root: pathlib.Path | str | None,
    awis: tuple[str, ...],
    session_id: str | None,
) -> pathlib.Path:
    with contextlib.ExitStack() as stack:
        return _create_managed_temp_scoped(prefix, root, awis, session_id, stack)


def _create_managed_temp_scoped(
    prefix: str,
    root: pathlib.Path | str | None,
    awis: tuple[str, ...],
    session_id: str | None,
    stack: contextlib.ExitStack,
) -> pathlib.Path:
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
            root_descriptor = stack.enter_context(
                permissions.descriptor(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            )
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
            target_descriptor = stack.enter_context(
                permissions.descriptor(
                    path.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=root_descriptor,
                )
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


def create_session_temp(prefix: str, session_root: pathlib.Path | str) -> pathlib.Path:
    """登録済みのセッションのmanaged-temp直下へ、個別登録を持たない作業ディレクトリを作成する。"""
    with contextlib.ExitStack() as stack:
        return _create_session_temp_scoped(prefix, session_root, stack)


def _create_session_temp_scoped(prefix: str, session_root: pathlib.Path | str, stack: contextlib.ExitStack) -> pathlib.Path:
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
            root_descriptor = stack.enter_context(
                permissions.descriptor(validated.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            )
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
            with permissions.descriptor(
                path.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_descriptor,
            ) as descriptor:
                opened = os.fstat(descriptor)
                if created_identity != (opened.st_dev, opened.st_ino):
                    raise ManagedTempError(
                        f"セッション内管理対象が作成中に置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                    )
                os.fchmod(descriptor, 0o700)
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
