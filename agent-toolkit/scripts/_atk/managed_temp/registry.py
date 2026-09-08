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
    from _atk.managed_temp.windows_security import (
        _AccessAllowedAce,
        _AceHeader,
        _Acl,
        _AclSizeInformation,
        _ByHandleFileInformation,
        _FileTime,
        _WindowsAce,
        _WindowsSecurity,
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
