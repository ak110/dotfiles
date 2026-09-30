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
from typing import TYPE_CHECKING

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import add as _add
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import bulk as _bulk
from agent_toolkit._atk.wi import user_comment as _user_comment
from agent_toolkit._atk.wi import uwi as _uwi
from agent_toolkit._atk.wi.common import (
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
from agent_toolkit._atk.wi.repo import (
    _normalize_remote_url,
    _resolve_repo_id,
    _verify_target_repo_content,
)
from agent_toolkit._atk.wi.repo import append_entry as _append_entry
from agent_toolkit._atk.wi.repo import edit_entry as _edit_entry
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import structure as _plan_format

if TYPE_CHECKING:
    from agent_toolkit._atk.wi.mutations.content import (
        _build_noninteractive_edit_content,
        _cmd_append,
        _cmd_edit,
        _preserve_agent_user_comment,
        _reject_agent_user_comment_change,
        _reject_agent_user_comment_message,
        append_entry_content,
        edit_entry_content,
    )
    from agent_toolkit._atk.wi.mutations.plan_conversion import (
        _assert_conversion_paths_clean,
        _assert_conversion_targets_tracked,
        _cmd_convert_to_plan,
        _convert_held_entries,
        _normalize_stored_plan_file,
        _plan_awi_paths,
        _PlanAwiValidationError,
        _read_plan_input_filenames,
        _resolve_plan_base_commit,
        _restore_conversion_paths,
        _store_plan_file,
        _StoredPlanFile,
        _validated_plan_awi_paths,
        convert_entries_to_plan,
        convert_entry_to_plan,
        edit_entry_to_plan,
    )
    from agent_toolkit._atk.wi.mutations.targets import (
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
        _resolve_active_targets,
        commit_entries,
    )
    from agent_toolkit._atk.wi.mutations.transitions import (
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


_BROKEN_ENTRY_NEXT_ACTION = "`atk wi show {name}`で保存内容を確認し、ユーザーへ報告する"
_LEGACY_DEPENDENCY_NEXT_ACTION = (
    "`atk wi set-dependencies {name} --depends-on <依存先>`で依存を現行の形式へ指定し直してから再実行する"
)


def _entry_dependencies(path: pathlib.Path, data: dict[str, object]) -> tuple[str, ...]:
    """エントリの依存先を文字列列として検証して返す。"""
    raw_dependencies = data.get("depends_on", [])
    if not isinstance(raw_dependencies, list) or not all(isinstance(value, str) for value in raw_dependencies):
        raise WebInputError(
            f"depends_onが不正です: {path.name}",
            next_action=f"`atk wi set-dependencies {path.name} --depends-on <依存先>`で依存を指定し直す",
        )
    return tuple(raw_dependencies)


def _entry_dependencies_for_conversion(path: pathlib.Path, data: dict[str, object]) -> tuple[str, ...]:
    """変換時にトップレベルまたは意味を保てる旧形式の依存先を返す。"""
    if "depends_on" in data:
        return _entry_dependencies(path, data)
    schedule = data.get("queue_schedule")
    if not isinstance(schedule, dict):
        return ()
    dependency = schedule.get("dependency")
    if not isinstance(dependency, dict) or dependency.get("kind") in (None, "none"):
        return ()
    if dependency.get("kind") != "entries":
        raise WebInputError(
            f"旧形式の依存を計画実装型へ移行できません: {path.name}",
            next_action=_LEGACY_DEPENDENCY_NEXT_ACTION.format(name=path.name),
        )
    filenames = dependency.get("filenames")
    if not isinstance(filenames, list) or not filenames or any(not isinstance(value, str) or not value for value in filenames):
        raise WebInputError(
            f"旧形式の依存が不正なため変換できません: {path.name}",
            next_action=_LEGACY_DEPENDENCY_NEXT_ACTION.format(name=path.name),
        )
    return tuple(dict.fromkeys(filenames))


def set_entry_dependencies(
    private_notes: pathlib.Path,
    *,
    filename: str,
    depends_on: tuple[str, ...],
    target_repo: str | None = None,
    lock_timeout: float = -1,
) -> dict[str, object | None]:
    """既存AWIの明示依存だけを更新し、保存済みメタデータを返す。

    対象は未終端の`inbox`、`processing`および`hold`とする。`hold`を含めるのは、投入済み項目の修正手順が
    投入済み項目を修正する際は、`hold`中に本文と依存を確定する。そのため、この状態でも依存を更新できる必要がある。
    依存の更新は保存状態を変えないため、`hold`の項目は更新後も`hold`のまま残る。
    """
    inbox_dir = private_notes / WI_STATE_INBOX
    processing_dir = _subdir(private_notes, WI_STATE_PROCESSING)
    hold_dir = private_notes / WI_STATE_HOLD
    _validate_filenames_only([filename, *depends_on], inbox_dir)
    normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None

    with _repo_lock(private_notes, timeout=lock_timeout):
        _push_pending_commits(private_notes)
        _pull(private_notes)
        path = _resolve_active_targets([filename], inbox_dir, processing_dir)[0]
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            raise WebInputError(
                f"frontmatterが破損しているため依存を更新できません: {path.name}",
                next_action=_BROKEN_ENTRY_NEXT_ACTION.format(name=path.name),
            )
        data, body = parsed
        if _require_type(path, text) != WI_TYPE_AWI:
            raise WebInputError(
                f"AWIだけ依存を更新できます: {path.name}",
                next_action="依存を更新するAWIのファイル名を指定し直す",
            )
        raw_entry_repo = data.get("target_repo")
        if not isinstance(raw_entry_repo, str):
            raise WebInputError(
                f"target_repoが不正です: {path.name}",
                next_action=_BROKEN_ENTRY_NEXT_ACTION.format(name=path.name),
            )
        entry_repo = _resolve_repo_id(raw_entry_repo)
        if normalized_target_repo is not None and entry_repo != normalized_target_repo:
            raise WebInputError(
                f"target_repoが一致しません: {path.name}は{entry_repo}、指定値は{normalized_target_repo}",
                next_action=(
                    f"実際の値を--target-repoへ指定し直すか、`atk wi show {path.name}`で別リポジトリの項目でないか確認する"
                ),
            )

        canonical_dependencies = tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in depends_on))
        if path.name in canonical_dependencies:
            raise WebInputError(
                f"自分自身を依存先へ指定できません: {path.name}",
                next_action="--depends-onから自分自身を外して再実行する",
            )
        dependency_graph = _active_dependency_graph(inbox_dir, processing_dir, hold_dir)
        dependency_graph[path.name] = set(canonical_dependencies)
        cycle = _dependency_cycle(dependency_graph, path.name, canonical_dependencies)
        if cycle is not None:
            raise WebInputError(
                f"循環する依存を指定できません: {path.name}（経路: {' → '.join(cycle)}）",
                next_action=(
                    f"`atk wi show {cycle[1]}`で依存先を確認し、循環の原因となる依存先を--depends-onの指定から外して再実行する"
                ),
            )
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
            raise WebInputError(
                f"active項目のfrontmatterが破損しているため依存を更新できません: {name}",
                next_action=_BROKEN_ENTRY_NEXT_ACTION.format(name=name),
            )
        data, _body = parsed
        if _require_type(entry_path, entry_text) != WI_TYPE_AWI:
            continue
        raw_dependencies = data.get("depends_on", [])
        if not isinstance(raw_dependencies, list) or not all(isinstance(value, str) for value in raw_dependencies):
            raise WebInputError(
                f"active項目のdepends_onが不正なため依存を更新できません: {name}",
                next_action=f"`atk wi set-dependencies {name} --depends-on <依存先>`で依存を指定し直してから再実行する",
            )
        graph[name] = {_validate_filename(value, inbox_dir).name for value in raw_dependencies}
    return graph


def _dependency_reaches(graph: dict[str, set[str]], start: str, target: str) -> bool:
    """startからtargetへ到達できる場合に真を返す。"""
    return _dependency_path(graph, start, target) is not None


def _dependency_path(graph: dict[str, set[str]], start: str, target: str) -> tuple[str, ...] | None:
    """startからtargetへ至る依存の経路を返す。到達できない場合は`None`を返す。"""
    pending: list[tuple[str, ...]] = [(start,)]
    visited: set[str] = set()
    while pending:
        route = pending.pop()
        current = route[-1]
        if current == target:
            return route
        if current in visited:
            continue
        visited.add(current)
        pending.extend((*route, following) for following in sorted(graph.get(current, ()), reverse=True))
    return None


def _dependency_cycle(
    graph: dict[str, set[str]],
    name: str,
    dependencies: typing.Iterable[str],
) -> tuple[str, ...] | None:
    """nameから依存先を経てnameへ戻る循環の経路を返す。循環が無い場合は`None`を返す。

    受信側が循環の原因となる依存先を特定できるよう、拒否の理由へ経路を載せるために使う。
    """
    for dependency in dependencies:
        route = _dependency_path(graph, dependency, name)
        if route is not None:
            return (name, *route)
    return None


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
        _outcome.report_failure(f"依存更新を拒否した: {error}", next_action=error.next_action)
        sys.exit(1)
    _outcome.report_success(f"依存を更新した: {args.filename}")
    _add._print_entry_details(details)  # pylint: disable=protected-access
