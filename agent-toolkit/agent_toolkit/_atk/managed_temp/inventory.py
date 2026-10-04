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
from typing import TYPE_CHECKING

from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import outcome as _outcome

if TYPE_CHECKING:
    from agent_toolkit._atk.managed_temp.cli import build_parser, dispatch, main
    from agent_toolkit._atk.managed_temp.creation import (
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
        _windows_reparse_identity,
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


from agent_toolkit._atk.managed_temp.creation import *  # noqa: F403
from agent_toolkit._atk.managed_temp.registry import *  # noqa: F403
from agent_toolkit._atk.managed_temp.windows_security import *  # noqa: F403


def is_missing_registered_temp(path_arg: pathlib.Path | str) -> bool:
    """登録だけが残り実体を失った管理対象であるかを判定する。

    真を返す条件は、登録されたroot直下の絶対パスであること、そのpathを記録した登録ファイルが
    存在すること、実体が存在しないことの3つをすべて満たす場合とする。
    実体を失った領域には使用中の主体が存在しないため、この条件に限り
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
    """対象が存在しない場合だけNoneを返し、存在を確認できない場合は例外を送出する。"""
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
        return _QuarantineJudgement(_QuarantineState.UNVERIFIABLE, reason=f"隔離途中の対象の状態を確認できない: {error}")
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
            "管理情報を保持したため、原因を除去した後に同じcleanupを再試行できる",
            next_action=ManagedTempError.RETRYABLE_NEXT_ACTION,
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
                    raise ManagedTempError(
                        f"隔離先が再開時に置換された: {quarantine}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                    )
                _clear_directory(descriptor)
            finally:
                os.close(descriptor)
            os.rmdir(quarantine)
        else:
            expected_tree = _tree_snapshot(quarantine)
            _unlink_windows_reparse_points(quarantine, expected_tree)
            _remove_windows_quarantine(quarantine, expected_tree)
    except OSError as error:
        raise ManagedTempError(
            f"中断した後始末の隔離先を後始末できない: {quarantine}: {error}。"
            "管理情報と隔離先を保持したため、原因を除去した後に同じcleanupを再試行できる",
            next_action=ManagedTempError.RETRYABLE_NEXT_ACTION,
        ) from error


def _unregistered_candidates(prefix: str | None) -> list[pathlib.Path]:
    """一時rootを指定しない場合に使う場所の直下で、マーカーだけが残る管理対象の絶対パスを返す。

    本関数は`atk`の`managed-temp`以外の全サブコマンドの前段から呼ばれ、対話シェルの起動ごとに
    発火する場合がある。一時ディレクトリ直下の項目ごとに外部状態ディレクトリを解決すると、
    その項目数に比例した待ち時間が対話シェルの起動へ生じる。判定を追加する場合も、
    項目の種別とマーカーの有無で候補を限定した後に外部状態を解決する評価順序を維持する。
    """
    root = _temp_root()
    with os.scandir(root) as entries:
        names = sorted(entry.name for entry in entries if entry.is_dir(follow_symlinks=False))
    candidates: list[pathlib.Path] = []
    state_root: pathlib.Path | None = None
    for name in names:
        if prefix is not None and not name.startswith(f"{prefix}-"):
            continue
        child = root / name
        if not os.path.lexists(child / _MARKER_NAME):
            continue
        if state_root is None:
            state_root = _state_root()
        if os.path.lexists(state_root / _registry_name(child)):
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
    """一時rootを指定しない場合に使う場所の直下で、マーカーだけが残る管理対象を報告する。"""
    try:
        candidates = _unregistered_candidates(prefix)
    except (OSError, ManagedTempError) as error:
        _outcome.report_warning(
            f"登録を持たない管理対象を探索できない: {error}",
            next_action="表示された原因を除去して`atk managed-temp list`を再実行する。解消しない場合はユーザーへ報告する",
        )
        return
    for child in candidates:
        registry_path = _registry_path(child)
        if _consuming_registry_path(registry_path) is not None:
            _outcome.report_warning(
                f"後始末が中断した可能性がある管理対象がある: {child}",
                next_action=f"回収する場合は atk managed-temp cleanup --path {child} を実行する",
            )
            continue
        if not _marker_recovery_is_accepted(child):
            _outcome.report_warning(
                f"マーカーから登録を復元できない管理対象がある: {child}",
                next_action=f"回収する場合は atk managed-temp cleanup --path {child} --force-remove を実行する",
            )
            continue
        _outcome.report_warning(
            f"登録を持たない管理対象がある: {child}",
            next_action=f"回収する場合は atk managed-temp cleanup --path {child} --recover-registry を実行する",
        )


def list_managed_temp(
    prefix: str | None = None,
    *,
    session_id: str | None = None,
    report_recovery_candidates: bool = False,
) -> list[_ManagedTempEntry]:
    """真正性検証を通過した管理対象を作成時刻順で返す。

    実体に依存しない検証（`path`欄の型と登録ファイル名との対応）と`prefix`による限定を通過した
    登録のうち、実体の消滅を確定できたものは登録ファイルごと削除し、削除を警告として報告する。
    実体を失った登録にはその領域を使用中の主体が存在しないため、登録ファイルの削除が
    利用中の管理対象へ影響しない。消滅を確定できない登録は削除せず列挙対象から外す。
    記録時と列挙時で一時領域の設定が異なる登録も回収対象へ含めるため、現在の一時領域直下で
    あることは条件としない。
    `report_recovery_candidates`が真の場合だけ、削除せず保持した登録と、一時rootを指定しない場合に使う場所の直下で
    登録を持たない管理対象を警告として報告する。回収候補の報告を`atk managed-temp list`に
    限ることで、全コマンドの起動時に実行する掃引がユーザーの操作と無関係な警告を出力しない。
    """
    if prefix is not None and not is_valid_prefix(prefix):
        raise _invalid_prefix_error(prefix)
    entries: list[_ManagedTempEntry] = []
    for registry_path in _state_root().glob("*.json"):
        entry = _load_listed_entry(
            registry_path, prefix=prefix, session_id=session_id, report_recovery_candidates=report_recovery_candidates
        )
        if entry is not None:
            entries.append(entry)
    if report_recovery_candidates:
        _report_unregistered_candidates(prefix)
    return sorted(entries, key=lambda item: (item["created_at"] is not None, item["created_at"] or "", item["path"] or ""))


def _load_listed_entry(
    registry_path: pathlib.Path,
    *,
    prefix: str | None,
    session_id: str | None,
    report_recovery_candidates: bool,
) -> _ManagedTempEntry | None:
    """1件の登録を`list_managed_temp`と同じ条件で検証し、列挙対象なら項目を返す。"""
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
        item_prefix = record.get("prefix") if schema_version in (2, 3, 4, 5, 6) else None
        created_at = record.get("created_at") if schema_version in (2, 3, 4, 5, 6) else None
        awis = record.get("awis") if schema_version in (4, 5, 6) else record.get("feedbacks") if schema_version == 3 else []
        item_session_id = record.get("session_id") if schema_version in (5, 6) else None
        if (
            not (item_prefix is None or isinstance(item_prefix, str))
            or not (created_at is None or isinstance(created_at, str))
            or not _awis_are_valid(awis)
            or not (item_session_id is None or isinstance(item_session_id, str))
        ):
            raise ManagedTempError("管理情報のprefix、created_at、awisまたはsession_idが不正")
        if prefix is not None and item_prefix != prefix:
            return None
        if session_id is not None and item_session_id != session_id:
            return None
        if not os.path.lexists(path):
            if _entity_absence_is_confirmed(record, path):
                registry_path.unlink(missing_ok=True)
                _outcome.report_warning(
                    f"実体が失われた管理対象の登録を回収した: {path}", next_action="対応不要（処理は継続した）"
                )
            elif report_recovery_candidates:
                _outcome.report_warning(
                    f"実体へ到達できないため登録を保持した: {path}",
                    next_action="同じ絶対パスへ到達できる実行文脈で atk managed-temp list を実行すると回収する",
                )
            return None
        validate_managed_temp(path)
        return {
            "path": str(path),
            "prefix": item_prefix,
            "created_at": created_at,
            "awis": typing.cast(list[str], awis),
            "session_id": item_session_id,
        }
    except (KeyError, OSError, ValueError, ManagedTempError) as error:
        if report_recovery_candidates:
            recorded_target = f": {recorded_path}" if isinstance(recorded_path, str) else ""
            recovery = (
                f"後始末する場合は atk managed-temp cleanup --path {recorded_path} を実行する。"
                "実体を削除した場合は、次回の atk managed-temp list で登録を回収する"
                if isinstance(recorded_path, str)
                else f"登録ファイル{registry_path}の内容を確認し、自分で直せない場合はユーザーへ報告する"
            )
            _outcome.report_warning(f"管理対象を列挙できない: {registry_path}{recorded_target}: {error}", next_action=recovery)
    return None


def _sweep_cleanup_completed_elsewhere(
    path: pathlib.Path,
    registry_path: pathlib.Path,
    nonce: str | None,
) -> bool:
    """別実行が実体と全ての後始末状態を削除済みである場合だけ真を返す。"""
    try:
        if os.path.lexists(path) or os.path.lexists(registry_path):
            return False
        if next(registry_path.parent.glob(f"{registry_path.name}.consuming-*"), None) is not None:
            return False
        if nonce is None:
            return next(path.parent.glob(".agent-toolkit-cleanup-*"), None) is None
        return not os.path.lexists(path.parent / f".agent-toolkit-cleanup-{nonce}")
    except OSError:
        return False


def _cutoff_ns(now: datetime.datetime, max_age_days: int) -> int:
    """`now`から`max_age_days`を遡った時刻をエポックからのナノ秒で返す。"""
    reference = now if now.tzinfo is not None else now.astimezone()
    cutoff = (reference - datetime.timedelta(days=max_age_days)).astimezone(datetime.UTC)
    elapsed = cutoff - datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
    return (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000_000 + elapsed.microseconds * 1_000


def _latest_update_and_git_paths(path: pathlib.Path) -> tuple[int, list[pathlib.Path]]:
    """領域自身と配下の最終更新時刻（ナノ秒）と、配下にある`.git`のパスを返す。

    作成時刻ではなく配下を含む最終更新を使うのは、中断後に再開した領域を古いと誤判定しないためである。
    """
    latest_mtime_ns = path.stat().st_mtime_ns
    git_paths: list[pathlib.Path] = []
    pending = [path]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                metadata = child.stat(follow_symlinks=False)
                latest_mtime_ns = max(latest_mtime_ns, metadata.st_mtime_ns)
                if child.name == ".git":
                    git_paths.append(pathlib.Path(child.path))
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(pathlib.Path(child.path))
    return latest_mtime_ns, git_paths


def _is_registered_git_worktree(git_path: pathlib.Path) -> bool:
    """`.git`ファイルの`gitdir:`が指すリポジトリ側の管理ディレクトリが実在するかを返す。

    git worktreeの`.git`はファイルで、リポジトリ側の`worktrees/<名前>`を指す。指す先が残る間は
    元のリポジトリの`git worktree list`に登録が残っており、そのリポジトリの作業が使用中である。
    ディレクトリの`.git`（単独の複製）は他から参照されないため、ここでは使用中と扱わない。
    """
    if not git_path.is_file() or git_path.is_symlink():
        return False
    try:
        first_line = git_path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeDecodeError, IndexError):
        # 内容を読めない場合は使用中かを判定できないため、削除せず残す側へ倒す。
        return True
    prefix = "gitdir:"
    if not first_line.startswith(prefix):
        return True
    target = pathlib.Path(first_line[len(prefix) :].strip())
    if not target.is_absolute():
        target = git_path.parent / target
    return target.is_dir()


_SWEEP_SCHEDULE_NAME = ".sweep-schedule"
"""掃引の期限判定記録のファイル名。登録ファイルの`*.json`と区別するため拡張子を付けない。"""
_SWEEP_SCHEDULE_VERSION = 1

_ScheduledRegistered = tuple[str, int, int]
"""登録済み候補の記録。管理対象のpath、記録時の領域自身の最終更新、観測した配下を含む最終更新（ナノ秒）。"""
_ScheduledUnregistered = tuple[int, int]
"""登録を失った候補の記録。記録時の領域自身の最終更新と、観測した配下を含む最終更新（ナノ秒）。"""


class SweepResult(typing.NamedTuple):
    """`sweep_managed_temp`の結果。"""

    deleted: list[pathlib.Path]
    """自動削除したパス。"""
    stale_unregistered: tuple[pathlib.Path, ...]
    """掃引の後も残った、最終更新から期限を超えた登録を持たない管理対象。"""
    unregistered_error: ManagedTempError | OSError | None
    """登録を持たない管理対象を探索できなかった場合の原因。"""


class _SweepSchedule(typing.NamedTuple):
    registered: dict[str, _ScheduledRegistered]
    unregistered: dict[str, _ScheduledUnregistered]


def _is_nanoseconds(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _load_sweep_schedule(state_root: pathlib.Path, temp_root: pathlib.Path, max_age_days: int) -> _SweepSchedule | None:
    """期限判定記録を読む。欠落・破損・前提の不一致では`None`を返し、呼び出し元は全件の掃引へ戻る。"""
    path = state_root / _SWEEP_SCHEDULE_NAME
    if not os.path.lexists(path):
        return None
    try:
        value = _load_private_json(path)
    except ManagedTempError:
        return None
    if (
        value.get("version") != _SWEEP_SCHEDULE_VERSION
        or value.get("state_root") != str(state_root)
        or value.get("temp_root") != str(temp_root)
        or value.get("max_age_days") != max_age_days
    ):
        return None
    raw_registered = value.get("registered")
    raw_unregistered = value.get("unregistered")
    if not isinstance(raw_registered, dict) or not isinstance(raw_unregistered, dict):
        return None
    registered: dict[str, _ScheduledRegistered] = {}
    for name, item in raw_registered.items():
        if not (
            isinstance(item, list)
            and len(item) == 3
            and isinstance(item[0], str)
            and _is_nanoseconds(item[1])
            and _is_nanoseconds(item[2])
        ):
            return None
        registered[name] = (item[0], item[1], item[2])
    unregistered: dict[str, _ScheduledUnregistered] = {}
    for name, item in raw_unregistered.items():
        if not (isinstance(item, list) and len(item) == 2 and _is_nanoseconds(item[0]) and _is_nanoseconds(item[1])):
            return None
        unregistered[name] = (item[0], item[1])
    return _SweepSchedule(registered, unregistered)


def _save_sweep_schedule(
    state_root: pathlib.Path, temp_root: pathlib.Path, max_age_days: int, schedule: _SweepSchedule
) -> None:
    """期限判定記録を置き換える。書けない場合は次回の起動が全件の掃引へ戻るだけなので、失敗を報告しない。"""
    path = state_root / _SWEEP_SCHEDULE_NAME
    temporary = state_root / f"{_SWEEP_SCHEDULE_NAME}.{secrets.token_hex(8)}.tmp"
    value = {
        "version": _SWEEP_SCHEDULE_VERSION,
        "state_root": str(state_root),
        "temp_root": str(temp_root),
        "max_age_days": max_age_days,
        "registered": {name: list(item) for name, item in schedule.registered.items()},
        "unregistered": {name: list(item) for name, item in schedule.unregistered.items()},
    }
    try:
        _write_private_json(temporary, value)
        os.replace(temporary, path)
    except (OSError, ManagedTempError):
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _scheduled_item_is_current(path: pathlib.Path, top_mtime_ns: int, latest_mtime_ns: int, cutoff_ns: int) -> bool:
    """記録した候補が期限前で、記録後に領域自身が変わっていない場合だけ真を返す。

    配下の更新は最終更新を後ろへ移すだけなので、記録した最終更新が期限前なら実際の最終更新も期限前である。
    領域自身の最終更新が変わった場合と消えた場合は、記録を信用せず従来の検証へ戻す。
    """
    if latest_mtime_ns < cutoff_ns:
        return False
    try:
        return os.stat(path).st_mtime_ns == top_mtime_ns
    except OSError:
        return False


def _registry_names(state_root: pathlib.Path) -> set[str]:
    with os.scandir(state_root) as entries:
        return {entry.name for entry in entries if entry.name.endswith(".json")}


def sweep_expired_managed_temp(
    *,
    now: datetime.datetime,
    max_age_days: int = MAX_AGE_DAYS,
) -> list[pathlib.Path]:
    """最終更新から`max_age_days`を超えたmanaged-tempのディレクトリを削除し、削除したパスを返す。"""
    return sweep_managed_temp(now=now, max_age_days=max_age_days).deleted


def sweep_managed_temp(
    *,
    now: datetime.datetime,
    max_age_days: int = MAX_AGE_DAYS,
) -> SweepResult:
    """最終更新から`max_age_days`を超えたmanaged-tempのディレクトリを削除し、削除結果と残存候補を返す。

    登録済み領域は`.git`を含むものを除いて削除する。続いて、一時rootを指定しない場合に使う場所の直下で
    登録を失った領域（マーカーだけを持つ領域）も、他の作業が使用中と判定できるもの以外を削除する。
    一時rootを指定しない場合に使う場所は`atk`だけが作成する場所であり、登録を失った領域を所有の検証なしに回収しても
    ユーザーのディレクトリを巻き込まないというユーザーの判断（2026年10月2日）に基づく。
    使用中の判定は、配下を含む最終更新が`max_age_days`以内であることと、git worktreeとして
    登録が残る`.git`を含むことの2つとする。

    本関数は`atk`の共通起動から毎回呼ばれる。期限前の候補を起動ごとに検証しないため、候補ごとに
    観測した最終更新を外部状態ディレクトリの期限判定記録へ残し、記録が期限前で領域自身が変わっていない
    候補だけを検証から外す。新規の候補、期限到来、領域自身の変化と消失、記録の欠落・破損では
    従来の検証と削除判定を行う。記録は検証を省く範囲を決めるだけで、削除は従来の判定を通った候補に限る。
    """
    cutoff_ns = _cutoff_ns(now, max_age_days)
    state_root = _state_root()
    temp_root: pathlib.Path | None = None
    temp_root_error: ManagedTempError | OSError | None = None
    try:
        temp_root = _temp_root()
    except (OSError, ManagedTempError) as error:
        temp_root_error = error
    schedule = None if temp_root is None else _load_sweep_schedule(state_root, temp_root, max_age_days)
    next_schedule = _SweepSchedule({}, {})
    deleted: list[pathlib.Path] = []

    if schedule is None:
        entries = list_managed_temp()
    else:
        entries = []
        for name in sorted(_registry_names(state_root)):
            known = schedule.registered.get(name)
            if known is not None and _scheduled_item_is_current(pathlib.Path(known[0]), known[1], known[2], cutoff_ns):
                next_schedule.registered[name] = known
                continue
            entry = _load_listed_entry(state_root / name, prefix=None, session_id=None, report_recovery_candidates=False)
            if entry is not None:
                entries.append(entry)
    for entry in entries:
        path = pathlib.Path(entry["path"])
        registry_path = _registry_path(path)
        nonce: str | None = None
        try:
            record = _load_private_json(registry_path)
            recorded_nonce = record.get("nonce")
            nonce = recorded_nonce if isinstance(recorded_nonce, str) else None
            top_mtime_ns = path.stat().st_mtime_ns
            if top_mtime_ns >= cutoff_ns:
                next_schedule.registered[registry_path.name] = (str(path), top_mtime_ns, top_mtime_ns)
                continue
            latest_mtime_ns, git_paths = _latest_update_and_git_paths(path)
            if latest_mtime_ns >= cutoff_ns or git_paths:
                next_schedule.registered[registry_path.name] = (str(path), top_mtime_ns, latest_mtime_ns)
                continue
            cleanup_managed_temp(path)
        except (ManagedTempError, OSError) as error:
            if _sweep_cleanup_completed_elsewhere(path, registry_path, nonce):
                continue
            _outcome.report_warning(
                f"managed-tempのディレクトリを自動削除できない: {path}: {error}",
                next_action=f"本来の操作は継続した。atk managed-temp cleanup --path {path} で回収する",
            )
            continue
        deleted.append(path)

    if temp_root is None:
        # `atk`の共通起動は探索できなかった原因を警告する（委譲先セッションと`atk managed-temp`を除く）。
        return SweepResult(deleted, (), temp_root_error)
    try:
        registry_names = _registry_names(state_root)
        with os.scandir(temp_root) as children:
            names = sorted(child.name for child in children if child.is_dir(follow_symlinks=False))
    except OSError as error:
        return SweepResult(deleted, (), error)
    stale: list[pathlib.Path] = []
    for name in names:
        child = temp_root / name
        if _registry_name(child) in registry_names:
            continue
        known_unregistered = None if schedule is None else schedule.unregistered.get(name)
        if known_unregistered is not None and _scheduled_item_is_current(child, *known_unregistered, cutoff_ns):
            next_schedule.unregistered[name] = known_unregistered
            continue
        if not os.path.lexists(child / _MARKER_NAME):
            continue
        path = child.absolute()
        candidate_latest_ns: int | None = None
        try:
            candidate_top_ns = path.stat().st_mtime_ns
            candidate_latest_ns, git_paths = _latest_update_and_git_paths(path)
            if name.startswith(".agent-toolkit-cleanup-"):
                # 中断した後始末の隔離先はマーカーを持つが、元の領域の登録か消費途中状態が後始末の再開を担う。
                next_schedule.unregistered[name] = (candidate_top_ns, candidate_latest_ns)
                if candidate_latest_ns < cutoff_ns:
                    stale.append(path)
                continue
            if candidate_latest_ns >= cutoff_ns or any(_is_registered_git_worktree(git_path) for git_path in git_paths):
                next_schedule.unregistered[name] = (candidate_top_ns, candidate_latest_ns)
                if candidate_latest_ns < cutoff_ns:
                    stale.append(path)
                continue
            cleanup_managed_temp(path, recover_registry=True, force_remove=True, force_reason="自動削除")
        except (ManagedTempError, OSError) as error:
            if name.startswith(".agent-toolkit-cleanup-"):
                continue
            if _sweep_cleanup_completed_elsewhere(path, _registry_path(path), None):
                continue
            _outcome.report_warning(
                f"登録を失ったmanaged-tempのディレクトリを自動削除できない: {path}: {error}",
                next_action=f"本来の操作は継続した。atk managed-temp cleanup --path {path} --force-remove で回収する",
            )
            if candidate_latest_ns is not None and candidate_latest_ns < cutoff_ns and os.path.lexists(path):
                stale.append(path)
            continue
        deleted.append(path)
    if schedule != next_schedule:
        _save_sweep_schedule(state_root, temp_root, max_age_days, next_schedule)
    return SweepResult(deleted, tuple(stale), None)


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
                raise ManagedTempError(
                    f"管理対象の子ディレクトリが後始末中に置換された: {name}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                )
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
    """受理できるreparse pointの種別、identityおよび格納値を返す。"""
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
    device, inode = _windows_reparse_identity(path)
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
            raise ManagedTempError(
                f"Windows reparse pointが後始末中に置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        if expected[0] in ("junction", "symlink-dir"):
            os.rmdir(path)
        else:
            os.unlink(path)


def _remove_windows_quarantine(root: pathlib.Path, expected_tree: dict[str, _TreeEntry]) -> None:
    """隔離内で検証済みの通常ファイルだけ、Readonlyによる削除拒否を再試行する。"""
    root_identity = _path_identity(root)

    def retry_readonly_file(function: typing.Callable[..., typing.Any], raw_path: str, error: BaseException) -> None:
        if function is not os.unlink or not isinstance(error, PermissionError) or getattr(error, "winerror", None) != 5:
            raise error
        path = pathlib.Path(raw_path)
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise error from None
        expected = expected_tree.get(str(relative))
        if expected is None or expected[0] != "leaf" or len(expected) != 3:
            raise error
        metadata = path.lstat()
        attributes = getattr(metadata, "st_file_attributes", 0)
        if not stat.S_ISREG(metadata.st_mode) or attributes & _WINDOWS_REPARSE_POINT:
            raise error
        if _path_identity(root) != root_identity or _path_identity(path) != expected[1:]:
            raise ManagedTempError(
                f"Readonly解除前に隔離対象が置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            ) from error
        # 親の置換によって、同じ相対位置から隔離領域外へ到達することも防ぐ。
        for parent in relative.parents:
            if parent == pathlib.Path("."):
                continue
            entry = expected_tree.get(str(parent))
            if entry is None or entry[0] != "dir" or _path_identity(root / parent) != entry[1:]:
                raise ManagedTempError(
                    f"Readonly解除前に親ディレクトリが置換された: {path}",
                    next_action=ManagedTempError.REPLACED_NEXT_ACTION,
                ) from error
        if not attributes & stat.FILE_ATTRIBUTE_READONLY:
            raise error
        # WindowsのPython 3.12の`os.chmod`は`follow_symlinks`を受け付けない。
        # 対応版ではリンクを辿らず、非対応版では直前の通常ファイルとreparse pointの検証に委ねる。
        if os.chmod in os.supports_follow_symlinks:
            os.chmod(path, stat.S_IWRITE, follow_symlinks=False)
        else:
            os.chmod(path, stat.S_IWRITE)
        function(raw_path)

    shutil.rmtree(root, onexc=retry_readonly_file)


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
        raise ManagedTempError(
            f"外部状態が消費時に置換された: {validated.registry_path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
        )
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
                raise ManagedTempError(
                    f"管理対象が復元中に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                )
            try:
                os.stat(_MARKER_NAME, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                _write_marker(validated.path, validated.record, directory_descriptor=descriptor)
        finally:
            os.close(descriptor)
        return
    if _path_identity(validated.path) != (validated.device, validated.inode):
        raise ManagedTempError(
            f"管理対象が復元中に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
        )
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
            raise ManagedTempError(
                f"管理対象rootが隔離時に置換または変更された: {root}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        current = os.stat(validated.path.name, dir_fd=root_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            validated.device,
            validated.inode,
        ):
            raise ManagedTempError(
                f"管理対象が隔離時に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        os.rename(validated.path.name, quarantine.name, src_dir_fd=root_descriptor, dst_dir_fd=root_descriptor)
        current = os.stat(quarantine.name, dir_fd=root_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            validated.device,
            validated.inode,
        ):
            raise ManagedTempError(
                f"管理対象が隔離時に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        target_descriptor = os.open(
            quarantine.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_descriptor,
        )
        opened = os.fstat(target_descriptor)
        if (opened.st_dev, opened.st_ino) != (validated.device, validated.inode):
            raise ManagedTempError(
                f"管理対象が隔離時に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        if _tree_snapshot(quarantine) != expected_tree:
            raise ManagedTempError(
                f"管理対象の内容が隔離時に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
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
            raise ManagedTempError(
                f"管理対象が隔離時に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        if _tree_snapshot(quarantine) != expected_tree:
            raise ManagedTempError(
                f"管理対象の内容が隔離時に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        _validate_root(root, expected=expected_root)
        _unlink_windows_reparse_points(quarantine, expected_tree)
        _remove_windows_quarantine(quarantine, expected_tree)
    except OSError as error:
        raise ManagedTempError(f"管理対象を後始末できない: {validated.path}: {error}") from error


def _cleanup_managed_temp(path_arg: pathlib.Path | str, *, recover_registry: bool = False) -> None:
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
    この復元はユーザーの明示指定を第二の信頼根拠として要求し、指定が無ければ行わない。
    """
    root, path = _validate_path_shape(pathlib.Path(path_arg))
    registry_path = _registry_path(path)
    if _restore_interrupted_consume(registry_path):
        _outcome.report_warning(f"中断した後始末の登録を復元した: {registry_path}", next_action="対応不要（処理は継続した）")
    judgement = _classify_quarantine(root, path)
    if judgement.state is _QuarantineState.UNVERIFIABLE:
        raise ManagedTempError(
            f"中断した後始末の状態を判定できない: {path}: {judgement.reason}。"
            "管理情報と隔離先を保持したため、原因を除去した後に同じcleanupを再試行できる",
            next_action=ManagedTempError.RETRYABLE_NEXT_ACTION,
        )
    if judgement.state is _QuarantineState.MATCHED:
        if judgement.quarantine is None or judgement.identity is None:
            raise AssertionError("一致した隔離途中状態に後始末情報がない")
        _cleanup_quarantine(root, judgement.quarantine, judgement.identity)
        _outcome.report_warning(f"中断した後始末の隔離先を後始末した: {path}", next_action="対応不要（処理は継続した）")
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
        _outcome.report_warning(
            f"欠落した登録をマーカーから復元した: {validated.registry_path}", next_action="対応不要（処理は継続した）"
        )
    if os.name == "nt":
        # 利用中に追加された受理済みACEを除去し、隔離以降を実行中のOSアカウントだけのDACLで実行する。
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
            raise ManagedTempError(
                f"管理対象の内容が後始末開始前に置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        if os.name == "posix":
            if not shutil.rmtree.avoids_symlink_attacks:
                raise ManagedTempError("symlink attack耐性を持つ後始末手段を利用できない")
            _cleanup_posix(root, validated, quarantine, before)
        elif os.name == "nt":
            _cleanup_windows(root, validated, quarantine, before)
        else:
            raise ManagedTempError(f"未対応platform: {os.name}", next_action=ManagedTempError.UNSUPPORTED_NEXT_ACTION)
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
            # 原因を分類した送出箇所の次の操作を優先し、分類の無い失敗には再試行を案内する。
            next_action = (
                error.next_action
                if isinstance(error, ManagedTempError) and error.next_action != ManagedTempError.DEFAULT_NEXT_ACTION
                else ManagedTempError.RETRYABLE_NEXT_ACTION
            )
            raise ManagedTempError(
                f"{failure}。管理情報の復元を検証したため、原因を除去した後に同じcleanupを再試行できる",
                next_action=next_action,
            ) from error
        raise ManagedTempError(
            f"{failure}。管理情報の復元を検証できないため、同じcleanupを再試行できない: {recovery_error}",
            next_action=f"同じcleanupは再試行しない。`atk managed-temp list`で{path}の状態を確認し、ユーザーへ報告する",
        ) from error


def _force_remove_managed_temp(
    path_arg: pathlib.Path | str, original_error: ManagedTempError, *, reason: str = "--force-remove"
) -> None:
    """親root、通常ディレクトリおよび所有者だけを確認して実体と登録を回収する。"""
    try:
        _, path = _validate_path_shape(pathlib.Path(path_arg))
        _validate_root(path.parent)
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or (
            os.name == "nt" and getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
        ):
            raise ManagedTempError(
                f"管理対象が通常ディレクトリではない: {path}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        if os.name == "posix":
            if metadata.st_uid != os.geteuid():
                raise ManagedTempError(
                    f"管理対象の所有者が実行中のOSアカウントではない: {path}",
                    next_action=ManagedTempError.PERMISSION_NEXT_ACTION,
                )
        elif os.name == "nt":
            security = _windows_security_descriptor(path)
            current_sid = _windows_sid_bytes(_windows_current_sid())
            if not _windows_equal_sids(security.owner, current_sid):
                raise ManagedTempError(
                    f"管理対象の所有者が実行中のOSアカウントではない: {path}",
                    next_action=ManagedTempError.PERMISSION_NEXT_ACTION,
                )
        else:
            raise ManagedTempError(f"未対応platform: {os.name}", next_action=ManagedTempError.UNSUPPORTED_NEXT_ACTION)
    except (OSError, ValueError, ManagedTempError) as validation_error:
        raise original_error from validation_error

    registry_path = _registry_path(path)
    try:
        shutil.rmtree(path)
        registry_path.unlink(missing_ok=True)
        for consuming in registry_path.parent.glob(f"{registry_path.name}.consuming-*"):
            consuming.unlink(missing_ok=True)
    except OSError as error:
        raise ManagedTempError(f"管理対象を強制回収できない: {path}: {error}") from error
    _outcome.report_warning(
        f"{reason}により管理情報、登録および権限の検証を省いて管理対象を回収した: {path}",
        next_action="対応不要（処理は継続した）",
    )


def _registered_ancestor(path: pathlib.Path) -> pathlib.Path | None:
    """対象の祖先にある登録済みのmanaged-tempのディレクトリを返す。該当が無ければNoneを返す。"""
    try:
        resolved = pathlib.Path(os.path.abspath(path))
    except OSError:
        return None
    for entry in list_managed_temp():
        recorded = entry["path"]
        if not isinstance(recorded, str):
            continue
        registered = pathlib.Path(recorded)
        if registered in resolved.parents:
            return registered
    return None


def _cleanup_child_of_registered_temp(path_arg: pathlib.Path | str) -> bool:
    """個別の管理情報を持たないmanaged-temp直下の作業ディレクトリを、登録済みのディレクトリの配下である場合に削除する。

    `atk managed-temp create --session-root`は、個別登録を持たないmanaged-temp直下の作業ディレクトリを親の配下へ作成する。
    この作業ディレクトリは管理情報を持たないため、通常の検証を通しても回収できない。
    祖先に登録済みのmanaged-tempのディレクトリが実在する場合だけ、その作業ディレクトリを削除して回収を成立させる。
    """
    path = pathlib.Path(path_arg)
    if os.path.lexists(path / _MARKER_NAME) or not path.is_dir():
        return False
    ancestor = _registered_ancestor(path)
    if ancestor is None:
        return False
    shutil.rmtree(path)
    print(
        f"note: 登録済みのmanaged-tempのディレクトリ{ancestor}の配下にあるため、個別の管理情報を経ずに削除した: {path}",
        file=sys.stderr,
    )
    return True


def cleanup_managed_temp(
    path_arg: pathlib.Path | str | None = None,
    *,
    session_id: str | None = None,
    recover_registry: bool = False,
    force_remove: bool = False,
    force_reason: str = "--force-remove",
) -> None:
    """通常の後始末を行い、明示指定時だけ検証失敗後の強制回収を試みる。

    `force_reason`は強制回収を報告する警告で、回収を指示した操作として示す語である。
    """
    if path_arg is not None and session_id is not None:
        raise ManagedTempError("pathとsession_idは同時に指定できない")
    if session_id is not None:
        entries = list_managed_temp(session_id=session_id)
        if not entries:
            return
        if len(entries) != 1:
            raise ManagedTempError(f"session_idに対応する管理対象が複数ある: {session_id}")
        path_arg = entries[0]["path"]
    if path_arg is None:
        raise ManagedTempError("pathまたはsession_idを指定する")
    if _cleanup_child_of_registered_temp(path_arg):
        return
    try:
        _cleanup_managed_temp(path_arg, recover_registry=recover_registry)
    except ManagedTempError as error:
        if not force_remove:
            raise
        _force_remove_managed_temp(path_arg, error, reason=force_reason)
