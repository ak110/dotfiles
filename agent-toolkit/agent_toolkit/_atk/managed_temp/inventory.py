"""managed-tempのディレクトリの列挙、期限切れの掃引と回収（中断した回収の再開と未登録の候補の報告を含む）。

回収は登録簿を消費してから隔離名へ移し、隔離後の内容を削除する順に進め、中断した時点から再開できる状態を保つ。
"""

from __future__ import annotations

import contextlib
import datetime
import enum
import functools
import hashlib
import ntpath
import os
import pathlib
import re
import secrets
import shutil
import stat
import sys
import typing

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.managed_temp import permissions
from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._atk.managed_temp.registry import (
    _MARKER_NAME,
    MAX_AGE_DAYS,
    _awis_are_valid,
    _invalid_prefix_error,
    _load_marker,
    _load_private_json,
    _ManagedTempEntry,
    _owner_record,
    _path_identity,
    _records_match,
    _registry_name,
    _registry_path,
    _state_root,
    _temp_root,
    _validate_root,
    _ValidatedRoot,
    _ValidatedTemp,
    _write_marker,
    _write_private_json,
    is_valid_prefix,
)
from agent_toolkit._atk.managed_temp.validation import (
    _validate_path_shape,
    _validate_posix,
    _validate_windows,
    validate_managed_temp,
)
from agent_toolkit._common import file_lock, plugin_root_leases

# 他の処理で使わない依存は、使用時まで読み込まない。
lazy from agent_toolkit._atk.managed_temp.windows_security import (
    _WINDOWS_FILE_ATTRIBUTE_DIRECTORY,
    _WINDOWS_REPARSE_POINT,
    _windows_current_sid,
    _windows_equal_sids,
    _windows_extended_path,
    _windows_identity,
    _windows_owner_belongs_to_current_token,
    _windows_reparse_identity,
    _windows_secure_path,
    _windows_security_descriptor,
    _windows_sid_bytes,
)


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
    except OSError, ValueError, ManagedTempError:
        return False
    if registry.get("path") != str(path) or os.path.lexists(path):
        return False
    if path.parent.exists():
        try:
            _validate_root(path.parent)
        except OSError, ValueError, ManagedTempError:
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
    except OSError, ValueError, ManagedTempError:
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
            with permissions.directory(quarantine) as descriptor:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != identity:
                    raise ManagedTempError(
                        f"隔離先が再開時に置換された: {quarantine}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                    )
                _clear_directory(descriptor)
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
    対話シェルの起動がその項目数に比例して遅れる。判定を追加する場合も、
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
            with permissions.descriptor(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) as directory_descriptor:
                marker = _load_marker(directory_descriptor, path)
        else:
            marker = _load_private_json(path / _MARKER_NAME)
    except OSError, ManagedTempError:
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
        if child.name.startswith(_CLEANUP_QUARANTINE_PREFIX):
            original = _quarantine_original_target(child)
            if (
                original is not None
                and not os.path.lexists(_registry_path(original))
                and not _cleanup_in_progress(_registry_path(original))
            ):
                _outcome.report_warning(
                    f"後始末が中断した可能性がある管理対象がある: {original}",
                    next_action=f"回収する場合は atk managed-temp cleanup --path {original} を実行する",
                )
            continue
        registry_path = _registry_path(child)
        if _cleanup_in_progress(registry_path):
            continue
        try:
            latest, git_paths = _latest_update_and_git_paths(child)
            if latest >= _cutoff_ns(datetime.datetime.now(datetime.UTC), MAX_AGE_DAYS) or any(
                _is_registered_git_worktree(git_path, tree_root=child) for git_path in git_paths
            ):
                continue
        except OSError:
            pass
        if _report_retained_plugin_root(child):
            continue
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
    if _cleanup_in_progress(registry_path):
        return None
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
            if _classify_quarantine(path.parent, path).state is not _QuarantineState.ABSENT:
                if report_recovery_candidates:
                    _outcome.report_warning(
                        f"後始末が中断した可能性がある管理対象がある: {path}",
                        next_action=f"回収する場合は atk managed-temp cleanup --path {path} を実行する",
                    )
                return None
            if _entity_absence_is_confirmed(record, path):
                registry_path.unlink(missing_ok=True)
                _outcome.report_warning(
                    f"実体が失われた管理対象の登録を回収した: {path}",
                    next_action=f"{path}を使用中だった場合は、消失の原因を確認してユーザーへ報告する",
                )
            elif report_recovery_candidates:
                _outcome.report_warning(
                    f"実体へ到達できないため登録を保持した: {path}",
                    next_action="同じ絶対パスへ到達できる実行文脈で atk managed-temp list を実行すると回収する",
                )
            return None
        validate_managed_temp(path)
        if report_recovery_candidates and path.name.startswith("agents-server-plugin-root-"):
            try:
                latest, _ = _latest_update_and_git_paths(path)
            except OSError, ManagedTempError:
                # 期限の観測に失敗した配布物も、真正性を検証した登録として一覧へ残す。
                pass
            else:
                if latest < _cutoff_ns(datetime.datetime.now(datetime.UTC), MAX_AGE_DAYS):
                    _report_retained_plugin_root(path)
        return {
            "path": str(path),
            "prefix": item_prefix,
            "created_at": created_at,
            "awis": typing.cast(list[str], awis),
            "session_id": item_session_id,
        }
    except (KeyError, OSError, ValueError, ManagedTempError) as error:
        if not os.path.lexists(registry_path) or _cleanup_in_progress(registry_path):
            return None
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


def _report_retained_plugin_root(path: pathlib.Path) -> bool:
    """期限超過の配布物をロック下で判定し、保持理由と再判定条件を示す。

    別processが判定・回収中なら、その対象の案内を省いてlistを継続する。
    """
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(plugin_root_leases.plugin_root_lock(path, blocking=False))
        except OSError:
            return True
        try:
            _require_unused_plugin_root(path)
        except _PluginRootRetained as error:
            _outcome.report_warning(
                str(error),
                next_action="利用プロセスの終了後に atk managed-temp list で再判定結果を確認する。次の自動掃引でも再判定する",
            )
            return True
    return False


def _cleanup_in_progress(registry_path: pathlib.Path) -> bool:
    """対象別ロックを別実行が保持している間は、途中状態の読み直しを省く。"""
    lock_path = registry_path.with_name(f"{registry_path.name}.cleanup.lock")
    if not lock_path.exists():
        return False
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            file_lock.acquire_lock(handle, blocking=False)
        except OSError:
            return True
        file_lock.release_lock(handle)
    return False


def _quarantine_original_target(quarantine: pathlib.Path) -> pathlib.Path | None:
    """隔離先のnonce・identityが管理側の記録と一致する場合だけ、元の回収対象を返す。"""
    try:
        marker = _load_private_json(quarantine / _MARKER_NAME)
        nonce = marker.get("nonce")
        recorded = marker.get("path")
        if not isinstance(nonce, str) or not isinstance(recorded, str):
            return None
        original = pathlib.Path(recorded)
        if original.parent != quarantine.parent or quarantine.name != f"{_CLEANUP_QUARANTINE_PREFIX}{nonce}":
            return None
        if os.path.lexists(original) or marker.get("identity") != list(_path_identity(quarantine)):
            return None
        registry = _registry_path(original)
        consuming = registry.with_name(f"{registry.name}.consuming-{nonce}")
        for path in (registry, consuming):
            if os.path.lexists(path) and _load_private_json(path) == marker:
                return original
    except OSError, ManagedTempError:
        pass
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

    配下を含む最終更新を使うのは、中断後に再開した領域を古いと誤判定しないためである。
    """
    if os.name == "posix":
        with permissions.descriptor(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) as descriptor:
            latest = os.fstat(descriptor).st_mtime_ns
            posix_git_paths = []
            for relative, metadata in _walk_posix(descriptor, tree_root=path):
                latest = max(latest, metadata.st_mtime_ns)
                if pathlib.PurePath(relative).name == ".git":
                    posix_git_paths.append(path / relative)
            return latest, posix_git_paths
    original_path = path
    path = pathlib.Path(_windows_extended_path(path))
    extended_root = path
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
    return latest_mtime_ns, [original_path / git_path.relative_to(extended_root) for git_path in git_paths]


def _is_registered_git_worktree(git_path: pathlib.Path, *, tree_root: pathlib.Path) -> bool:
    """`.git`ファイルの`gitdir:`が指すリポジトリ側の管理ディレクトリが実在するかを返す。

    git worktreeの`.git`はファイルで、リポジトリ側の`worktrees/<名前>`を指す。指す先が残る間は
    元のリポジトリの`git worktree list`に登録が残っており、そのリポジトリの作業が使用中である。
    ディレクトリの`.git`（単独の複製）は他から参照されないため、ここでは使用中と扱わない。
    """
    try:
        with _open_tree_parent(git_path, tree_root=tree_root) as (parent, name):
            metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode):
                return False
            if parent is None:
                first_line = pathlib.Path(name).read_text(encoding="utf-8").splitlines()[0]
            else:
                with (
                    permissions.descriptor(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent) as descriptor,
                    os.fdopen(descriptor, encoding="utf-8", closefd=False) as handle,
                ):
                    first_line = handle.readline().rstrip("\r\n")
    except FileNotFoundError, NotADirectoryError:
        return False
    except OSError, UnicodeDecodeError, IndexError:
        # 内容を読めない場合は使用中かを判定できないため、使用中と扱って削除せず残す。
        return True
    prefix = "gitdir:"
    if not first_line.startswith(prefix):
        return True
    target = pathlib.Path(first_line[len(prefix) :].strip())
    if not target.is_absolute():
        target = git_path.parent / target
    try:
        with _open_tree_parent(target) as (parent, name):
            return stat.S_ISDIR(os.stat(name, dir_fd=parent).st_mode)
    except FileNotFoundError, NotADirectoryError:
        return False


@contextlib.contextmanager
def _open_tree_parent(path: pathlib.Path, *, tree_root: pathlib.Path | None = None) -> typing.Generator[tuple[int | None, str]]:
    """深部へ相対openで到達し、管理領域内ではsymlinkを追跡せず親と名前を返す。"""
    if os.name != "posix":
        yield None, _windows_extended_path(path)
        return
    # 管理領域の祖先と外部gitdirは通常のパス解決を保つ。非追跡の境界は領域自身からとする。
    root = pathlib.Path(path.anchor) if tree_root is None else tree_root
    flags = os.O_RDONLY | os.O_DIRECTORY
    if tree_root is not None:
        flags |= os.O_NOFOLLOW
    with contextlib.ExitStack() as stack:
        descriptor = stack.enter_context(permissions.descriptor(root, flags))
        for part in path.relative_to(root).parts[:-1]:
            descriptor = stack.enter_context(permissions.descriptor(part, flags, dir_fd=descriptor))
        yield descriptor, path.name


_SWEEP_SCHEDULE_NAME = ".sweep/schedule"
"""掃引の期限判定記録のファイル名。登録ファイルの`*.json`と区別するため拡張子を付けない。"""
_SWEEP_SCHEDULE_VERSION = 2

_ScheduledRegistered = tuple[str, int, int, tuple[str, ...]]
"""登録済み候補の記録。

管理対象のpath、記録時の領域自身の最終更新、観測した配下を含む最終更新（ナノ秒）、
期限を超えても`.git`を理由に残した場合の`.git`の領域からの相対パス（それ以外は空）。
"""
_ScheduledUnregistered = tuple[int, int, tuple[str, ...]]
"""登録を失った候補の記録。

記録時の領域自身の最終更新、観測した配下を含む最終更新（ナノ秒）、
期限を超えても使用中のgit worktreeを理由に残した場合のその`.git`の領域からの相対パス（それ以外は空）。
"""
_CLEANUP_QUARANTINE_PREFIX = ".agent-toolkit-cleanup-"


class SweepResult(typing.NamedTuple):
    """`sweep_managed_temp`の結果。"""

    deleted: list[pathlib.Path]
    """自動削除したパス。"""
    stale_unregistered: tuple[pathlib.Path, ...]
    """掃引の後も残った、最終更新から期限を超えた登録を持たない管理対象。"""
    unregistered_error: ManagedTempError | OSError | None
    """登録を持たない管理対象を探索できなかった場合の原因。"""
    retained: tuple[tuple[str, str], ...] = ()
    retention_changed: bool = False


class _SweepSchedule(typing.NamedTuple):
    registered: dict[str, _ScheduledRegistered]
    unregistered: dict[str, _ScheduledUnregistered]
    retained: tuple[tuple[str, str], ...] = ()
    retention_basis: dict[str, dict[str, typing.Any]] | None = None


def _is_nanoseconds(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _exclusion_basis_or_none(value: object) -> tuple[str, ...] | None:
    """記録した除外根拠を検証し、領域の外を指し得る値では`None`を返す。"""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    for item in value:
        relative = pathlib.PurePath(item)
        if relative.is_absolute() or relative.anchor or ".." in relative.parts or relative.name != ".git":
            return None
    return tuple(value)


def _exclusion_basis(path: pathlib.Path, git_paths: typing.Iterable[pathlib.Path]) -> tuple[str, ...]:
    return tuple(sorted(str(git_path.relative_to(path)) for git_path in git_paths))


def _registered_exclusion_holds(path: pathlib.Path, basis: tuple[str, ...]) -> bool:
    """登録済み候補を残した理由の`.git`が1つでも残るかを返す。"""
    for relative in basis:
        try:
            with _open_tree_parent(path / relative, tree_root=path) as (parent, name):
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            return True
        except FileNotFoundError, NotADirectoryError:
            continue
        except OSError:
            # 到達不能な根拠は保持し、次回も削除対象へ移さない。
            return True
    return False


def _unregistered_exclusion_holds(path: pathlib.Path, basis: tuple[str, ...]) -> bool:
    """登録を失った候補を残した理由（後始末の隔離先の名前、使用中のgit worktree）が残るかを返す。"""
    return path.name.startswith(_CLEANUP_QUARANTINE_PREFIX) or any(
        _is_registered_git_worktree(path / relative, tree_root=path) for relative in basis
    )


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
            and len(item) == 4
            and isinstance(item[0], str)
            and _is_nanoseconds(item[1])
            and _is_nanoseconds(item[2])
        ):
            return None
        basis = _exclusion_basis_or_none(item[3])
        if basis is None:
            return None
        registered[name] = (item[0], item[1], item[2], basis)
    unregistered: dict[str, _ScheduledUnregistered] = {}
    for name, item in raw_unregistered.items():
        if not (isinstance(item, list) and len(item) == 3 and _is_nanoseconds(item[0]) and _is_nanoseconds(item[1])):
            return None
        basis = _exclusion_basis_or_none(item[2])
        if basis is None:
            return None
        unregistered[name] = (item[0], item[1], basis)
    retained = value.get("retained", [])
    if not isinstance(retained, list) or any(
        not isinstance(item, list) or len(item) != 2 or not all(isinstance(part, str) for part in item) for item in retained
    ):
        return None
    bases = value.get("retention_basis", {})
    if not isinstance(bases, dict) or any(not isinstance(item, dict) for item in bases.values()):
        return None
    return _SweepSchedule(registered, unregistered, tuple((item[0], item[1]) for item in retained), bases)


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
        "registered": {name: [*item[:3], list(item[3])] for name, item in schedule.registered.items()},
        "unregistered": {name: [*item[:2], list(item[2])] for name, item in schedule.unregistered.items()},
        "retained": [list(item) for item in schedule.retained],
        "retention_basis": schedule.retention_basis or {},
    }
    try:
        _write_private_json(temporary, value)
        os.replace(temporary, path)
    except OSError, ManagedTempError:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _scheduled_item_is_current(
    path: pathlib.Path,
    top_mtime_ns: int,
    latest_mtime_ns: int,
    cutoff_ns: int,
    exclusion_holds: typing.Callable[[], bool],
) -> bool:
    """記録した候補が期限前か除外根拠を保ち、記録後に領域自身が変わっていない場合だけ真を返す。

    配下の更新は最終更新を後ろへ移すだけなので、記録した最終更新が期限前なら実際の最終更新も期限前である。
    期限を超えても除外根拠（`.git`、使用中のgit worktree）を理由に残した候補は、その根拠が残る間は
    同じ判定で残るため、配下を走査し直さない。根拠が消えた場合は従来の検証へ戻して再判定する。
    領域自身の最終更新が変わった場合と消えた場合は、記録を信用せず従来の検証へ戻す。
    """
    if latest_mtime_ns < cutoff_ns and not exclusion_holds():
        return False
    try:
        return os.stat(path).st_mtime_ns == top_mtime_ns
    except OSError:
        return False


def _registry_names(state_root: pathlib.Path) -> set[str]:
    with os.scandir(state_root) as entries:
        return {entry.name for entry in entries if entry.name.endswith(".json")}


def _retained_candidate_is_current(path: pathlib.Path, top_mtime_ns: int, basis: dict[str, typing.Any]) -> bool:
    if not plugin_root_leases.usage_basis_is_current(basis):
        return False
    try:
        return path.stat().st_mtime_ns == top_mtime_ns
    except OSError:
        return False


def _sweep_signature(state_root: pathlib.Path, temp_root: pathlib.Path) -> list[list[int]]:
    """集合と詳細記録の変更を、直下の名前と属性から検出する。"""
    try:
        detail = (state_root / _SWEEP_SCHEDULE_NAME).stat()
    except FileNotFoundError:
        detail = None
    signature = [
        [
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_size,
            metadata.st_nlink,
            int.from_bytes(hashlib.sha256(os.fsencode("\0".join(sorted(os.listdir(directory))))).digest()),
        ]
        for directory, metadata in ((state_root, state_root.stat()), (temp_root, temp_root.stat()))
    ]
    signature.append([] if detail is None else [detail.st_dev, detail.st_ino, detail.st_mtime_ns, detail.st_ctime_ns])
    return signature


def _sweep_gate_path(state_root: pathlib.Path) -> pathlib.Path:
    return state_root.parent / ".managed-temp-sweep-gate"


def _sweep_gate_result(
    state_root: pathlib.Path,
    temp_root: pathlib.Path,
    cutoff_ns: int,
    days: int,
) -> SweepResult | None:
    try:
        gate = _load_private_json(_sweep_gate_path(state_root))
        until = gate.get("until")
        if not (
            gate.get("version") == 2
            and gate.get("state_root") == str(state_root)
            and gate.get("temp_root") == str(temp_root)
            and gate.get("days") == days
            and (until is None or _is_nanoseconds(until) and cutoff_ns <= until)
            and gate.get("signature") == _sweep_signature(state_root, temp_root)
        ):
            return None
        exceptions = gate.get("exceptions")
        retained = gate.get("retained")
        if not isinstance(exceptions, list) or not isinstance(retained, list):
            return None
        for item in exceptions:
            if not isinstance(item, list) or len(item) != 6 or not isinstance(item[0], str):
                return None
            path, top, latest, registered, raw_basis, retention_basis = item
            basis = _exclusion_basis_or_none(raw_basis)
            if not _is_nanoseconds(top) or not _is_nanoseconds(latest) or basis is None or not isinstance(registered, bool):
                return None
            target = pathlib.Path(path)
            if not target.is_absolute() or retention_basis is not None and not isinstance(retention_basis, dict):
                return None
            if isinstance(retention_basis, dict):
                if not _retained_candidate_is_current(target, top, retention_basis):
                    return None
            elif not _scheduled_item_is_current(
                target,
                top,
                latest,
                cutoff_ns,
                functools.partial(_registered_exclusion_holds if registered else _unregistered_exclusion_holds, target, basis),
            ):
                return None
        if any(
            not isinstance(item, list) or len(item) != 2 or not all(isinstance(part, str) for part in item) for item in retained
        ):
            return None
        return SweepResult([], (), None, tuple((item[0], item[1]) for item in retained))
    except OSError, TypeError, ValueError, ManagedTempError:
        return None


def _save_sweep_gate(
    state_root: pathlib.Path,
    temp_root: pathlib.Path,
    days: int,
    schedule: _SweepSchedule,
    cutoff_ns: int,
    before: list[list[int]] | None,
) -> None:
    deadlines: list[int] = []
    exceptions: list[list[typing.Any]] = []
    items = [
        *((item[0], item[1], item[2], True, item[3]) for item in schedule.registered.values()),
        *((str(temp_root / name), item[0], item[1], False, item[2]) for name, item in schedule.unregistered.items()),
    ]
    for path, top, latest, registered, basis in items:
        if latest >= cutoff_ns:
            deadlines.append(latest)
            continue
        retention_basis = (schedule.retention_basis or {}).get(path)
        if (
            not basis
            and retention_basis is None
            and (registered or not pathlib.Path(path).name.startswith(_CLEANUP_QUARANTINE_PREFIX))
        ):
            return
        exceptions.append([path, top, latest, registered, list(basis), retention_basis])
    temporary = state_root.parent / f".managed-temp-sweep-gate-{secrets.token_hex(8)}"
    try:
        signature = _sweep_signature(state_root, temp_root)
        # 掃引中の集合変更と、並行した旧版の作成も有効な不変記録として確定しない。
        if before is None or signature[:2] != before[:2]:
            return
        _write_private_json(
            temporary,
            {
                "version": 2,
                "state_root": str(state_root),
                "temp_root": str(temp_root),
                "days": days,
                "until": min(deadlines) if deadlines else None,
                "signature": signature,
                "exceptions": exceptions,
                "retained": [list(item) for item in schedule.retained],
            },
        )
        os.replace(temporary, _sweep_gate_path(state_root))
    except OSError, ManagedTempError:
        pass
    finally:
        temporary.unlink(missing_ok=True)


def sweep_expired_managed_temp(
    *,
    now: datetime.datetime,
    max_age_days: int = MAX_AGE_DAYS,
) -> list[pathlib.Path]:
    """最終更新から`max_age_days`を超えたmanaged-tempのディレクトリを削除し、削除したパスを返す。"""
    return sweep_managed_temp(now=now, max_age_days=max_age_days).deleted


@plugin_root_leases.reuse_process_observation()
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
    候補だけを検証から外す。期限を超えても`.git`か使用中のgit worktreeを理由に残した候補は、その除外根拠を
    記録し、根拠が残る間は配下の走査と真正性検証を省き、根拠が消えた起動で再判定する。
    集合と詳細記録が不変で期限前の場合は、小さい判定情報と期限超過の除外根拠だけを読み直す。
    集合変更、期限到来、除外根拠の消失、記録の欠落・破損では従来の検証と削除判定へ戻り、
    領域自身の変化と消失も現物で確認する。削除は従来の判定を通った候補に限る。
    """
    cutoff_ns = _cutoff_ns(now, max_age_days)
    state_root = _state_root()
    (state_root / ".sweep").mkdir(mode=0o700, exist_ok=True)
    temp_root: pathlib.Path | None = None
    temp_root_error: ManagedTempError | OSError | None = None
    try:
        temp_root = _temp_root()
    except (OSError, ManagedTempError) as error:
        temp_root_error = error
    before = None
    if temp_root is not None:
        cached_result = _sweep_gate_result(state_root, temp_root, cutoff_ns, max_age_days)
        if cached_result is not None:
            return cached_result
        with contextlib.suppress(OSError):
            before = _sweep_signature(state_root, temp_root)
    schedule = None if temp_root is None else _load_sweep_schedule(state_root, temp_root, max_age_days)
    next_schedule = _SweepSchedule({}, {}, (), {})
    deleted: list[pathlib.Path] = []
    retained: list[tuple[str, str]] = []
    previous_retained = {} if schedule is None else dict(schedule.retained)
    retry_needed = False

    if schedule is None:
        entries = list_managed_temp()
    else:
        entries = []
        for name in sorted(_registry_names(state_root)):
            known = schedule.registered.get(name)
            if known is not None:
                known_path = pathlib.Path(known[0])
                retained_basis = (schedule.retention_basis or {}).get(str(known_path))
                reason = previous_retained.get(str(known_path))
                if (
                    reason is not None
                    and retained_basis is not None
                    and _retained_candidate_is_current(known_path, known[1], retained_basis)
                ):
                    retained.append((str(known_path), reason))
                    next_schedule.registered[name] = known
                    assert next_schedule.retention_basis is not None
                    next_schedule.retention_basis[str(known_path)] = retained_basis
                    continue
                if _scheduled_item_is_current(
                    known_path,
                    known[1],
                    known[2],
                    cutoff_ns,
                    functools.partial(_registered_exclusion_holds, known_path, known[3]),
                ):
                    next_schedule.registered[name] = known
                    continue
            entry = _load_listed_entry(state_root / name, prefix=None, session_id=None, report_recovery_candidates=False)
            if entry is not None:
                entries.append(entry)
    for entry in entries:
        path = pathlib.Path(entry["path"])
        registry_path = _registry_path(path)
        nonce: str | None = None
        top_mtime_ns: int | None = None
        latest_mtime_ns: int | None = None
        try:
            record = _load_private_json(registry_path)
            recorded_nonce = record.get("nonce")
            nonce = recorded_nonce if isinstance(recorded_nonce, str) else None
            top_mtime_ns = path.stat().st_mtime_ns
            if top_mtime_ns >= cutoff_ns:
                next_schedule.registered[registry_path.name] = (str(path), top_mtime_ns, top_mtime_ns, ())
                continue
            latest_mtime_ns, git_paths = _latest_update_and_git_paths(path)
            if latest_mtime_ns >= cutoff_ns:
                next_schedule.registered[registry_path.name] = (str(path), top_mtime_ns, latest_mtime_ns, ())
                continue
            if git_paths:
                basis = _exclusion_basis(path, git_paths)
                next_schedule.registered[registry_path.name] = (str(path), top_mtime_ns, latest_mtime_ns, basis)
                continue
            if not _cleanup_managed_temp(path, blocking=False):
                retry_needed = True
                continue
        except _PluginRootRetained as error:
            retained.append((str(path), str(error)))
            if top_mtime_ns is not None and latest_mtime_ns is not None and error.basis is not None:
                next_schedule.registered[registry_path.name] = (str(path), top_mtime_ns, latest_mtime_ns, ())
                assert next_schedule.retention_basis is not None
                next_schedule.retention_basis[str(path)] = error.basis
            continue
        except (ManagedTempError, OSError) as error:
            if (
                not os.path.lexists(registry_path)
                or _cleanup_in_progress(registry_path)
                or _sweep_cleanup_completed_elsewhere(path, registry_path, nonce)
            ):
                continue
            retry_needed = True
            _outcome.report_persistent_warning(
                f"managed-tempのディレクトリを自動削除できない: {path}: {error}",
                next_action=(
                    error.next_action
                    if isinstance(error, ManagedTempError) and error.next_action != ManagedTempError.DEFAULT_NEXT_ACTION
                    else f"本来の操作は継続した。atk managed-temp cleanup --path {path} で回収する"
                ),
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
        if known_unregistered is not None:
            known_top_ns, known_latest_ns, known_basis = known_unregistered
            retained_basis = (schedule.retention_basis or {}).get(str(child)) if schedule is not None else None
            retained_reason = previous_retained.get(str(child))
            if (
                retained_reason is not None
                and retained_basis is not None
                and _retained_candidate_is_current(child, known_top_ns, retained_basis)
            ):
                retained.append((str(child), retained_reason))
                next_schedule.unregistered[name] = known_unregistered
                assert next_schedule.retention_basis is not None
                next_schedule.retention_basis[str(child)] = retained_basis
                continue
            if _scheduled_item_is_current(
                child,
                known_top_ns,
                known_latest_ns,
                cutoff_ns,
                functools.partial(_unregistered_exclusion_holds, child, known_basis),
            ):
                next_schedule.unregistered[name] = known_unregistered
                if known_latest_ns < cutoff_ns and not known_basis and not name.startswith(_CLEANUP_QUARANTINE_PREFIX):
                    stale.append(child.absolute())
                continue
        if not os.path.lexists(child / _MARKER_NAME):
            continue
        path = child.absolute()
        candidate_latest_ns: int | None = None
        candidate_top_ns: int | None = None
        try:
            candidate_top_ns = path.stat().st_mtime_ns
            candidate_latest_ns, git_paths = _latest_update_and_git_paths(path)
            if name.startswith(_CLEANUP_QUARANTINE_PREFIX):
                # 中断した後始末の隔離先はマーカーを持つが、元の領域の登録か消費途中状態が後始末の再開を担う。
                # 名前が除外根拠であり、名前が変われば別の候補として扱われるため、記録する根拠は持たない。
                next_schedule.unregistered[name] = (candidate_top_ns, candidate_latest_ns, ())
                continue
            if candidate_latest_ns >= cutoff_ns:
                next_schedule.unregistered[name] = (candidate_top_ns, candidate_latest_ns, ())
                continue
            live_worktrees = [git_path for git_path in git_paths if _is_registered_git_worktree(git_path, tree_root=path)]
            if live_worktrees:
                basis = _exclusion_basis(path, live_worktrees)
                next_schedule.unregistered[name] = (candidate_top_ns, candidate_latest_ns, basis)
                continue
            try:
                if not _cleanup_managed_temp(path, recover_registry=True, blocking=False):
                    retry_needed = True
                    continue
            except ManagedTempError as error:
                if not _force_remove_managed_temp(path, error, reason="自動削除", blocking=False):
                    retry_needed = True
                    continue
        except _PluginRootRetained as error:
            retained.append((str(path), str(error)))
            if candidate_top_ns is not None and candidate_latest_ns is not None and error.basis is not None:
                next_schedule.unregistered[name] = (candidate_top_ns, candidate_latest_ns, ())
                assert next_schedule.retention_basis is not None
                next_schedule.retention_basis[str(path)] = error.basis
            continue
        except (ManagedTempError, OSError) as error:
            if name.startswith(_CLEANUP_QUARANTINE_PREFIX):
                continue
            if _sweep_cleanup_completed_elsewhere(path, _registry_path(path), None):
                continue
            retry_needed = True
            _outcome.report_persistent_warning(
                f"登録を失ったmanaged-tempのディレクトリを自動削除できない: {path}: {error}",
                next_action=(
                    error.next_action
                    if isinstance(error, ManagedTempError) and error.next_action != ManagedTempError.DEFAULT_NEXT_ACTION
                    else f"本来の操作は継続した。atk managed-temp cleanup --path {path} --force-remove で回収する"
                ),
            )
            if candidate_latest_ns is not None and candidate_latest_ns < cutoff_ns and os.path.lexists(path):
                stale.append(path)
            continue
        deleted.append(path)
    retention = tuple(sorted(retained))
    next_schedule = next_schedule._replace(retained=retention)
    retention_changed = bool(retention) and (schedule is None or schedule.retained != retention)
    if schedule != next_schedule:
        _save_sweep_schedule(state_root, temp_root, max_age_days, next_schedule)
    if not stale and not retry_needed and registry_names == set(next_schedule.registered):
        _save_sweep_gate(state_root, temp_root, max_age_days, next_schedule, cutoff_ns, before)
    return SweepResult(deleted, tuple(stale), None, retention, retention_changed)


@contextlib.contextmanager
def _open_posix_child(
    descriptor: int, name: str, before: os.stat_result, *, display_path: str | None = None
) -> typing.Generator[int]:
    """非追跡openの前後で子のidentityが一致するか確かめる。"""
    with permissions.directory(name, dir_fd=descriptor, expected=before, display_path=display_path) as child:
        yield child


def _walk_posix(
    descriptor: int, prefix: str = "", *, tree_root: pathlib.Path | None = None
) -> typing.Iterator[tuple[str, os.stat_result]]:
    """snapshotと保持判定で共用する、記述子相対の非追跡走査。"""
    with os.scandir(descriptor) as entries:
        for entry in entries:
            metadata = entry.stat(follow_symlinks=False)
            relative = os.path.join(prefix, entry.name)
            yield relative, metadata
            if stat.S_ISDIR(metadata.st_mode):
                display = relative if tree_root is None else str(tree_root / relative)
                with _open_posix_child(descriptor, entry.name, metadata, display_path=display) as child:
                    yield from _walk_posix(child, relative, tree_root=tree_root)


def _clear_directory(descriptor: int) -> None:
    """開いたディレクトリだけを起点に、リンク参照を避けて内容を除去する。"""
    _clear_directory_entries(descriptor, "")


def _clear_directory_entries(descriptor: int, prefix: str) -> None:
    for name in os.listdir(descriptor):
        before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            os.unlink(name, dir_fd=descriptor)
            continue
        relative = os.path.join(prefix, name)
        with _open_posix_child(descriptor, name, before, display_path=relative) as child_descriptor:
            _clear_directory_entries(child_descriptor, relative)
            os.rmdir(name, dir_fd=descriptor)


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
        stored_target = os.readlink(_windows_extended_path(path))
        target = _windows_stored_path(stored_target)
        root = _windows_stored_path(str(managed_root))
    except OSError, ValueError:
        raise ManagedTempError(f"Windows reparse pointは後始末できない: {path}") from None
    if not target.is_absolute() or not target.is_relative_to(root):
        raise ManagedTempError(f"Windows reparse pointは後始末できない: {path}")
    device, inode = _windows_reparse_identity(path)
    return kind, device, inode, stored_target


def _tree_snapshot(root: pathlib.Path | int) -> dict[str, _TreeEntry]:
    """cleanup開始前のtree identityを取得し、安全なreparse pointだけを記録する。"""
    snapshot: dict[str, _TreeEntry] = {}
    if os.name == "posix":
        with permissions.descriptor(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) as descriptor:
            for relative, metadata in _walk_posix(descriptor, tree_root=root if isinstance(root, pathlib.Path) else None):
                kind = "dir" if stat.S_ISDIR(metadata.st_mode) else "leaf"
                snapshot[relative] = (kind, metadata.st_dev, metadata.st_ino)
        return snapshot
    assert isinstance(root, pathlib.Path)
    root = pathlib.Path(_windows_extended_path(root))
    pending = [root]
    while pending:
        parent = pending.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
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
        path = pathlib.Path(_windows_extended_path(root / relative))
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
    """隔離先を削除する。Readonly属性で削除を拒否された通常ファイルは、隔離内で検証済みのものに限り属性を解除して削除を再試行する。"""
    root = pathlib.Path(_windows_extended_path(root))
    root_identity = _path_identity(root)

    def retry_readonly_file(function: typing.Callable[..., typing.Any], raw_path: str, error: BaseException) -> None:
        if (
            function not in (os.unlink, os.rmdir)
            or not isinstance(error, PermissionError)
            or getattr(error, "winerror", None) != 5
        ):
            raise error
        path = pathlib.Path(raw_path)
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise error from None
        expected = ("dir", *root_identity) if relative == pathlib.Path(".") else expected_tree.get(str(relative))
        if expected is None or expected[0] not in ("leaf", "dir") or len(expected) != 3:
            raise error
        metadata = path.lstat()
        attributes = getattr(metadata, "st_file_attributes", 0)
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)) or attributes & _WINDOWS_REPARSE_POINT:
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
        security = _windows_security_descriptor(path)
        if not _windows_owner_belongs_to_current_token(security.owner):
            raise ManagedTempError(
                f"所有者が異なるためReadonly属性を解除できない: {path}",
                next_action=ManagedTempError.PERMISSION_NEXT_ACTION,
            ) from error
        os.chmod(path, stat.S_IWRITE, follow_symlinks=False)
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
        with permissions.descriptor(validated.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) as descriptor:
            metadata = os.fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) != (validated.device, validated.inode):
                raise ManagedTempError(
                    f"管理対象が復元中に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                )
            try:
                os.stat(_MARKER_NAME, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                _write_marker(validated.path, validated.record, directory_descriptor=descriptor)
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
    with contextlib.ExitStack() as stack:
        _cleanup_posix_scoped(root, validated, quarantine, expected_tree, stack)


def _cleanup_posix_scoped(
    root: pathlib.Path,
    validated: _ValidatedTemp,
    quarantine: pathlib.Path,
    expected_tree: dict[str, _TreeEntry],
    stack: contextlib.ExitStack,
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
        root_descriptor = stack.enter_context(permissions.descriptor(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
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
        target_descriptor = stack.enter_context(
            permissions.descriptor(
                quarantine.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_descriptor,
            )
        )
        opened = os.fstat(target_descriptor)
        if (opened.st_dev, opened.st_ino) != (validated.device, validated.inode):
            raise ManagedTempError(
                f"管理対象が隔離時に置換された: {validated.path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
            )
        if _tree_snapshot(target_descriptor) != expected_tree:
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


def _cleanup_managed_temp(
    path_arg: pathlib.Path | str,
    *,
    recover_registry: bool = False,
    blocking: bool = True,
) -> bool:
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
    consuming_before_lock = _consuming_registry_path(registry_path)
    existed_before_lock = os.path.lexists(path) or os.path.lexists(registry_path) or consuming_before_lock is not None
    lock_path = registry_path.with_name(f"{registry_path.name}.cleanup.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            file_lock.acquire_lock(lock_file, blocking=blocking)
        except OSError:
            if not blocking:
                return False
            raise
        try:
            if (
                existed_before_lock
                and not os.path.lexists(path)
                and not os.path.lexists(registry_path)
                and _consuming_registry_path(registry_path) is None
            ):
                return True
            with contextlib.ExitStack() as stack:
                # 自動掃引は内側の配布物ロックも待たず、使用登録や明示回収と競合した対象を保持して次へ進む。
                try:
                    stack.enter_context(plugin_root_leases.plugin_root_lock(path, blocking=blocking))
                except OSError:
                    if not blocking:
                        return False
                    raise
                _require_unused_plugin_root(path)
                _cleanup_managed_temp_locked(root, path, registry_path, recover_registry=recover_registry)
        finally:
            file_lock.release_lock(lock_file)
    return True


def _cleanup_managed_temp_locked(
    root: pathlib.Path,
    path: pathlib.Path,
    registry_path: pathlib.Path,
    *,
    recover_registry: bool,
) -> None:
    """対象別ロックを所有する呼び出し元のために後始末の状態遷移を実行する。"""
    _restore_interrupted_consume(registry_path)
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
    if os.name == "nt":
        # 利用中に追加された受理済みACEを除去し、隔離以降の処理は実行中のOSアカウントだけを許可するDACLの下で行う。
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
    path_arg: pathlib.Path | str,
    original_error: ManagedTempError,
    *,
    reason: str = "--force-remove",
    blocking: bool = True,
) -> bool:
    """親root、通常ディレクトリおよび所有者だけを確認して実体と登録を回収する。

    `blocking=False`で配布物ロックを即時取得できない場合は、対象を変えずに偽を返す。
    """
    try:
        _, path = _validate_path_shape(pathlib.Path(path_arg))
        _validate_root(path.parent)
        metadata = path.lstat()
        identity = _path_identity(path)
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

    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(plugin_root_leases.plugin_root_lock(path, blocking=blocking))
        except OSError:
            if not blocking:
                return False
            raise
        _require_unused_plugin_root(path)
        _force_remove_validated_temp(path, reason=reason, expected_identity=identity)
    return True


class _PluginRootRetained(ManagedTempError):
    """使用中または観測不能のため保持する配布物。"""

    basis: dict[str, typing.Any] | None = None


def _require_unused_plugin_root(path: pathlib.Path) -> None:
    if plugin_root_leases.plugin_root_in_use(path):
        error = _PluginRootRetained(
            f"使用中または使用状態を確認できない配布物を保持した: {path}。{plugin_root_leases.usage_details()}",
            next_action=f"利用プロセスの終了後に atk managed-temp cleanup --path {path} を再実行する",
        )
        error.basis = plugin_root_leases.usage_basis()
        raise error


def _force_remove_validated_temp(path: pathlib.Path, *, reason: str, expected_identity: tuple[int, int]) -> None:
    registry_path = _registry_path(path)
    try:
        if os.name == "posix":
            before = path.lstat()
            if (before.st_dev, before.st_ino) != expected_identity or before.st_uid != os.geteuid():
                raise ManagedTempError(f"強制回収の対象が置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION)
            with permissions.directory(path, expected=before) as opened:
                _clear_directory(opened)
                if _path_identity(path) != (before.st_dev, before.st_ino):
                    raise ManagedTempError(
                        f"強制回収の対象が置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION
                    )
                os.rmdir(path)
        else:
            if _path_identity(path) != expected_identity:
                raise ManagedTempError(f"強制回収の対象が置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION)
            snapshot = _tree_snapshot(path)
            _unlink_windows_reparse_points(path, snapshot)
            _remove_windows_quarantine(path, snapshot)
        registry_path.unlink(missing_ok=True)
        for consuming in registry_path.parent.glob(f"{registry_path.name}.consuming-*"):
            consuming.unlink(missing_ok=True)
    except OSError as error:
        raise ManagedTempError(f"管理対象を強制回収できない: {path}: {error}") from error
    _outcome.report_warning(
        f"{reason}により管理情報、登録および権限の検証を省いて管理対象を回収した: {path}",
        next_action=f"{path}を使用中だった場合は、回収の原因を確認してユーザーへ報告する",
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
    """登録済み領域の配下を、祖先の排他と既存の非追跡削除で回収する。"""
    path = pathlib.Path(path_arg)
    if not path.is_absolute() or ".." in path.parts or os.path.lexists(path / _MARKER_NAME):
        return False
    if not os.path.lexists(path) and not _registry_path(path).with_suffix(".child-cleanup").exists():
        return False
    ancestor = _registered_ancestor(path)
    if ancestor is None:
        return False
    lock = _registry_path(ancestor).with_suffix(".json.cleanup.lock")
    with lock.open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle)
        try:
            with plugin_root_leases.plugin_root_lock(ancestor):
                _require_unused_plugin_root(ancestor)
                parent = _validate_posix(ancestor) if os.name == "posix" else _validate_windows(ancestor)
                _cleanup_session_child(path, parent)
        finally:
            file_lock.release_lock(handle)
    print(
        f"note: 登録済みのmanaged-tempのディレクトリ{ancestor}の配下にあるため、個別の管理情報を経ずに削除した: {path}",
        file=sys.stderr,
    )
    return True


def _cleanup_session_child(path: pathlib.Path, parent: _ValidatedTemp) -> None:
    """子のidentityと隔離名を外部へ残し、失敗・中断後も同じpathで再開する。"""
    with contextlib.ExitStack() as stack:
        if os.name == "posix":
            opened = stack.enter_context(permissions.directory(parent.path, recover=False))
            for part in path.relative_to(parent.path).parts:
                try:
                    metadata = os.stat(part, dir_fd=opened, follow_symlinks=False)
                except FileNotFoundError:
                    break
                opened = stack.enter_context(permissions.directory(part, dir_fd=opened, expected=metadata))
        _cleanup_session_child_scoped(path, parent)


def _cleanup_session_child_scoped(path: pathlib.Path, parent: _ValidatedTemp) -> None:
    journal = _registry_path(path).with_suffix(".child-cleanup")
    root = _validate_root(parent.path)
    directory = parent.path
    for part in path.parent.relative_to(parent.path).parts:
        directory /= part
        root = _validate_root(directory, explicit=True)
    record: dict[str, typing.Any]
    if journal.exists():
        record = _load_private_json(journal)
        nonce = record.get("nonce")
        identity = record.get("identity")
        if (
            record.get("path") != str(path)
            or record.get("parent_identity") != [parent.device, parent.inode]
            or record.get("root_identity") != [root.device, root.inode]
            or not isinstance(nonce, str)
            or re.fullmatch(r"[0-9a-f]{64}", nonce) is None
            or not isinstance(identity, list)
            or len(identity) != 2
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in identity)
        ):
            raise ManagedTempError(f"managed-temp直下の作業ディレクトリの回収記録が一致しない: {path}")
        quarantine = path.parent / f".agent-toolkit-cleanup-{nonce}"
        if os.path.lexists(quarantine):
            if os.path.lexists(path) or _path_identity(quarantine) != tuple(identity):
                raise ManagedTempError(f"managed-temp直下の作業ディレクトリの隔離先が置換された: {path}")
            _cleanup_quarantine(path.parent, quarantine, (identity[0], identity[1]))
            journal.unlink()
            return
        if not os.path.lexists(path):
            journal.unlink()
            return
        if _path_identity(path) != tuple(identity):
            raise ManagedTempError(f"回収中のmanaged-temp直下の作業ディレクトリが置換された: {path}")
    else:
        child = _validate_root(path, explicit=True)
        nonce = secrets.token_hex(32)
        record = {
            "path": str(path),
            "identity": [child.device, child.inode],
            "parent_identity": [parent.device, parent.inode],
            "root_identity": [root.device, root.inode],
            "nonce": nonce,
        }
        _write_private_json(journal, record)
    validated = _ValidatedTemp(
        path,
        record["identity"][0],
        record["identity"][1],
        nonce,
        journal,
        record,
        root.device,
        root.inode,
        root.owner,
        root.mode,
        root.security,
    )
    quarantine = path.parent / f".agent-toolkit-cleanup-{nonce}"
    try:
        before = _tree_snapshot(path)
        if os.name == "posix":
            if not shutil.rmtree.avoids_symlink_attacks:
                raise ManagedTempError("symlink attack耐性を持つ後始末手段を利用できない")
            _cleanup_posix(path.parent, validated, quarantine, before)
        else:
            _windows_secure_path(path, directory=True, expected_identity=(validated.device, validated.inode))
            _cleanup_windows(path.parent, validated, quarantine, before)
        journal.unlink()
    except (OSError, ManagedTempError) as error:
        raise ManagedTempError(
            f"managed-temp直下の作業ディレクトリを回収できない: {path}: {error}。親と回収記録を保持した",
            next_action=ManagedTempError.RETRYABLE_NEXT_ACTION,
        ) from error


def cleanup_managed_temp(
    path_arg: pathlib.Path | str | None = None,
    *,
    session_id: str | None = None,
    recover_registry: bool = False,
    force_remove: bool = False,
    force_reason: str = "--force-remove",
) -> None:
    """通常の後始末を行い、明示指定時だけ検証失敗後の強制回収を試みる。

    `force_reason`は、強制回収を報告する警告に、回収を指示した操作として表示する語である。
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
    try:
        if _cleanup_child_of_registered_temp(path_arg):
            return
        _cleanup_managed_temp(path_arg, recover_registry=recover_registry)
    except _PluginRootRetained:
        # 親の使用保護も子のforce-removeへ引き継ぐ。子自身の名前だけで再判定すると保護を失う。
        raise
    except (ManagedTempError, OSError) as error:
        failure = (
            error
            if isinstance(error, ManagedTempError)
            else ManagedTempError(
                f"管理対象を後始末できない: {path_arg}: {error}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        )
        if force_remove:
            _force_remove_managed_temp(path_arg, failure, reason=force_reason)
        elif failure is error:
            raise
        else:
            raise failure from error
