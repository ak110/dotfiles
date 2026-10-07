"""managed-tempのprefixの規則、作成rootと状態rootの解決、マーカーと登録簿の記録の形式と読み書き。"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
import typing
import unicodedata

import platformdirs

from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._atk.managed_temp.windows_security import (
    _WINDOWS_REPARSE_POINT,
    _validate_windows_security,
    _windows_current_sid,
    _windows_identity,
    _windows_managed_root_security_is_valid,
    _windows_secure_path,
    _windows_security_descriptor,
    _windows_sid_bytes,
)


def prefix_violation(prefix: str) -> str | None:
    """prefixが違反した最初の条件の説明を返す。違反が無ければNoneを返す。"""
    for description, satisfied in _PREFIX_RULES:
        if not satisfied(prefix):
            return description
    return None


def is_valid_prefix(prefix: str) -> bool:
    """prefixがmanaged-tempのディレクトリの命名規則に一致するか返す。"""
    return prefix_violation(prefix) is None


def _invalid_prefix_error(prefix: str) -> ManagedTempError:
    """違反した条件と拒否値を示すprefix検証エラーを返す。"""
    violation = prefix_violation(prefix)
    assert violation is not None
    return ManagedTempError(f"prefixが条件を満たしていません（{violation}）: {prefix}")


_MARKER_NAME = ".agent-toolkit-managed-temp.json"
_SCHEMA_VERSION = 6
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
"""managed-tempのディレクトリを自動削除するまでの日数。最終更新日時からの経過で判定する。"""


class _ManagedTempEntry(typing.TypedDict):
    """真正性検証済みのmanaged-tempのディレクトリを列挙する公開項目。"""

    path: str
    prefix: str | None
    created_at: str | None
    awis: list[str]
    session_id: str | None


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


def _temp_root_path() -> pathlib.Path:
    """rootを指定しない場合に使うOS別のユーザーキャッシュ領域を返す。"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise ManagedTempError("LOCALAPPDATAが設定されていない")
        return pathlib.Path(base) / "agent-toolkit" / "managed-temp"
    return pathlib.Path(platformdirs.user_cache_dir("agent-toolkit", appauthor=False)) / "managed-temp"


def _temp_root() -> pathlib.Path:
    root = _temp_root_path()
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            metadata = root.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise ManagedTempError(
                    f"一時ディレクトリのルートの所有者または種別が不正: {root}",
                    next_action=ManagedTempError.PERMISSION_NEXT_ACTION,
                )
            root.chmod(0o700)
            if stat.S_IMODE(root.stat().st_mode) != 0o700:
                raise ManagedTempError(
                    f"一時ディレクトリのルートの権限が不正: {root}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
                )
        elif os.name == "nt":
            _windows_secure_path(root, directory=True)
            _validate_windows_security(root)
        else:
            raise ManagedTempError(f"未対応platform: {os.name}", next_action=ManagedTempError.UNSUPPORTED_NEXT_ACTION)
    except OSError as error:
        raise ManagedTempError(f"一時ディレクトリのルートを準備できない: {root}: {error}") from error
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
            raise ManagedTempError(
                f"管理対象rootが通常ディレクトリではない: {root}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        if mode & (stat.S_ISUID | stat.S_ISGID):
            raise ManagedTempError(f"管理対象rootの特殊権限が不正: {root}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION)
        if (mode & stat.S_IWUSR) == 0 or (mode & stat.S_IXUSR) == 0:
            raise ManagedTempError(
                f"管理対象rootの所有者権限が不正: {root}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            if explicit or mode != 0o1777:
                raise ManagedTempError(
                    f"管理対象rootの権限が不安全: {root}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
                )
        elif metadata.st_uid != os.geteuid():
            raise ManagedTempError(
                f"管理対象rootの所有者が実行中のOSアカウントではない: {root}",
                next_action=ManagedTempError.PERMISSION_NEXT_ACTION,
            )
        current = _ValidatedRoot(metadata.st_dev, metadata.st_ino, metadata.st_uid, mode)
    elif os.name == "nt":
        try:
            metadata = root.lstat()
        except OSError as error:
            raise ManagedTempError(f"管理対象rootを検証できない: {root}: {error}") from error
        if not stat.S_ISDIR(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT:
            raise ManagedTempError(
                f"管理対象rootが通常ディレクトリではない: {root}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        identity = _windows_identity(root)
        current_sid = _windows_sid_bytes(_windows_current_sid())
        security = _windows_security_descriptor(root)
        if not security.directory or not security.dacl_present:
            raise ManagedTempError(
                f"Windows pathのownerまたはACLが不正: {root}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        if explicit and not _windows_managed_root_security_is_valid(security, current_sid):
            raise ManagedTempError(
                f"Windows pathのownerまたはACLが不正: {root}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        after_identity = _windows_identity(root)
        after_security = _windows_security_descriptor(root)
        if after_identity != identity:
            raise ManagedTempError(
                f"管理対象rootが検証中に置換された: {root}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        if after_security != security:
            raise ManagedTempError(
                f"管理対象rootが検証中に変更された: {root}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        current = _ValidatedRoot(after_identity[0], after_identity[1], None, None, after_security)
    else:
        raise ManagedTempError(f"未対応platform: {os.name}", next_action=ManagedTempError.UNSUPPORTED_NEXT_ACTION)
    if expected is not None and current != expected:
        raise ManagedTempError(
            f"管理対象rootが検証中に置換または変更された: {root}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
        )
    return current


def _owner_record() -> dict[str, str | int]:
    if os.name == "posix":
        return {"kind": "uid", "id": os.geteuid()}
    if os.name == "nt":
        return {"kind": "sid", "id": _windows_current_sid()}
    raise ManagedTempError(f"未対応platform: {os.name}", next_action=ManagedTempError.UNSUPPORTED_NEXT_ACTION)


def _state_root_path() -> pathlib.Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise ManagedTempError("LOCALAPPDATAが設定されていない")
        return pathlib.Path(base) / "agent-toolkit" / "managed-temp"
    base = os.environ.get("XDG_STATE_HOME")
    # 相対パスのXDG_STATE_HOMEは作業ディレクトリごとに別の場所を指すため、XDG Base Directory仕様どおり無視する。
    # `atk config get state_dir`と`agents_server`の状態ディレクトリも同じ扱いにしている。
    state_home = pathlib.Path(base) if base and pathlib.Path(base).is_absolute() else pathlib.Path.home() / ".local" / "state"
    return state_home / "agent-toolkit" / "managed-temp"


def _state_root() -> pathlib.Path:
    root = _state_root_path()
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            metadata = root.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise ManagedTempError(
                    f"外部状態ディレクトリの所有者または種別が不正: {root}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
                )
            root.chmod(0o700)
            if stat.S_IMODE(root.stat().st_mode) != 0o700:
                raise ManagedTempError(
                    f"外部状態ディレクトリの権限が不正: {root}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
                )
        elif os.name == "nt":
            _windows_secure_path(root, directory=True)
            _validate_windows_security(root)
        else:
            raise ManagedTempError(f"未対応platform: {os.name}", next_action=ManagedTempError.UNSUPPORTED_NEXT_ACTION)
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
            raise ManagedTempError(
                f"外部状態が通常ファイルではない: {path}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        if os.name == "posix":
            if before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600:
                raise ManagedTempError(
                    f"外部状態の所有者または権限が不正: {path}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
                )
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "r", encoding="utf-8") as source:
                opened = os.fstat(source.fileno())
                value = json.load(source)
            after = path.lstat()
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or (
                after.st_dev,
                after.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                raise ManagedTempError(
                    f"外部状態が検証中に置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                )
        else:
            _validate_windows_security(path)
            identity = _windows_identity(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            if _windows_identity(path) != identity:
                raise ManagedTempError(
                    f"外部状態が検証中に置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                )
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
    session_id: str | None = None,
    identity: tuple[int, int] | None = None,
) -> dict[str, typing.Any]:
    record = _record_base(path, nonce, identity=identity)
    record.update(
        {
            "schema_version": _SCHEMA_VERSION,
            "prefix": prefix,
            "created_at": created_at,
            "awis": list(awis),
            "session_id": session_id,
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
    expected_identity = identity
    if identity is not None:
        try:
            current_identity = _path_identity(path)
        except (OSError, ManagedTempError):
            return False
        if os.name == "posix":
            if current_identity[1] != identity[1]:
                return False
            recorded_identity = registry.get("identity")
            if (
                not isinstance(recorded_identity, list)
                or len(recorded_identity) != 2
                or any(not isinstance(value, int) or isinstance(value, bool) for value in recorded_identity)
            ):
                return False
            # POSIXのdevice番号は再起動で変わるため、保存済み値を期待レコードへ使う。
            expected_identity = (recorded_identity[0], identity[1])
        elif current_identity != identity:
            return False
    nonce = registry.get("nonce")
    schema_version = registry.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        return False
    if schema_version == 1:
        expected = _record_base(path, typing.cast(str, nonce), identity=expected_identity)
        expected["schema_version"] = schema_version
    elif schema_version == 2:
        prefix = registry.get("prefix")
        created_at = registry.get("created_at")
        if not isinstance(prefix, str) or not is_valid_prefix(prefix) or not _is_utc_iso8601(created_at):
            return False
        expected = _record_base(path, typing.cast(str, nonce), identity=expected_identity)
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
            identity=expected_identity,
        )
        expected.pop("session_id")
        expected["schema_version"] = schema_version
        if schema_version == 3:
            expected["feedbacks"] = expected.pop("awis")
    elif schema_version in (5, 6):
        prefix = registry.get("prefix")
        created_at = registry.get("created_at")
        awis = registry.get("awis")
        session_id = registry.get("session_id")
        if (
            not isinstance(prefix, str)
            or not is_valid_prefix(prefix)
            or not _is_utc_iso8601(created_at)
            or not _awis_are_valid(awis)
            or not (session_id is None or isinstance(session_id, str))
        ):
            return False
        expected = _record(
            path,
            typing.cast(str, nonce),
            prefix=prefix,
            created_at=typing.cast(str, created_at),
            awis=tuple(typing.cast(list[str], awis)),
            session_id=session_id,
            identity=expected_identity,
        )
        if schema_version == 5:
            expected["schema_version"] = 5
    else:
        return False
    return (
        isinstance(nonce, str)
        and re.fullmatch(r"[0-9a-f]{64}", nonce) is not None
        and marker == registry
        and {key: value for key, value in registry.items() if key != "session_owner"} == expected
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
            raise ManagedTempError(
                f"管理情報が通常ファイルではない: {path / _MARKER_NAME}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        if before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600:
            raise ManagedTempError(
                f"管理情報の所有者または権限が不正: {path / _MARKER_NAME}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        descriptor = os.open(_MARKER_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ManagedTempError(
                f"管理情報が検証中に置換された: {path / _MARKER_NAME}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
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
        raise ManagedTempError(
            f"管理情報が検証中に置換された: {path / _MARKER_NAME}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
        )
    if not isinstance(value, dict):
        raise ManagedTempError(f"管理情報はJSON objectである必要がある: {path / _MARKER_NAME}")
    return value
