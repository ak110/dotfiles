# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
"""agent-toolkitプラグイン配下の`atk wi`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_mutations.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import typing

from _plan import locations as _plan_file
from _plan import structure as _plan_format

from _atk import git_sync as _atk_git_sync
from _atk.wi import add as _add
from _atk.wi import frontmatter as _frontmatter
from _atk.wi import remove_all as _remove_all
from _atk.wi import user_comment as _user_comment
from _atk.wi import uwi as _uwi
from _atk.wi.common import (
    TRANSITION_EXPLICIT_STATES,
    WI_PROCESSABLE_STATES,
    WI_STATE_ADOPTED,
    WI_STATE_HOLD,
    WI_STATE_INBOX,
    WI_STATE_PROCESSING,
    WI_STATE_REJECTED,
    WI_STATES,
    WI_TYPE_AWI,
    WI_TYPE_UWI,
    WebInputError,
    _commit_and_push,
    _copy_to_tempfile,
    _dedup_positional_filenames,
    _pull,
    _push_pending_commits,
    _repo_lock,
    _require_type,
    _stamp_result,
    _subdir,
    _validate_filename,
    _validate_filenames_only,
    is_agent_environment,
    normalized_wi_type,
)
from _atk.wi.repo import (
    _normalize_remote_url,
    _resolve_repo_id,
    _verify_target_repo_content,
)
from _atk.wi.repo import append_entry as _append_entry
from _atk.wi.repo import edit_entry as _edit_entry


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _atk.wi.mutations.targets import (
        _GIT_TIMEOUT_SECONDS,
        _atomic_write_text,
        _candidate_local_worktree,
        _cmd_commit,
        _commit_values_by_path,
        _entry_target_repo,
        _git_head,
        _invalidate_repo_bound_metadata,
        _local_worktree_repo_id,
        _resolve_awi_targets,
        _resolve_commit,
        _resolve_conversion_targets,
        _resolve_processable_targets,
        _resolve_removable_targets,
        commit_entries,
    )
    from _atk.wi.mutations.content import (
        _build_noninteractive_edit_content,
        _cmd_append,
        _cmd_edit,
        _preserve_agent_user_comment,
        _reject_agent_user_comment_change,
        _reject_agent_user_comment_message,
        append_entry_content,
        edit_entry_content,
    )
    from _atk.wi.mutations.plan_conversion import (
        _PlanAwiValidationError,
        _StoredPlanFile,
        _assert_conversion_paths_clean,
        _assert_conversion_targets_tracked,
        _cmd_convert_to_plan,
        _convert_held_entries,
        _normalize_stored_plan_file,
        _plan_awi_paths,
        _read_plan_input_filenames,
        _resolve_plan_base_commit,
        _restore_conversion_paths,
        _store_plan_file,
        _validated_plan_awi_paths,
        convert_entries_to_plan,
        convert_entry_to_plan,
        edit_entry_to_plan,
    )
    from _atk.wi.mutations.dependencies import (
        _active_dependency_graph,
        _cmd_set_dependencies,
        _dependency_reaches,
        _entry_dependencies,
        _entry_dependencies_for_conversion,
        set_entry_dependencies,
    )


def _validate_transition_options(
    action: str,
    filenames: list[str],
    *,
    state: str | None,
    expected_content: str | None,
    cooldown_days: int | None,
) -> None:
    """状態遷移オプション間の制約を検証する。"""
    if action not in {"start-processing", "return-to-inbox", "hold", "unhold", "adopt", "reject", "remove"}:
        raise WebInputError(f"未知のエントリ操作です: {action}")
    if cooldown_days is not None and (action != "return-to-inbox" or cooldown_days < 3):
        raise WebInputError("cooldown_daysはreturn-to-inboxで3以上を指定してください")
    accepted_states = TRANSITION_EXPLICIT_STATES.get(action, ())
    state_is_valid = state in accepted_states
    if state is not None and not state_is_valid:
        rendered_states = "、".join(accepted_states) if accepted_states else "なし"
        raise WebInputError(f"操作{action}はstate={state}を受理しません。明示stateとして受理する状態: {rendered_states}")
    if expected_content is not None and (action != "remove" or len(filenames) != 1):
        raise WebInputError("expected_contentはremoveで1件を指定する場合に限り使用できます")


def _resolve_transition_paths(
    private_notes: pathlib.Path,
    action: str,
    filenames: list[str],
    state: str | None,
    *,
    missing_is_conflict: bool,
) -> list[pathlib.Path]:
    """操作種別と明示状態から対象エントリを解決する。"""
    inbox_dir = private_notes / WI_STATE_INBOX
    processing_dir = _subdir(private_notes, WI_STATE_PROCESSING)
    if state is not None:
        return _resolve_awi_targets(filenames, private_notes / state, missing_is_conflict=missing_is_conflict)
    if action == "start-processing":
        return _resolve_awi_targets(filenames, inbox_dir, missing_is_conflict=missing_is_conflict)
    if action == "return-to-inbox":
        return _resolve_awi_targets(filenames, processing_dir, missing_is_conflict=missing_is_conflict)
    if action == "unhold":
        return _resolve_awi_targets(
            filenames,
            _subdir(private_notes, WI_STATE_HOLD),
            missing_is_conflict=missing_is_conflict,
        )
    if action == "remove":
        return _resolve_removable_targets(
            filenames,
            inbox_dir,
            processing_dir,
            missing_is_conflict=missing_is_conflict,
        )
    return _resolve_processable_targets(filenames, inbox_dir, processing_dir, missing_is_conflict=missing_is_conflict)


def _validate_transition_targets(
    paths: list[pathlib.Path],
    *,
    action: str,
    target_repo: str | None,
    expected_content: str | None,
    cooldown_days: int | None,
    force: bool,
) -> str | None:
    """解決済み対象の内容・対象repo・状態保護を検証する。"""
    current_content: str | None = None
    if action == "remove" and expected_content is not None:
        try:
            current_content = paths[0].read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise RuntimeError("編集中に他プロセスが対象を変更しました") from error
        if current_content != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
    normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None
    for path in paths:
        content = current_content if current_content is not None else path.read_text(encoding="utf-8")
        if action == "start-processing":
            # 移動前にtype欠落・不正を拒否する。
            _require_type(path, content)
            # `--target-repo`未指定でもtarget_repo欠落とfrontmatter解析不能を拒否するため、
            # 不一致判定を`_verify_target_repo_content`へ委ねる一方でこの必須検査は残す。
            _entry_target_repo(path, content)
        _verify_target_repo_content(path, content, normalized_target_repo)
    if cooldown_days is not None:
        non_awi = [path.name for path in paths if _require_type(path, path.read_text(encoding="utf-8")) != WI_TYPE_AWI]
        if non_awi:
            raise WebInputError(f"--`cooldown-days`はAWI専用です: {', '.join(non_awi)}")
    if action == "remove" and not force:
        protected = [path.name for path in paths if path.parent.name == WI_STATE_PROCESSING]
        if protected:
            print(
                "processing状態のファイルは既定で削除を保護します。"
                f"削除するには--force（Web APIはforce指定）を指定してください: {', '.join(protected)}",
                file=sys.stderr,
            )
            sys.exit(2)
    return current_content


def _update_transition_metadata(
    paths: list[pathlib.Path],
    *,
    action: str,
    now: datetime.datetime,
    cooldown_days: int | None,
) -> None:
    """active状態間の移動前にcooldownメタデータを更新する。"""
    if action not in {"start-processing", "return-to-inbox"}:
        return
    for path in paths:
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            continue
        data, body = parsed
        if action == "return-to-inbox" and cooldown_days is not None:
            deadline = now.astimezone(datetime.UTC) + datetime.timedelta(days=cooldown_days)
            data["cooldown_until"] = deadline.isoformat()
        else:
            data.pop("cooldown_until", None)
        updated = _frontmatter.serialize_frontmatter(data, body)
        if updated != text:
            _atomic_write_text(path, updated)


def _strip_result_section(path: pathlib.Path) -> None:
    """末尾にある最後の`## 処理結果`節を取り除く。"""
    text = path.read_text(encoding="utf-8")
    matches = tuple(re.finditer(r"(?m)^## 処理結果[ \t]*\r?\n", text))
    if not matches:
        return
    updated = text[: matches[-1].start()].rstrip() + "\n"
    _atomic_write_text(path, updated)


def _apply_transition(
    private_notes: pathlib.Path,
    paths: list[pathlib.Path],
    *,
    action: str,
    now: datetime.datetime,
    note: str | None,
    commit_values: dict[pathlib.Path, str | None],
    cooldown_days: int | None,
) -> None:
    """検証済みエントリを削除又は目的状態へ移動する。"""
    destination_name = {
        "start-processing": WI_STATE_PROCESSING,
        "return-to-inbox": WI_STATE_INBOX,
        "hold": WI_STATE_HOLD,
        "unhold": WI_STATE_INBOX,
        "adopt": WI_STATE_ADOPTED,
        "reject": WI_STATE_REJECTED,
    }.get(action)
    if destination_name is None:
        for path in paths:
            path.unlink()
        return
    destination = _subdir(private_notes, destination_name)
    conflicts = [path.name for path in paths if (destination / path.name).exists()]
    if conflicts:
        print(
            f"移動先（{destination_name}）に同名エントリが既に存在します: {', '.join(conflicts)}",
            file=sys.stderr,
        )
        sys.exit(2)
    _update_transition_metadata(paths, action=action, now=now, cooldown_days=cooldown_days)
    for path in paths:
        if action == "return-to-inbox":
            _strip_result_section(path)
        if action in {"adopt", "reject"}:
            _stamp_result(path, outcome=destination_name, now=now, commit=commit_values[path], note=note)
        shutil.move(path, destination / path.name)


def _transition_commit_message(action: str, count: int, note: str | None) -> str:
    """状態遷移の管理repo用コミットメッセージを返す。"""
    item_word = "entry" if count == 1 else "entries"
    note_suffix = f" (理由: {note})" if action == "remove" and note else ""
    return {
        "start-processing": f"chore: start processing {count} {item_word}",
        "return-to-inbox": f"chore: return {count} {item_word} to inbox",
        "hold": f"chore: hold {count} {item_word}",
        "unhold": f"chore: unhold {count} {item_word}",
        "adopt": f"chore: process {count} {item_word} (adopted)",
        "reject": f"chore: process {count} {item_word} (rejected)",
        "remove": f"chore: remove {count} {item_word}{note_suffix}",
    }[action]


def transition_entries(
    private_notes: pathlib.Path,
    *,
    action: str,
    filenames: list[str],
    now: datetime.datetime,
    target_repo: str | None = None,
    note: str | None = None,
    commit: str | None = None,
    lock_timeout: float = -1,
    force: bool = False,
    state: str | None = None,
    expected_content: str | None = None,
    cooldown_days: int | None = None,
    local_worktree: pathlib.Path | None = None,
    skip_push: bool = False,
) -> list[str]:
    """平引数でエントリの一括状態遷移又は削除を実行する。

    `action="remove"`かつ`force=False`（既定）の場合、processing状態のファイルが
    対象に含まれるとexit 2で拒否する（`atk wi rm`の既定保護。処理中ファイルの
    意図しない削除を防ぐ。解除するには`force=True`を渡す）。
    """
    _validate_transition_options(
        action,
        filenames,
        state=state,
        expected_content=expected_content,
        cooldown_days=cooldown_days,
    )
    inbox_dir = private_notes / WI_STATE_INBOX
    _validate_filenames_only(filenames, inbox_dir)
    with _repo_lock(private_notes, timeout=lock_timeout):
        if not skip_push:
            _push_pending_commits(private_notes)
        _pull(private_notes)
        missing_is_conflict = action == "remove" and expected_content is not None
        paths = _resolve_transition_paths(
            private_notes,
            action,
            filenames,
            state,
            missing_is_conflict=missing_is_conflict,
        )
        _validate_transition_targets(
            paths,
            action=action,
            target_repo=target_repo,
            expected_content=expected_content,
            cooldown_days=cooldown_days,
            force=force,
        )
        commit_values = (
            _commit_values_by_path(paths, commit, local_worktree)
            if action in {"adopt", "reject"}
            else {path: commit for path in paths}
        )
        _apply_transition(
            private_notes,
            paths,
            action=action,
            now=now,
            note=note,
            commit_values=commit_values,
            cooldown_days=cooldown_days,
        )
        for state_name in WI_STATES:
            _subdir(private_notes, state_name)
        _commit_and_push(
            private_notes,
            _transition_commit_message(action, len(paths), note),
            list(WI_STATES),
            skip_push=skip_push,
        )
    return [path.name for path in paths]


def _cmd_adopt(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """adoptサブコマンド: 採用としてinboxまたはprocessingからadopted/へ移動しcommit・push。

    移動前に対象ファイル末尾へ`## 処理結果`節を追記する（`--note`・`--commit`が指定された場合のみ該当項目を含む）。
    inbox・processingいずれの起点も許容し、両方に同名ファイルがある場合はprocessingを優先する。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "adopt")
    local_worktree = _candidate_local_worktree(args.target_repo) if args.commit is not None else None
    filenames = transition_entries(
        private_notes,
        action="adopt",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        note=args.note,
        commit=args.commit,
        local_worktree=local_worktree,
        skip_push=args.skip_push,
    )
    print(f"{len(filenames)}件採用処理: {', '.join(filenames)}")


def _cmd_reject(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """rejectサブコマンド: 不採用としてinboxまたはprocessingからrejected/へ移動しcommit・push。

    移動前に対象ファイル末尾へ`## 処理結果`節を追記する（`--note`・`--commit`が指定された場合のみ該当項目を含む）。
    inbox・processingいずれの起点も許容し、両方に同名ファイルがある場合はprocessingを優先する。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "reject")
    local_worktree = _candidate_local_worktree(args.target_repo) if args.commit is not None else None
    filenames = transition_entries(
        private_notes,
        action="reject",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        note=args.note,
        commit=args.commit,
        state=WI_STATE_INBOX if args.if_inbox else None,
        local_worktree=local_worktree,
        skip_push=args.skip_push,
    )
    print(f"{len(filenames)}件不採用処理: {', '.join(filenames)}")


def _cmd_start_processing(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """start-processingサブコマンド: inboxからprocessing/へ移動しcommit・push。

    後続の`adopt`・`reject`が処理を継続することを前提とし、`## 処理結果`節の追記はしない
    （最終処理結果の記録は`adopt`・`reject`側で行う）。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "start-processing")
    filenames = transition_entries(
        private_notes,
        action="start-processing",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
    )
    print(f"{len(filenames)}件処理開始: {', '.join(filenames)}")


def _cmd_hold(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """holdサブコマンド: 処理可能な項目をholdへ移動する。"""
    args.filenames = _dedup_positional_filenames(args.filenames, "hold")
    filenames = transition_entries(
        private_notes,
        action="hold",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
    )
    print(f"{len(filenames)}件保留: {', '.join(filenames)}")


def _cmd_unhold(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """unholdサブコマンド: hold項目をinboxへ戻す。"""
    args.filenames = _dedup_positional_filenames(args.filenames, "unhold")
    filenames = transition_entries(
        private_notes,
        action="unhold",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
    )
    print(f"{len(filenames)}件保留解除: {', '.join(filenames)}")


def _cmd_return_to_inbox(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """return-to-inboxサブコマンド: processingからinbox/へ戻しcommit・push。

    保留判定でprocessing化済みの対象を未処理状態へ戻す用途で使う
    （`agent-toolkit:process-wi`のpicker起動契約「同一セッション中にUWIの回答を受領した場合」参照）。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "return-to-inbox")
    filenames = transition_entries(
        private_notes,
        action="return-to-inbox",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        state=args.state,
        cooldown_days=args.cooldown_days,
    )
    print(f"{len(filenames)}件inboxへ差し戻し: {', '.join(filenames)}")


def _cmd_rm(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """rmサブコマンド: 個別指定または対象リポジトリ単位でactive項目を削除する。"""
    if args.all:
        filenames = _remove_all.remove_all_entries(
            private_notes,
            target_repo=args.target_repo,
            assume_yes=args.yes,
            force=args.force,
            note=args.note,
            skip_pull=args.skip_pull,
        )
        if filenames:
            print(f"{len(filenames)}件削除: {', '.join(filenames)}")
        return

    args.filenames = _dedup_positional_filenames(args.filenames, "rm")
    filenames = transition_entries(
        private_notes,
        action="remove",
        filenames=args.filenames,
        now=datetime.datetime.now(),
        target_repo=args.target_repo,
        force=args.force,
        note=args.note,
    )
    print(f"{len(filenames)}件削除: {', '.join(filenames)}")
