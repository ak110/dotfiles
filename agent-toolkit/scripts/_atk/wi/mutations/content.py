# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
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
    WI_EDITABLE_STATES,
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
        _resolve_editable_targets,
        _resolve_processable_targets,
        _resolve_removable_targets,
        commit_entries,
    )
    from _atk.wi.mutations.transitions import (
        _apply_transition,
        _cmd_adopt,
        _cmd_hold,
        _cmd_reject,
        _cmd_return_to_inbox,
        _cmd_rm,
        _cmd_start_processing,
        _cmd_unhold,
        _resolve_transition_paths,
        _strip_result_section,
        _transition_commit_message,
        _update_transition_metadata,
        _validate_transition_options,
        _validate_transition_targets,
        transition_entries,
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


def _reject_agent_user_comment_change(original: str, updated: str) -> bool:
    """$EDITOR経路からのユーザーコメント変更を拒否した場合に真を返す。"""
    if not is_agent_environment():
        return False
    try:
        changed = _user_comment.extract_user_comment(original) != _user_comment.extract_user_comment(updated)
    except _user_comment.UserCommentError:
        changed = True
    if not changed:
        return False
    print(_user_comment.AGENT_USER_COMMENT_EDIT_ERROR, file=sys.stderr)
    return True


def _reject_agent_user_comment_message(message: str) -> bool:
    """エージェント環境のMESSAGEが予約見出しを含む場合に真を返す。"""
    if not is_agent_environment() or not _user_comment.has_reserved_heading(message):
        return False
    print(_user_comment.AGENT_USER_COMMENT_EDIT_ERROR, file=sys.stderr)
    return True


def _preserve_agent_user_comment(original: str, updated: str) -> str:
    """エージェント環境では保存済みユーザーコメント節を編集結果へ連結する。"""
    if not is_agent_environment():
        return updated
    try:
        _before, saved_user_comment = _user_comment.split_before_user_comment(original)
    except _user_comment.UserCommentError:
        print(_user_comment.AGENT_USER_COMMENT_EDIT_ERROR, file=sys.stderr)
        sys.exit(1)
    if not saved_user_comment:
        return updated
    return updated.rstrip("\r\n") + "\n\n" + saved_user_comment


def edit_entry_content(
    private_notes: pathlib.Path,
    *,
    state: str,
    filename: str,
    content: str,
    target_repo: str | None = None,
    lock_timeout: float = -1,
    expected_content: str | None = None,
    finalized_content: dict[str, str] | None = None,
) -> bool:
    """平引数でAWI本文を更新する。

    `finalized_content`を渡した場合は、保存本文との一致判定に用いる確定本文を格納する。
    """
    if state not in WI_EDITABLE_STATES:
        raise WebInputError("編集可能状態はinbox、processing又はholdです")

    directory = private_notes / state
    return _edit_entry(
        private_notes,
        directory=directory,
        filename=filename,
        content=content,
        target_repo=target_repo,
        lock_timeout=lock_timeout,
        expected_content=expected_content,
        commit_message="chore: edit wi item",
        content_transformer=_invalidate_repo_bound_metadata,
        finalized_content=finalized_content,
    )


def append_entry_content(
    private_notes: pathlib.Path,
    *,
    state: str,
    filename: str,
    content: bytes,
    target_repo: str | None = None,
    lock_timeout: float = -1,
    expected_content: bytes | None = None,
    finalized_content: dict[str, str] | None = None,
) -> bool:
    """AWI本文をraw bytesのまま追記する。UWIは拒否する。

    `finalized_content`を渡した場合は、保存本文との一致判定に用いる確定本文を格納する。
    """
    if state not in WI_EDITABLE_STATES:
        raise WebInputError("追記可能状態はinbox、processing又はholdです")

    directory = private_notes / state
    path = directory / filename

    def validate(previous: str, updated: str) -> None:
        del updated
        if _require_type(path, previous) == WI_TYPE_UWI:
            raise WebInputError("UWIには追記できません")

    return _append_entry(
        private_notes,
        directory=directory,
        filename=filename,
        content=content,
        target_repo=target_repo,
        lock_timeout=lock_timeout,
        expected_content=expected_content,
        commit_message="chore: append wi item",
        content_validator=validate,
        finalized_content=finalized_content,
    )


def _build_noninteractive_edit_content(path: pathlib.Path, original: str, message: str) -> str:
    """MESSAGEを既存メタデータへ重ね、種別別の保存内容を返す。"""
    parsed = _frontmatter.parse_frontmatter(original)
    if parsed is None:
        raise WebInputError(f"frontmatterが破損しているため編集できません: {path.name}")
    stored_data, stored_body = parsed
    entry_type = _require_type(path, original)
    assert entry_type is not None
    message_frontmatter, message_body = _add.parse_entry_message(message, entry_type=entry_type)
    normalized_message_body = message_body.strip("\n")

    requested_type = message_frontmatter.get("type")
    if requested_type is not None and requested_type != entry_type:
        print(
            f"typeを変更することはできません（現在値: {entry_type}）: {path.name}",
            file=sys.stderr,
        )
        sys.exit(2)
    if entry_type != WI_TYPE_UWI:
        uwi_only_keys = sorted({"scope", "question_type", "choices"} & message_frontmatter.keys())
        if uwi_only_keys:
            raise WebInputError(f"AWIでは指定できないメタデータです: {', '.join(uwi_only_keys)}")

    updates = dict(message_frontmatter)
    if "target_repo" in updates:
        raw_target_repo = updates["target_repo"]
        if not isinstance(raw_target_repo, str):
            raise WebInputError("target_repoは文字列で指定してください")
        updates["target_repo"] = _resolve_repo_id(raw_target_repo)
    if "depends_on" in updates:
        raise WebInputError("depends_onは予約キーのため atk wi edit では指定できません")
    if "target_commit" in updates:
        raise WebInputError("target_commitは予約キーのため atk wi edit では指定できません")
    if "cooldown_until" in updates:
        raise WebInputError("cooldown_untilは予約キーのため atk wi edit では指定できません")
    if "repair_target" in updates:
        raise WebInputError("repair_targetは予約キーのため atk wi edit では指定できません")
    if "repair_kind" in updates:
        raise WebInputError("repair_kindは予約キーのため atk wi edit では指定できません")
    if "plan_file" in updates:
        raise WebInputError("plan_fileは予約キーのため atk wi edit では指定できません")
    updated_data = {**stored_data, **updates}
    target_repo_changed = "target_repo" in updates and stored_data.get("target_repo") != updates["target_repo"]
    if target_repo_changed:
        updated_data.pop("target_commit", None)

    if entry_type != WI_TYPE_UWI:
        return _frontmatter.serialize_frontmatter(updated_data, "\n" + normalized_message_body.rstrip() + "\n")

    if not normalized_message_body.strip():
        raise WebInputError("UWIの質問本文は空にできません")
    question_type = updated_data.get("question_type")
    if question_type not in {"choice", "yes-no", "free-form"}:
        raise WebInputError("question_typeが不正です")
    if question_type == "choice" and not updated_data.get("choices"):
        raise WebInputError("choice形式にはchoicesが必要です")

    marker_index = stored_body.rfind(_uwi.ANSWER_MARKER)
    if marker_index < 0:
        raise WebInputError("回答欄マーカーがありません")
    answer_heading_index = stored_body.rfind(_uwi.ANSWER_HEADING, 0, marker_index)
    question_heading_index = stored_body.rfind(_uwi.QUESTION_HEADING, 0, answer_heading_index)
    if answer_heading_index < 0 or question_heading_index < 0:
        raise WebInputError("UWIの質問見出しまたは回答見出しがありません")
    question_heading_end = question_heading_index + len(_uwi.QUESTION_HEADING)
    updated_body = (
        stored_body[:question_heading_end]
        + "\n\n"
        + normalized_message_body.rstrip()
        + "\n\n"
        + stored_body[answer_heading_index:]
    )
    return _frontmatter.serialize_frontmatter(updated_data, updated_body)


def _resolve_edit_message(args: argparse.Namespace) -> str | None:
    """MESSAGE又は本文ファイルを単一の編集本文へ解決する。"""
    if args.body_file is None:
        return args.message
    if args.message is not None:
        args.subparser.error("--body-fileとMESSAGEは併用できません。")
    try:
        return _add.read_body_files([args.body_file])[0]
    except WebInputError as error:
        print(f"編集を拒否しました: {error}", file=sys.stderr)
        sys.exit(1)


def _reject_positional_edit_file_path(args: argparse.Namespace, message: str) -> None:
    """位置引数MESSAGEだけを、編集種別に対応する案内付きで検査する。"""
    if args.body_file is not None:
        return
    if args.plan_file is not None:
        operation = "計画型編集"
        hint = "ファイル内容を本文にする場合はMESSAGEを省略し、エディターで貼り付けてください。"
    elif args.append:
        operation = "追記"
        hint = "MESSAGEには追記する本文を指定してください。"
    else:
        operation = "編集"
        hint = "ファイル内容を本文にする場合はMESSAGEを省略し、エディターで貼り付けてください。"
    try:
        _add.reject_message_file_path(message, file_input_hint=hint)
    except WebInputError as error:
        print(f"{operation}を拒否しました: {error}", file=sys.stderr)
        sys.exit(1)


def _cmd_edit(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """editサブコマンド: MESSAGE又は$EDITORで対象を編集しcommit・pushする。

    無引数時は_pull実行後にinbox配下でファイル名順の最大値（最終追加分）を選択する。
    """
    message = _resolve_edit_message(args)
    if args.depends_on and args.plan_file is None:
        args.subparser.error("--depends-onは--plan-fileとともに指定してください。")
    if args.plan_file is not None:
        if args.filename is None or message is None:
            args.subparser.error("--plan-fileではFILENAMEとMESSAGEを指定してください。")
        if args.append:
            args.subparser.error("--plan-fileと--appendは併用できません。")
        assert args.filename is not None
        assert message is not None
    elif args.append:
        if args.filename is None or message is None:
            args.subparser.error("--appendではFILENAMEとMESSAGEを指定してください。")
        assert message is not None
    elif message is not None and args.filename is None:
        args.subparser.error("MESSAGEを指定する場合はFILENAMEも指定してください。")
    if message is not None:
        _reject_positional_edit_file_path(args, message)
    if args.plan_file is not None:
        assert args.filename is not None
        assert message is not None
        try:
            target_repo, local_worktree = _add.resolve_add_target(args.target_repo)
            if local_worktree is None:
                local_worktree = _candidate_local_worktree(args.target_repo)
            if local_worktree is None:
                raise WebInputError("計画型編集には対象リポジトリのローカルworktreeが必要です")
            if _local_worktree_repo_id(local_worktree) != target_repo:
                raise WebInputError("計画型編集の対象repoとローカルworktreeが一致しません")
            stored_plan_file = _normalize_stored_plan_file(args.plan_file, private_notes=private_notes)
            plan_path = _plan_file.require_saved_plan_file(stored_plan_file, private_notes=private_notes)
            target_commit = _resolve_plan_base_commit(plan_path, local_worktree)
        except (OSError, ValueError, WebInputError) as error:
            print(f"計画型編集を拒否しました: {error}", file=sys.stderr)
            sys.exit(1)

        inbox_dir = private_notes / WI_STATE_INBOX
        _validate_filenames_only([args.filename], inbox_dir)
        with _repo_lock(private_notes):
            _pull(private_notes)
            snapshot_path = _validate_filename(args.filename, private_notes / WI_STATE_HOLD)
            if not snapshot_path.is_file():
                print(f"holdに存在しません: {snapshot_path.name}", file=sys.stderr)
                sys.exit(2)
            snapshot = snapshot_path.read_text(encoding="utf-8")
            _verify_target_repo_content(snapshot_path, snapshot, target_repo)
        if _reject_agent_user_comment_message(message):
            sys.exit(1)
        message = _preserve_agent_user_comment(snapshot, message)
        try:
            details = edit_entry_to_plan(
                private_notes,
                filename=snapshot_path.name,
                content=message,
                plan_file=args.plan_file,
                target_commit=target_commit,
                depends_on=tuple(args.depends_on or ()),
                target_repo=target_repo,
                expected_content=snapshot,
            )
        except RuntimeError:
            print(
                f"編集中に他プロセスが対象を変更しました: {snapshot_path.name}。"
                "指定したMESSAGEは反映されていません。同じFILENAMEとMESSAGEで再実行してください。",
                file=sys.stderr,
            )
            sys.exit(1)
        except WebInputError as error:
            print(f"計画型編集を拒否しました: {error}", file=sys.stderr)
            sys.exit(1)
        print(f"計画型編集反映: {snapshot_path.name}")
        _add._print_entry_details(details)  # pylint: disable=protected-access
        return
    if args.append:
        assert message is not None
        _cmd_append(args, private_notes, message)
        return
    if message is not None and _reject_agent_user_comment_message(message):
        sys.exit(1)
    editor = None
    if message is None:
        editor = os.environ.get("EDITOR")
        if not editor:
            print("$EDITORが未設定のため編集できません。", file=sys.stderr)
            sys.exit(1)
    inbox_dir = private_notes / WI_STATE_INBOX
    _subdir(private_notes, WI_STATE_PROCESSING)
    with _repo_lock(private_notes):
        if args.filename is None:
            _pull(private_notes)
            candidates = sorted(
                (p for p in inbox_dir.iterdir() if p.suffix == ".md" and p.is_file()),
                key=lambda p: p.name,
            )
            if not candidates:
                print("inboxが空のため編集対象がありません。", file=sys.stderr)
                sys.exit(2)
            path = candidates[-1]
        else:
            _validate_filenames_only([args.filename], inbox_dir)
            _pull(private_notes)
            paths = _resolve_editable_targets([args.filename], private_notes)
            path = paths[0]
        if path.parent.name == WI_STATE_PROCESSING and is_agent_environment():
            print(
                f"processingの項目はエージェント環境から編集できません: {path.name}。"
                "処理中の要求を書き換えると、当該要求が当該セッションで処理されるかが変わります。"
                "書き換えたい内容はatk wi addで新しい項目として投入し、この項目へは"
                "atk wi edit --appendで追記してください。",
                file=sys.stderr,
            )
            sys.exit(2)
        snapshot = path.read_bytes()
        normalized_target_repo = _resolve_repo_id(args.target_repo) if args.target_repo is not None else None
        _verify_target_repo_content(path, _frontmatter.decode_entry_text(snapshot), normalized_target_repo)
    original = _frontmatter.decode_entry_text(snapshot)
    tmp_path: pathlib.Path | None = None
    if message is None:
        assert editor is not None
        tmp_path = _copy_to_tempfile(snapshot)
        subprocess.run([editor, str(tmp_path)], check=True)
        edited = tmp_path.read_text(encoding="utf-8")
    else:
        try:
            edited = _build_noninteractive_edit_content(path, original, message)
        except WebInputError as error:
            print(f"編集を拒否しました: {error}", file=sys.stderr)
            sys.exit(1)
        edited = _preserve_agent_user_comment(original, edited)
    if edited == original:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        print("差分なし。")
        return
    if message is None and _reject_agent_user_comment_change(original, edited):
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        sys.exit(1)
    finalized_content: dict[str, str] = {}
    try:
        edit_entry_content(
            private_notes,
            state=path.parent.name,
            filename=path.name,
            content=edited,
            target_repo=args.target_repo,
            expected_content=original,
            finalized_content=finalized_content,
        )
    except RuntimeError:
        if tmp_path is None:
            print(
                f"編集中に他プロセスが対象を変更しました: {path.name}。"
                "指定したMESSAGEは反映されていません。同じFILENAMEとMESSAGEで再実行してください。",
                file=sys.stderr,
            )
        else:
            print(
                f"編集中に他プロセスが対象を変更しました: {path.name}。"
                f"編集内容は{tmp_path}に残しています。再度atk wi editを実行してください。",
                file=sys.stderr,
            )
        sys.exit(1)
    if tmp_path is not None:
        tmp_path.unlink(missing_ok=True)
    print(f"編集反映: {path.name}")
    _add._print_entry_details(  # pylint: disable=protected-access
        _add._read_saved_entry_details(  # pylint: disable=protected-access
            path,
            expected_body=finalized_content["content"],
        )
    )


def _cmd_append(args: argparse.Namespace, private_notes: pathlib.Path, message: str) -> None:
    """Edit --appendサブコマンド: 既存raw bytesを保ってMESSAGEを末尾へ追記する。"""
    assert args.filename is not None
    if _reject_agent_user_comment_message(message):
        sys.exit(1)

    inbox_dir = private_notes / WI_STATE_INBOX
    _subdir(private_notes, WI_STATE_PROCESSING)
    with _repo_lock(private_notes):
        _validate_filenames_only([args.filename], inbox_dir)
        _pull(private_notes)
        path = _resolve_editable_targets([args.filename], private_notes)[0]
        snapshot = path.read_bytes()
        normalized_target_repo = _resolve_repo_id(args.target_repo) if args.target_repo is not None else None
        _verify_target_repo_content(path, _frontmatter.decode_entry_text(snapshot), normalized_target_repo)

    original = snapshot.decode("utf-8")
    if is_agent_environment():
        try:
            before_user_comment, saved_user_comment = _user_comment.split_before_user_comment(original)
        except _user_comment.UserCommentError:
            print(_user_comment.AGENT_USER_COMMENT_EDIT_ERROR, file=sys.stderr)
            sys.exit(1)
        content = (
            before_user_comment.encode("utf-8")
            + b"\n\n"
            + message.encode("utf-8")
            + (b"\n\n" + saved_user_comment.encode("utf-8") if saved_user_comment else b"")
        )
    else:
        content = snapshot + b"\n\n" + message.encode("utf-8")
    finalized_content: dict[str, str] = {}
    try:
        append_entry_content(
            private_notes,
            state=path.parent.name,
            filename=path.name,
            content=content,
            target_repo=args.target_repo,
            expected_content=snapshot,
            finalized_content=finalized_content,
        )
    except RuntimeError:
        print(
            f"追記中に他プロセスが対象を変更しました: {path.name}。"
            "指定したMESSAGEは反映されていません。同じFILENAMEとMESSAGEで再実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"追記反映: {path.name}")
    _add._print_entry_details(  # pylint: disable=protected-access
        _add._read_saved_entry_details(  # pylint: disable=protected-access
            path,
            expected_body=finalized_content["content"],
        )
    )
