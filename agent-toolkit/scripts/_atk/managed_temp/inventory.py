# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
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
import ntpath
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
    from _atk.managed_temp.cli import build_parser, dispatch, main


from _atk.managed_temp.creation import *  # noqa: F403
from _atk.managed_temp.registry import *  # noqa: F403
from _atk.managed_temp.windows_security import *  # noqa: F403


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


type _TreeEntry = tuple[str, int, int] | tuple[str, int, int, str]

_WINDOWS_MOUNT_POINT_REPARSE_TAG = 0xA0000003
_WINDOWS_SYMLINK_REPARSE_TAG = 0xA000000C


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
            expected_tree = _tree_snapshot(quarantine)
            _unlink_windows_reparse_points(quarantine, expected_tree)
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


def _windows_stored_path(path: str) -> pathlib.PureWindowsPath:
    """Windows namespace接頭辞を除き、格納値を字句比較できる形にする。"""
    if path.startswith("\\\\?\\UNC\\"):
        path = f"\\\\{path[8:]}"
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return pathlib.PureWindowsPath(ntpath.normpath(path))


def _windows_reparse_entry(
    path: pathlib.Path,
    metadata: os.stat_result,
    managed_root: pathlib.Path,
) -> _TreeEntry:
    """受理できるreparse pointの種別、identity及び格納値を返す。"""
    tag = getattr(metadata, "st_reparse_tag", None)
    if tag == _WINDOWS_MOUNT_POINT_REPARSE_TAG:
        kind = "junction"
    elif tag == _WINDOWS_SYMLINK_REPARSE_TAG:
        kind = (
            "symlink-dir" if getattr(metadata, "st_file_attributes", 0) & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY else "symlink-file"
        )
    else:
        raise ManagedTempError(f"Windows reparse pointは後始末できない: {path}")
    try:
        stored_target = os.readlink(path)
        target = _windows_stored_path(stored_target)
        root = _windows_stored_path(str(managed_root))
    except (OSError, ValueError):
        raise ManagedTempError(f"Windows reparse pointは後始末できない: {path}") from None
    if not target.is_absolute() or not target.is_relative_to(root):
        raise ManagedTempError(f"Windows reparse pointは後始末できない: {path}")
    device, inode = _path_identity(path)
    return kind, device, inode, stored_target


def _tree_snapshot(root: pathlib.Path) -> dict[str, _TreeEntry]:
    """cleanup開始前のtree identityを取得し、安全なreparse pointだけを記録する。"""
    snapshot: dict[str, _TreeEntry] = {}
    pending = [root]
    while pending:
        parent = pending.pop()
        for entry in os.scandir(parent):
            path = pathlib.Path(entry.path)
            metadata = path.lstat()
            if os.name == "nt" and getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT:
                snapshot[str(path.relative_to(root))] = _windows_reparse_entry(path, metadata, root.parent)
                continue
            kind = "dir" if stat.S_ISDIR(metadata.st_mode) else "leaf"
            device, inode = _path_identity(path)
            relative = str(path.relative_to(root))
            snapshot[relative] = (kind, device, inode)
            if kind == "dir":
                pending.append(path)
    return snapshot


def _unlink_windows_reparse_points(root: pathlib.Path, expected_tree: dict[str, _TreeEntry]) -> None:
    """検証済みreparse pointを深い順に、リンク先を追跡せず解除する。"""
    links = [(relative, entry) for relative, entry in expected_tree.items() if len(entry) == 4]
    for relative, expected in sorted(links, key=lambda item: len(pathlib.PurePath(item[0]).parts), reverse=True):
        path = root / relative
        metadata = path.lstat()
        if _windows_reparse_entry(path, metadata, root.parent) != expected:
            raise ManagedTempError(f"Windows reparse pointが後始末中に置換された: {path}")
        if expected[0] in ("junction", "symlink-dir"):
            os.rmdir(path)
        else:
            os.unlink(path)


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
    expected_tree: dict[str, _TreeEntry],
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
    expected_tree: dict[str, _TreeEntry],
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
        _unlink_windows_reparse_points(quarantine, expected_tree)
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
