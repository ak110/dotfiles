"""計画バンドルとCI対応レビュー表の対象限定commit。"""

from __future__ import annotations

import os
import pathlib

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import plans_bundle as _plans_bundle
from agent_toolkit._atk import plans_checkout as _plans_checkout
from agent_toolkit._atk import plans_storage as _plans_storage
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._git import command as _git_command
from agent_toolkit._plan import bundle_kinds as _bundle_kinds
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import owner_records as _owner_records


def commit_plan(
    private_notes: pathlib.Path,
    plan_file: str,
    *,
    home: pathlib.Path | str | None = None,
    lock_timeout: float | None = None,
    skip_push: bool = False,
) -> dict[str, object]:
    """指定計画bundleを`private-notes/plans/`へ移し、対象限定commitを作成する。

    作業バンドルを回収する時点でその計画の所有記録も回収し、`~/.claude/plans`へ記録だけが残らないようにする。
    """
    if _bundle_kinds.EXEC_REVIEW.matches(plan_file):
        return commit_ci_review(
            private_notes,
            plan_file,
            home=home,
            lock_timeout=lock_timeout,
            skip_push=skip_push,
        )
    plan_file = _plan_file.resolve_plan_input(plan_file, private_notes=private_notes, home=home).as_posix()
    working_relative: pathlib.Path | None = None
    try:
        working_relative = _plan_file.validate_working_plan_relative_path(plan_file)
        relative_main = working_relative
    except ValueError as working_error:
        try:
            relative_main = _plan_file.validate_plan_relative_path(plan_file)
        except ValueError as saved_error:
            try:
                relative_main = _plan_file.validate_migrated_plan_relative_path(plan_file)
            except ValueError:
                raise _wi_web_input.web_input_error_from(
                    working_error, next_action=_plans_bundle.PLAN_PATH_NEXT_ACTION
                ) from saved_error
    requested_relative = working_relative or relative_main
    checkout_record = _plans_checkout.read_checkout_record(requested_relative)
    recorded_contents: dict[str, bytes] = {}
    if checkout_record is not None:
        relative_main, recorded_contents = checkout_record
        working_lookup_relative = pathlib.Path(relative_main.name)
    else:
        working_lookup_relative = requested_relative
    working_bundle = _plans_bundle.working_plan_bundle(home, working_lookup_relative)
    working_main = _plan_file.working_plans_root(home) / working_lookup_relative
    if checkout_record is None and working_relative is None and not working_bundle:
        working_root = _plan_file.working_plans_root(home)
        residue = (
            tuple(
                sorted(
                    path
                    for path in working_root.iterdir()
                    if (path.name == relative_main.name or path.name.startswith(f"{relative_main.stem}."))
                    and path.is_file()
                    and not path.is_symlink()
                    and not _plans_bundle.excluded_path(path)
                )
            )
            if working_root.is_dir()
            else ()
        )
        if residue:
            names = "、".join(path.name for path in residue)
            raise _wi_web_input.WebInputError(
                f"`~/.claude/plans`直下に保存済み計画バンドルと同じstemのファイルが残っています: {names}。"
                "保存先へ反映していないため、この状態では保存を完了できません。",
                next_action=(
                    f"`~/.claude/plans`直下の{names}を`~/.claude/plans`の外へ退避し、保存済み計画を正とするか、"
                    "退避した内容を別名の新しい計画として保存してください。"
                ),
            )
    if checkout_record is not None:
        if not working_bundle:
            _plans_checkout.remove_checkout_record(requested_relative)
            _owner_records.remove_owner_record(working_main)
            working_main.with_name(_bundle_kinds.CREATED_RECORD.name_for(working_main.stem)).unlink(missing_ok=True)
            return {"plan_file": relative_main.as_posix(), "paths": (), "message": ""}
    elif working_relative is not None and not working_bundle:
        raise _wi_web_input.WebInputError(
            f"指定した作業中の計画バンドルが見つかりません: {working_relative}",
            next_action=(
                "保存済みの場合は`atk plans checkout <private-notes/plans/相対パス>`で取得してください。"
                "作業中の計画は`atk plans list`で確認できる"
            ),
        )
    if checkout_record is None and working_relative is not None and working_bundle:
        working_main = _plan_file.working_plans_root(home) / working_relative
        date = _plans_storage.birth_date(working_main).split("/")
        relative_main = pathlib.Path(date[0], date[1], working_relative.name)
        try:
            _plans_checkout.validate_saved_plan_relative_path(relative_main.as_posix())
        except _wi_web_input.WebInputError as error:
            record = working_main.with_name(_bundle_kinds.CREATED_RECORD.name_for(working_main.stem))
            raise _wi_web_input.WebInputError(
                f"保存年月{date[0]}/{date[1]}と計画名の日{working_relative.name[:2]}が実在する日付になりません",
                next_action=f"{record}のdateを正しい作成日（YYYY-MM-DD）へ直し、計画名の日も確認して再実行する",
            ) from error
    with _atk_git_sync.repo_lock(private_notes, timeout=lock_timeout):
        saved_main = _plan_file.new_plans_root(private_notes) / relative_main
        saved_bundle_exists = saved_main.is_file()
        if checkout_record is not None and _atk_git_sync.has_remote(private_notes):
            _git_command.run_quiet(["fetch"], cwd=private_notes)
        elif not working_bundle or not saved_bundle_exists:
            if not skip_push:
                _atk_git_sync.push_pending_commits(private_notes)
            _atk_git_sync.pull(private_notes)
        snapshots: dict[pathlib.Path, tuple[tuple[int, int], bytes]] = {}
        if checkout_record is not None:
            saved_bundle = _plans_bundle.saved_plan_bundle(private_notes, relative_main)
            if not saved_bundle:
                raise _wi_web_input.WebInputError(
                    f"取得元の計画バンドルが見つかりません: {relative_main}",
                    next_action=(
                        f"`git -C {private_notes} log -- {_plan_file.NEW_PLANS_DIRECTORY}/{relative_main.as_posix()}`で"
                        "保存元の削除・移動を確認する。作業側の内容を残す場合は別名の新しい計画として保存する"
                    ),
                )
            saved_contents = _plans_storage.bundle_contents(saved_bundle)
            working_contents = _plans_storage.bundle_contents(working_bundle)
            remote_contents = (
                _plans_bundle.bundle_contents_at_ref(private_notes, relative_main, "@{u}")
                if _atk_git_sync.has_remote(private_notes)
                else saved_contents
            )
            recorded_current = _plans_bundle.current_bundle_contents(recorded_contents, relative_main.name)
            saved_current = _plans_bundle.current_bundle_contents(saved_contents, relative_main.name)
            remote_current = _plans_bundle.current_bundle_contents(remote_contents, relative_main.name)
            if saved_current not in (recorded_current, working_contents) or remote_current not in (
                recorded_current,
                working_contents,
            ):
                differences = _plans_storage.checkout_conflict_differences(
                    recorded_current,
                    working_contents,
                    saved_current,
                    remote_current,
                )
                bundle_names = "、".join(sorted(working_contents))
                raise _wi_web_input.WebInputError(
                    f"取得後に保存元の計画バンドルが変更されています: {relative_main}。"
                    "取得時点・作業側・保存元の内容が一致しないため、どれを正とするかが確定するまで"
                    f"保存も取得もできません。相違した対象は{differences}です。",
                    next_action=(
                        "次の順に実行してください。"
                        f"`~/.claude/plans`直下の{bundle_names}を`~/.claude/plans`の外へ退避します。"
                        f"`atk plans commit {working_main.name}`を実行すると、"
                        "作業バンドルが不在のため取得記録だけを回収します。"
                        "保存済み計画を確認し、退避した内容を残す場合は別名の新しい計画として保存します。"
                    ),
                )
            snapshots = _plans_storage.working_snapshots(working_bundle)
            if saved_current == recorded_current:
                legacy_contents = {name: content for name, content in saved_contents.items() if name not in saved_current}
                _plans_storage.update_saved_bundle(saved_main.parent, saved_bundle, legacy_contents | working_contents)
            bundle = _plans_bundle.plan_bundle(private_notes, relative_main)
        elif working_bundle:
            bundle, snapshots = _plans_storage.copy_working_bundle(private_notes, relative_main, working_bundle)
        else:
            bundle = _plans_bundle.plan_bundle(private_notes, relative_main)
        relative_paths = tuple(_plans_bundle.as_relative_notes_path(path, private_notes) for path in bundle)
        message = f"chore: update plan {_plans_storage.plan_display_name(relative_main)}"
        _atk_git_sync.commit_and_push(private_notes, message, relative_paths, skip_push=skip_push)
        if not skip_push and _atk_git_sync.has_remote(private_notes) and not _atk_git_sync.remote_contains_head(private_notes):
            raise _wi_web_input.WebInputError(
                "計画commitがremote branchへ到達したことを確認できません", next_action=_plans_bundle.UNPUSHED_NEXT_ACTION
            )
        if checkout_record is not None:
            _plans_storage.remove_checked_out_working_bundle(working_bundle, snapshots, requested_relative)
            _plans_checkout.remove_checkout_record(requested_relative)
            _owner_records.remove_owner_record(working_main)
            working_main.with_name(_bundle_kinds.CREATED_RECORD.name_for(working_main.stem)).unlink(missing_ok=True)
        elif working_bundle:
            _plans_storage.remove_finalized_working_bundle(working_bundle, snapshots, bundle, relative_main, home)
            _owner_records.remove_owner_record(working_main)
            working_main.with_name(_bundle_kinds.CREATED_RECORD.name_for(working_main.stem)).unlink(missing_ok=True)
    return {"plan_file": relative_main.as_posix(), "paths": relative_paths, "message": message}


def commit_ci_review(
    private_notes: pathlib.Path,
    review_table: str,
    *,
    home: pathlib.Path | str | None = None,
    lock_timeout: float | None = None,
    skip_push: bool = False,
) -> dict[str, object]:
    """CI対応レビュー指摘管理表を対象限定でcommitし、成功後に作業側を回収する。"""
    review_table = _plan_file.resolve_plan_input(review_table, private_notes=private_notes, home=home).as_posix()
    try:
        working_relative = _plans_checkout.validate_working_ci_review_relative_path(review_table)
        requested_relative = working_relative
    except _wi_web_input.WebInputError as working_error:
        try:
            saved_relative = _plans_checkout.validate_saved_ci_review_relative_path(review_table)
        except _wi_web_input.WebInputError as saved_error:
            raise working_error from saved_error
        requested_relative = saved_relative
        working_relative = pathlib.Path(saved_relative.name)
    checkout_record = _plans_checkout.read_checkout_record(requested_relative)
    recorded_contents: dict[str, bytes] = {}
    relative = _plans_bundle.CI_REVIEW_DIRECTORY / working_relative.name
    if checkout_record is not None:
        relative, recorded_contents = checkout_record
    working = _plan_file.working_plans_root(home) / working_relative.name
    if checkout_record is not None and not working.exists():
        _plans_checkout.remove_checkout_record(requested_relative)
        return {"plan_file": relative.as_posix(), "paths": (), "message": "", "kind": "ci-review"}
    if working.is_symlink() or not working.is_file():
        raise _wi_web_input.WebInputError(
            f"指定したCI対応レビュー指摘管理表が見つかりません: {working_relative}",
            next_action=(
                "`~/.claude/plans`直下の表の名前を確かめて指定し直す。"
                "保存済みの表は`atk plans checkout ci/<ファイル名>`で取得してから編集する"
            ),
        )
    snapshots = _plans_storage.working_snapshots((working,))
    working_contents = {working.name: snapshots[working][1]}
    with _atk_git_sync.repo_lock(private_notes, timeout=lock_timeout):
        saved = _plan_file.new_plans_root(private_notes) / relative
        if checkout_record is not None and _atk_git_sync.has_remote(private_notes):
            _git_command.run_quiet(["fetch"], cwd=private_notes)
        elif not saved.exists():
            if not skip_push:
                _atk_git_sync.push_pending_commits(private_notes)
            _atk_git_sync.pull(private_notes)
        if saved.exists() and (saved.is_symlink() or not saved.is_file()):
            raise _wi_web_input.WebInputError(
                f"保存先のCI対応レビュー指摘管理表が通常ファイルではありません: {saved}",
                next_action=f"`git -C {private_notes} status`で保存先の状態を確認し、原因が分からない場合はユーザーへ報告する",
            )
        saved_contents = {saved.name: saved.read_bytes()} if saved.is_file() else {}
        if checkout_record is not None:
            remote_contents = (
                _plans_bundle.bundle_contents_at_ref(private_notes, relative, "@{u}")
                if _atk_git_sync.has_remote(private_notes)
                else saved_contents
            )
            if saved_contents not in (recorded_contents, working_contents) or remote_contents not in (
                recorded_contents,
                working_contents,
            ):
                differences = _plans_storage.checkout_conflict_differences(
                    recorded_contents,
                    working_contents,
                    saved_contents,
                    remote_contents,
                )
                raise _wi_web_input.WebInputError(
                    f"取得後に保存元のCI対応レビュー指摘管理表が変更されています: {relative}。"
                    "取得時点・作業側・保存元の内容が一致しないため、どれを正とするかが確定するまで"
                    f"保存も取得もできません。相違した対象は{differences}です。",
                    next_action=(
                        "次の順に実行してください。"
                        f"`~/.claude/plans`直下の{working.name}を`~/.claude/plans`の外へ退避します。"
                        f"`atk plans commit {working.name}`を実行すると、作業側が不在のため取得記録だけを回収します。"
                        "保存済みの表を確認し、退避した内容を残す場合は、"
                        f"{_atk_help.CI_REVIEW_TABLE_NAME_FORMAT}の書式で別の短縮OIDを名前に持つ表として保存します。"
                    ),
                )
        elif saved_contents and saved_contents != working_contents:
            raise _wi_web_input.WebInputError(
                _plans_bundle.SAVED_BUNDLE_CONFLICT_MESSAGE.format(destination=saved),
                next_action=_plans_bundle.SAVED_BUNDLE_CONFLICT_NEXT_ACTION,
            )
        if saved_contents != working_contents:
            saved.parent.mkdir(parents=True, exist_ok=True)
            if saved_contents:
                saved.write_bytes(working_contents[working.name])
            else:
                descriptor = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(working_contents[working.name])
        if saved.read_bytes() != working_contents[working.name]:
            raise _wi_web_input.WebInputError(
                f"保存したCI対応レビュー指摘管理表の読戻し内容が一致しません: {saved}",
                next_action=_plans_bundle.READBACK_NEXT_ACTION,
            )
        relative_path = _plans_bundle.as_relative_notes_path(saved, private_notes)
        message = f"chore: update CI review {working.stem}"
        _atk_git_sync.commit_and_push(private_notes, message, (relative_path,), skip_push=skip_push)
        if not skip_push and _atk_git_sync.has_remote(private_notes) and not _atk_git_sync.remote_contains_head(private_notes):
            raise _wi_web_input.WebInputError(
                "CI対応レビュー指摘管理表のcommitがremote branchへ到達したことを確認できません",
                next_action=_plans_bundle.UNPUSHED_NEXT_ACTION,
            )
        _plans_storage.remove_checked_out_working_bundle((working,), snapshots, working_relative)
        if checkout_record is not None:
            _plans_checkout.remove_checkout_record(requested_relative)
    return {"plan_file": relative.as_posix(), "paths": (relative_path,), "message": message, "kind": "ci-review"}
