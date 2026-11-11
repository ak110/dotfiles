"""計画バンドルのcheckoutと取得記録の管理。"""

from __future__ import annotations

import json
import os
import pathlib

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import plans_bundle as _plans_bundle
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._plan import bundle_kinds as _bundle_kinds
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import owner_records as _owner_records


def checkout_record_root(relative_main: pathlib.Path) -> pathlib.Path:
    """指定計画stemのcheckout記録ディレクトリを返す。"""
    return _state_paths.state_dir() / "plan-checkouts" / relative_main.stem


def validate_saved_plan_relative_path(plan_file: str) -> pathlib.Path:
    """正規形式または移行済み形式の`private-notes/plans/`相対メイン計画パスを返す。"""
    try:
        return _plan_file.validate_plan_relative_path(plan_file)
    except ValueError as canonical_error:
        try:
            return _plan_file.validate_migrated_plan_relative_path(plan_file)
        except ValueError as migrated_error:
            raise _wi_web_input.web_input_error_from(
                canonical_error, next_action=_plans_bundle.PLAN_PATH_NEXT_ACTION
            ) from migrated_error


def validate_working_ci_review_relative_path(review_table: str) -> pathlib.Path:
    """`~/.claude/plans`直下のCI対応レビュー指摘管理表名を返す。"""
    next_action = f"{_atk_help.CI_REVIEW_TABLE_NAME_FORMAT}の形式で指定し直す"
    if "\0" in review_table or "\\" in review_table or "$(" in review_table:
        raise _wi_web_input.WebInputError("CI対応レビュー指摘管理表のパスが不正です", next_action=next_action)
    relative = pathlib.Path(review_table)
    if relative.parent != pathlib.Path() or _plans_bundle.CI_REVIEW_NAME_RE.fullmatch(relative.name) is None:
        raise _wi_web_input.WebInputError(
            f"CI対応レビュー指摘管理表は{_atk_help.CI_REVIEW_TABLE_NAME_FORMAT}で指定してください",
            next_action=next_action,
        )
    return relative


def validate_saved_ci_review_relative_path(review_table: str) -> pathlib.Path:
    """Plans root相対のCI対応レビュー指摘管理表パスを返す。"""
    next_action = f"ci/{_atk_help.CI_REVIEW_TABLE_NAME_FORMAT}の形式で指定し直す"
    if "\0" in review_table or "\\" in review_table or "$(" in review_table:
        raise _wi_web_input.WebInputError("CI対応レビュー指摘管理表のパスが不正です", next_action=next_action)
    relative = pathlib.Path(review_table)
    if relative.parent != _plans_bundle.CI_REVIEW_DIRECTORY or _plans_bundle.CI_REVIEW_NAME_RE.fullmatch(relative.name) is None:
        raise _wi_web_input.WebInputError(
            f"保存済みのCI対応レビュー指摘管理表はci/{_atk_help.CI_REVIEW_TABLE_NAME_FORMAT}で指定してください",
            next_action=next_action,
        )
    return relative


def validate_saved_checkout_relative_path(path: str) -> pathlib.Path:
    """checkout記録が受理する計画またはCI対応レビュー指摘管理表の相対パスを返す。"""
    if _bundle_kinds.EXEC_REVIEW.matches(path):
        return validate_saved_ci_review_relative_path(path)
    return validate_saved_plan_relative_path(path)


def read_checkout_record(relative_main: pathlib.Path) -> tuple[pathlib.Path, dict[str, bytes]] | None:
    """checkout記録を検証して読み込む。"""
    root = checkout_record_root(relative_main)
    if not root.exists():
        return None
    # 取得記録は取得時点の内容を持つ写しであり、壊れた記録を削除しても作業側は失われない。
    next_action = (
        f"`~/.claude/plans`直下の該当計画バンドルを`~/.claude/plans`の外へ退避してから取得記録{root}を削除し、"
        "`atk plans checkout <private-notes/plans/相対パス>`で再取得する"
    )
    if root.is_symlink() or not root.is_dir():
        raise _wi_web_input.WebInputError(f"計画の取得記録が不正です: {root}", next_action=next_action)
    try:
        raw_metadata = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        recorded_relative = validate_saved_checkout_relative_path(raw_metadata["relative_main"])
    except (OSError, KeyError, TypeError, _wi_web_input.WebInputError, json.JSONDecodeError) as error:
        raise _wi_web_input.WebInputError(f"計画の取得記録を読み取れません: {root}", next_action=next_action) from error
    if recorded_relative.stem != relative_main.stem:
        raise _wi_web_input.WebInputError(f"計画の取得記録とstemが一致しません: {root}", next_action=next_action)
    files_root = root / "files"
    if files_root.is_symlink() or not files_root.is_dir():
        raise _wi_web_input.WebInputError(f"計画の取得記録にファイルsnapshotがありません: {root}", next_action=next_action)
    snapshots: dict[str, bytes] = {}
    for path in files_root.iterdir():
        if path.is_symlink() or not path.is_file() or _plans_bundle.excluded_path(path):
            raise _wi_web_input.WebInputError(f"計画の取得記録に不正なファイルがあります: {path}", next_action=next_action)
        snapshots[path.name] = path.read_bytes()
    if recorded_relative.name not in snapshots:
        raise _wi_web_input.WebInputError(f"計画の取得記録にメイン計画がありません: {root}", next_action=next_action)
    return recorded_relative, snapshots


def remove_checkout_record(relative_main: pathlib.Path) -> None:
    """指定計画のcheckout記録だけを回収する。"""
    root = checkout_record_root(relative_main)
    if not root.exists():
        return
    files_root = root / "files"
    if files_root.is_dir() and not files_root.is_symlink():
        for path in files_root.iterdir():
            if path.is_file() and not path.is_symlink():
                path.unlink()
        files_root.rmdir()
    metadata = root / "meta.json"
    if metadata.is_file() and not metadata.is_symlink():
        metadata.unlink()
    root.rmdir()


def write_checkout_record(
    relative_main: pathlib.Path,
    snapshots: dict[str, bytes],
    *,
    duplicate_error: _wi_web_input.WebInputError,
) -> None:
    """取得元と取得時点のbytesをcheckout記録へ排他的に保存する。"""
    root = checkout_record_root(relative_main)
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise duplicate_error from error
    try:
        files_root = root / "files"
        files_root.mkdir()
        (root / "meta.json").write_text(
            json.dumps({"relative_main": relative_main.as_posix()}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        for name, content in snapshots.items():
            (files_root / name).write_bytes(content)
    except Exception:
        remove_checkout_record(relative_main)
        raise


def checkout_plan(
    private_notes: pathlib.Path,
    plan_file: str,
    *,
    home: pathlib.Path | str | None = None,
) -> tuple[pathlib.Path, ...]:
    """保存済み計画バンドルを`~/.claude/plans`へ取得し、取得時点と所有セッションを記録する。"""
    if _bundle_kinds.EXEC_REVIEW.matches(plan_file):
        return checkout_ci_review(private_notes, plan_file, home=home)
    relative_main = validate_saved_plan_relative_path(
        _plan_file.resolve_plan_input(plan_file, private_notes=private_notes, home=home, saved_only=True).as_posix()
    )
    working_root = _plan_file.working_plans_root(home)
    working_main = working_root / relative_main.name
    duplicate_error = _wi_web_input.WebInputError(
        f"同じ計画を取得済みです: {relative_main}",
        next_action=(
            "`~/.claude/plans`直下にその計画バンドルがある場合は、それが取得結果のため再取得は不要です。"
            "`~/.claude/plans`直下にその計画バンドルが無い場合は、"
            f"`atk plans commit {working_main.name}`で取得記録を回収してください。"
        ),
    )
    with _atk_git_sync.repo_lock(private_notes):
        if checkout_record_root(relative_main).exists():
            raise duplicate_error
        if _atk_git_sync.has_remote(private_notes):
            _atk_git_sync.push_pending_commits(private_notes)
            _atk_git_sync.pull(private_notes)
        saved_bundle = _plans_bundle.saved_plan_bundle(private_notes, relative_main)
        if not saved_bundle:
            raise _wi_web_input.WebInputError(
                f"指定したメイン計画が見つかりません: {relative_main}",
                next_action=_plans_bundle.PLAN_PATH_NEXT_ACTION,
            )
        destinations = tuple(working_root / path.name for path in saved_bundle)
        created_record = working_main.with_name(_bundle_kinds.CREATED_RECORD.name_for(working_main.stem))
        conflicts = tuple(path for path in (*destinations, created_record) if path.exists() or path.is_symlink())
        if conflicts:
            raise _wi_web_input.WebInputError(
                f"`~/.claude/plans`に同名ファイルがあります: {conflicts[0]}",
                next_action="`~/.claude/plans`側のファイルを`~/.claude/plans`の外へ退避してから再実行する",
            )
        snapshots = {path.name: path.read_bytes() for path in saved_bundle}
        working_root.mkdir(parents=True, exist_ok=True)
        copied: list[pathlib.Path] = []
        try:
            for destination in destinations:
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(snapshots[destination.name])
                copied.append(destination)
            with created_record.open("x", encoding="utf-8") as output:
                copied.append(created_record)
                date = f"{relative_main.parts[0]}-{relative_main.parts[1]}-{relative_main.name[:2]}"
                output.write(json.dumps({"date": date}) + "\n")
            write_checkout_record(relative_main, snapshots, duplicate_error=duplicate_error)
        except Exception:
            for path in copied:
                path.unlink(missing_ok=True)
            raise
        _owner_records.record_plan_owner(working_main)
        return destinations


def checkout_ci_review(
    private_notes: pathlib.Path,
    review_table: str,
    *,
    home: pathlib.Path | str | None = None,
) -> tuple[pathlib.Path, ...]:
    """保存済みのCI対応レビュー指摘管理表を`~/.claude/plans`へ取得する。"""
    relative = validate_saved_ci_review_relative_path(
        _plan_file.resolve_plan_input(review_table, private_notes=private_notes, home=home, saved_only=True).as_posix()
    )
    working = _plan_file.working_plans_root(home) / relative.name
    duplicate_error = _wi_web_input.WebInputError(
        f"同じCI対応レビュー指摘管理表を取得済みです: {relative}",
        next_action=(
            "`~/.claude/plans`直下にその表がある場合は、それが取得結果のため再取得は不要です。"
            f"`~/.claude/plans`直下にその表が無い場合は、`atk plans commit {working.name}`で取得記録を回収してください。"
        ),
    )
    with _atk_git_sync.repo_lock(private_notes):
        if checkout_record_root(relative).exists():
            raise duplicate_error
        if _atk_git_sync.has_remote(private_notes):
            _atk_git_sync.push_pending_commits(private_notes)
            _atk_git_sync.pull(private_notes)
        saved = _plan_file.new_plans_root(private_notes) / relative
        if saved.is_symlink() or not saved.is_file():
            raise _wi_web_input.WebInputError(
                f"指定したCI対応レビュー指摘管理表が見つかりません: {relative}",
                next_action="`private-notes/plans/`相対のci/ci-{短縮OID}.exec-review.tsvを確かめて指定し直す",
            )
        if working.exists():
            raise _wi_web_input.WebInputError(
                f"`~/.claude/plans`に同名ファイルがあります: {working}",
                next_action="`~/.claude/plans`側のファイルを`~/.claude/plans`の外へ退避してから再実行する",
            )
        content = saved.read_bytes()
        working.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(working, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
            write_checkout_record(relative, {relative.name: content}, duplicate_error=duplicate_error)
        except Exception:
            working.unlink(missing_ok=True)
            raise
        return (working,)
