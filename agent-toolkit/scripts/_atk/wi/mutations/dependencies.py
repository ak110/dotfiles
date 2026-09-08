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


def _entry_dependencies(path: pathlib.Path, data: dict[str, object]) -> tuple[str, ...]:
    """エントリの依存先を文字列列として検証して返す。"""
    raw_dependencies = data.get("depends_on", [])
    if not isinstance(raw_dependencies, list) or not all(isinstance(value, str) for value in raw_dependencies):
        raise WebInputError(f"depends_onが不正です: {path.name}")
    return tuple(raw_dependencies)


def _entry_dependencies_for_conversion(path: pathlib.Path, data: dict[str, object]) -> tuple[str, ...]:
    """変換時にトップレベル又は意味を保てる旧形式の依存先を返す。"""
    if "depends_on" in data:
        return _entry_dependencies(path, data)
    schedule = data.get("queue_schedule")
    if not isinstance(schedule, dict):
        return ()
    dependency = schedule.get("dependency")
    if not isinstance(dependency, dict) or dependency.get("kind") in (None, "none"):
        return ()
    if dependency.get("kind") != "entries":
        raise WebInputError(f"旧形式の依存を計画実装型へ移行できません: {path.name}")
    filenames = dependency.get("filenames")
    if not isinstance(filenames, list) or not filenames or any(not isinstance(value, str) or not value for value in filenames):
        raise WebInputError(f"旧形式の依存が不正なため変換できません: {path.name}")
    return tuple(dict.fromkeys(filenames))


def set_entry_dependencies(
    private_notes: pathlib.Path,
    *,
    filename: str,
    depends_on: tuple[str, ...],
    target_repo: str | None = None,
    lock_timeout: float = -1,
) -> dict[str, object | None]:
    """既存AWIの明示依存だけを更新し、保存済みメタデータを返す。"""
    inbox_dir = private_notes / WI_STATE_INBOX
    processing_dir = _subdir(private_notes, WI_STATE_PROCESSING)
    _validate_filenames_only([filename, *depends_on], inbox_dir)
    normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None

    with _repo_lock(private_notes, timeout=lock_timeout):
        _push_pending_commits(private_notes)
        _pull(private_notes)
        path = _resolve_processable_targets([filename], inbox_dir, processing_dir)[0]
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            raise WebInputError(f"frontmatterが破損しているため依存を更新できません: {path.name}")
        data, body = parsed
        if _require_type(path, text) != WI_TYPE_AWI:
            raise WebInputError(f"AWIだけ依存を更新できます: {path.name}")
        raw_entry_repo = data.get("target_repo")
        if not isinstance(raw_entry_repo, str):
            raise WebInputError(f"target_repoが不正です: {path.name}")
        entry_repo = _resolve_repo_id(raw_entry_repo)
        if normalized_target_repo is not None and entry_repo != normalized_target_repo:
            raise WebInputError(f"target_repoが一致しません: {path.name}は{entry_repo}、指定値は{normalized_target_repo}")

        canonical_dependencies = tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in depends_on))
        if path.name in canonical_dependencies:
            raise WebInputError(f"自分自身を依存先へ指定できません: {path.name}")
        dependency_graph = _active_dependency_graph(inbox_dir, processing_dir)
        dependency_graph[path.name] = set(canonical_dependencies)
        if any(_dependency_reaches(dependency_graph, dependency, path.name) for dependency in canonical_dependencies):
            raise WebInputError(f"循環する依存を指定できません: {path.name}")
        data.pop("queue_schedule", None)
        if canonical_dependencies:
            data["depends_on"] = list(canonical_dependencies)
        else:
            data.pop("depends_on", None)
        updated_text = _frontmatter.serialize_frontmatter(data, body)
        if updated_text != text:
            _atomic_write_text(path, updated_text)
            relative_path = str(path.relative_to(private_notes))
            _commit_and_push(private_notes, "chore: update awi dependencies", [relative_path])
        return _add._read_saved_entry_details(  # pylint: disable=protected-access
            path,
            expected_body=updated_text,
        )


def _active_dependency_graph(
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    hold_dir: pathlib.Path | None = None,
) -> dict[str, set[str]]:
    """ロック内で取得したactiveなAWIの依存グラフを返す。"""
    entries: dict[str, pathlib.Path] = {}
    directories = (inbox_dir, processing_dir) if hold_dir is None else (inbox_dir, hold_dir, processing_dir)
    for directory in directories:
        if directory.is_dir():
            entries.update({path.name: path for path in directory.glob("*.md") if path.is_file()})
    graph: dict[str, set[str]] = {}
    for name, entry_path in entries.items():
        entry_text = entry_path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(entry_text)
        if parsed is None:
            raise WebInputError(f"active項目のfrontmatterが破損しているため依存を更新できません: {name}")
        data, _body = parsed
        if _require_type(entry_path, entry_text) != WI_TYPE_AWI:
            continue
        raw_dependencies = data.get("depends_on", [])
        if not isinstance(raw_dependencies, list) or not all(isinstance(value, str) for value in raw_dependencies):
            raise WebInputError(f"active項目のdepends_onが不正なため依存を更新できません: {name}")
        graph[name] = {_validate_filename(value, inbox_dir).name for value in raw_dependencies}
    return graph


def _dependency_reaches(graph: dict[str, set[str]], start: str, target: str) -> bool:
    """startからtargetへ到達できる場合に真を返す。"""
    pending = [start]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, ()))
    return False


def _cmd_set_dependencies(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """set-dependenciesサブコマンドを実行する。"""
    target_repo = args.target_repo
    if target_repo is None:
        target_repo, _local_worktree = _add.resolve_add_target(None)
    try:
        details = set_entry_dependencies(
            private_notes,
            filename=args.filename,
            depends_on=tuple(args.depends_on or ()),
            target_repo=target_repo,
        )
    except WebInputError as error:
        print(f"依存更新を拒否しました: {error}", file=sys.stderr)
        sys.exit(1)
    print(f"依存を更新: {args.filename}")
    _add._print_entry_details(details)  # pylint: disable=protected-access
