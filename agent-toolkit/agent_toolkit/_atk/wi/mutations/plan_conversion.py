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
from agent_toolkit._atk.wi import add as _add
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import remove_all as _remove_all
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
    from agent_toolkit._atk.wi.mutations.dependencies import (
        _active_dependency_graph,
        _cmd_set_dependencies,
        _dependency_reaches,
        _entry_dependencies,
        _entry_dependencies_for_conversion,
        set_entry_dependencies,
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
        _resolve_removable_targets,
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


class _PlanAwiValidationError(Exception):
    """計画入力の参照元を付ける前の検証失敗。"""


def _read_plan_input_filenames(plan_path: pathlib.Path) -> tuple[tuple[str, ...], str]:
    """キュー項目名と、参照元を示すユーザー向け表示名を返す。"""
    try:
        text = plan_path.read_text(encoding="utf-8")
    except OSError as error:
        raise WebInputError(f"plan_fileを読み込めません: {plan_path}") from error
    except UnicodeError as error:
        raise WebInputError(f"plan_fileをUTF-8として読み込めません: {plan_path}") from error

    metadata, metadata_errors = _plan_format.parse_plan_metadata(text)
    if metadata_errors:
        raise WebInputError("計画メタ情報が不正です: " + "; ".join(metadata_errors))
    if metadata is not None and _plan_format.PLAN_METADATA_RELATED_WI_FIELD in metadata.values:
        source_description = "計画メタ情報の関連WI"
        related_errors = _plan_format.check_plan_related_wi(metadata)
        if related_errors:
            raise WebInputError("計画メタ情報の関連WIが不正です: " + "; ".join(related_errors))
        filenames = tuple(filename for filename, _summary in metadata.related_wi)
        if metadata.values[_plan_format.PLAN_METADATA_RELATED_WI_FIELD] == "なし":
            filenames = ()
    else:
        source_description = "計画の提示素材"
        materials, errors = _plan_format.parse_plan_materials(text)
        if errors:
            raise WebInputError("旧書式の計画の提示素材が不正です: " + "; ".join(errors))
        if materials is None:
            raise WebInputError("旧書式の計画の提示素材を解析できません")
        filenames = materials.material_paths if materials.is_human_readable else materials.feedback_queue_ids
    return tuple(sorted(filenames)), source_description


def _validated_plan_awi_paths(
    private_notes: pathlib.Path,
    filenames: tuple[str, ...],
) -> tuple[pathlib.Path, ...]:
    """計画入力を検証し、参照元を付ける前の失敗を送出する。"""
    awi_paths: list[pathlib.Path] = []
    for filename in filenames:
        candidates = tuple((state, private_notes / state / filename) for state in WI_STATES)
        existing = tuple((state, path) for state, path in candidates if path.is_file())
        if len(existing) != 1:
            raise _PlanAwiValidationError(f"を一意に特定できません: {filename}")
        state, path = existing[0]
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            raise _PlanAwiValidationError(f"のfrontmatterが破損しています: {filename}")
        entry_type = normalized_wi_type(parsed[0].get("type"))
        if entry_type == WI_TYPE_AWI:
            if state != WI_STATE_HOLD:
                raise _PlanAwiValidationError(f"の変換元awiがholdに存在しません: {filename}")
            if "plan_file" in parsed[0]:
                raise _PlanAwiValidationError(f"が既に計画型です: {filename}")
            awi_paths.append(path)
            continue
        if entry_type == WI_TYPE_UWI:
            if state not in WI_PROCESSABLE_STATES:
                raise _PlanAwiValidationError(f"のUWIがactive状態ではありません: {filename}")
            continue
        raise _PlanAwiValidationError(f"のtypeが不正です: {filename}")
    if not awi_paths:
        raise _PlanAwiValidationError("に変換元awiがありません")
    return tuple(awi_paths)


def _plan_awi_paths(
    private_notes: pathlib.Path,
    filenames: tuple[str, ...],
    source_description: str,
) -> tuple[pathlib.Path, ...]:
    """計画入力を検証し、holdにある変換元awiだけを返す。"""
    try:
        return _validated_plan_awi_paths(private_notes, filenames)
    except _PlanAwiValidationError as error:
        raise WebInputError(f"{source_description}{error}") from error


def _resolve_plan_base_commit(plan_path: pathlib.Path, local_worktree: pathlib.Path) -> str:
    """計画メタ情報の一意なベースコミットを対象作業ツリーで解決する。"""
    try:
        content = plan_path.read_text(encoding="utf-8")
    except OSError as error:
        raise WebInputError(f"plan_fileを読み込めません: {plan_path}") from error
    except UnicodeError as error:
        raise WebInputError(f"plan_fileをUTF-8として読み込めません: {plan_path}") from error
    metadata, errors = _plan_format.parse_plan_metadata(content)
    if errors:
        raise WebInputError("計画メタ情報を一意に解析できません: " + "; ".join(errors))
    if metadata is None:
        raise WebInputError("計画メタ情報にベースコミットがありません")
    candidates = tuple(dict.fromkeys(metadata.base_commit_candidates))
    if len(candidates) != 1:
        raise WebInputError("計画メタ情報のベースコミットを一意に特定できません")
    candidate = candidates[0].lower()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", candidate) is None:
        raise WebInputError("計画メタ情報のベースコミットは完全OIDで指定してください")
    return _resolve_commit(local_worktree, candidate)


_StoredPlanFile = typing.NewType("_StoredPlanFile", str)
"""キュー項目のfrontmatterへ保存するplan_file値。"""


def _normalize_stored_plan_file(plan_file: str, *, private_notes: pathlib.Path) -> _StoredPlanFile:
    """入力のplan_fileを保存用の値へ正規化する。"""
    return _StoredPlanFile(_plan_file.normalize_plan_file(plan_file, private_notes=private_notes))


def _store_plan_file(data: dict[str, object], stored_plan_file: _StoredPlanFile) -> None:
    """キュー項目のfrontmatterへplan_fileを書き込む。

    保存値は可搬接頭辞で始まる値か、読み取り互換で受理するroot外の絶対パスに限る。
    """
    if (
        not stored_plan_file.startswith(_plan_file.PORTABLE_PLAN_PREFIX)
        and not pathlib.PurePath(stored_plan_file).is_absolute()
    ):
        raise WebInputError(f"plan_fileの保存値が可搬値でも絶対パスでもありません: {stored_plan_file}")
    data["plan_file"] = stored_plan_file


def edit_entry_to_plan(
    private_notes: pathlib.Path,
    *,
    filename: str,
    content: str,
    plan_file: str,
    target_commit: str,
    depends_on: tuple[str, ...] = (),
    target_repo: str | None = None,
    lock_timeout: float = -1,
    expected_content: str | None = None,
) -> dict[str, object | None]:
    """holdの最古項目を計画型awiへ編集し、inboxへ原子的に移動する。"""
    try:
        stored_plan_file = _normalize_stored_plan_file(plan_file, private_notes=private_notes)
        plan_path = _plan_file.require_saved_plan_file(stored_plan_file, private_notes=private_notes)
    except ValueError as error:
        raise WebInputError(f"plan_fileを解決できません: {plan_file}（{error}）") from error
    except OSError as error:
        raise WebInputError(f"plan_fileを検証できません: {plan_file}") from error
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", target_commit) is None:
        raise WebInputError("target_commitは40桁または64桁の完全OIDで指定してください")

    inbox_dir = private_notes / WI_STATE_INBOX
    _validate_filenames_only([filename, *depends_on], inbox_dir)
    normalized_filename = _validate_filename(filename, inbox_dir).name
    normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None

    with _repo_lock(private_notes, timeout=lock_timeout):
        _push_pending_commits(private_notes)
        _pull(private_notes)
        material_names, source_description = _read_plan_input_filenames(plan_path)
        normalized_material_names = tuple(dict.fromkeys(_validate_filename(name, inbox_dir).name for name in material_names))
        if not normalized_material_names:
            raise WebInputError(f"{source_description}に変換元awiがありません")
        if normalized_filename not in normalized_material_names:
            raise WebInputError(f"指定項目が{source_description}に含まれません: {normalized_filename}")
        material_paths = _plan_awi_paths(private_notes, normalized_material_names, source_description)
        awi_names = tuple(path.name for path in material_paths)
        if normalized_filename not in awi_names:
            raise WebInputError(f"指定項目が計画の変換元awiに含まれません: {normalized_filename}")
        oldest_material = min(awi_names)
        if normalized_filename != oldest_material:
            raise WebInputError(f"計画型へ変換できるのは変換元awiの昇順最古だけです: {oldest_material}")
        held_path = _validate_filename(normalized_filename, private_notes / WI_STATE_HOLD)
        previous = held_path.read_text(encoding="utf-8")
        if expected_content is not None and previous != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        _verify_target_repo_content(held_path, previous, normalized_target_repo)
        parsed = _frontmatter.parse_frontmatter(previous)
        if parsed is None:
            raise WebInputError(f"frontmatterが破損しているため計画型へ編集できません: {held_path.name}")
        stored_data, _stored_body = parsed
        if _require_type(held_path, previous) != WI_TYPE_AWI:
            raise WebInputError(f"AWIだけを計画型へ編集できます: {held_path.name}")
        if "plan_file" in stored_data:
            raise WebInputError(f"既に計画型のため再変換できません: {held_path.name}")

        material_repositories: set[str] = set()
        dependencies: list[str] = []
        for material_path in material_paths:
            material_text = material_path.read_text(encoding="utf-8")
            material_parsed = _frontmatter.parse_frontmatter(material_text)
            if material_parsed is None:
                raise WebInputError(f"変換元awiのfrontmatterが破損しています: {material_path.name}")
            material_data, _material_body = material_parsed
            material_repo = _entry_target_repo(material_path, material_text)
            material_repositories.add(material_repo)
            dependencies.extend(
                _validate_filename(value, inbox_dir).name for value in _entry_dependencies(material_path, material_data)
            )
        if len(material_repositories) != 1:
            raise WebInputError(f"{source_description}は同一target_repoである必要があります")
        material_repo = next(iter(material_repositories))
        if normalized_target_repo is not None and material_repo != normalized_target_repo:
            raise WebInputError(f"target_repoが一致しません: 期待={normalized_target_repo} 実際={material_repo}")

        message_frontmatter, message_body = _add.parse_entry_message(content, entry_type=WI_TYPE_AWI)
        requested_type = message_frontmatter.get("type")
        if requested_type is not None and requested_type != WI_TYPE_AWI:
            raise WebInputError(f"計画型編集のtypeは{WI_TYPE_AWI}で指定してください: {held_path.name}")
        for key in ("target_commit", "depends_on", "plan_file", "queue_schedule", "cooldown_until"):
            if key in message_frontmatter:
                raise WebInputError(f"{key}は計画型編集が管理する予約キーです")
        updates = dict(message_frontmatter)
        if "target_repo" in updates:
            raw_target_repo = updates["target_repo"]
            if not isinstance(raw_target_repo, str):
                raise WebInputError("target_repoは文字列で指定してください")
            updates["target_repo"] = _resolve_repo_id(raw_target_repo)
            if updates["target_repo"] != material_repo:
                raise WebInputError(f"target_repoが一致しません: 期待={material_repo} 実際={updates['target_repo']}")

        updated_data = {**stored_data, **updates}
        updated_data["source"] = "plan-and-add-awi"
        _store_plan_file(updated_data, stored_plan_file)
        updated_data["target_commit"] = target_commit
        updated_data.pop("queue_schedule", None)
        updated_data.pop("cooldown_until", None)
        all_dependencies = dependencies + list(depends_on)
        canonical_dependencies = tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in all_dependencies))
        excluded_inputs = set(awi_names)
        canonical_dependencies = tuple(
            value for value in canonical_dependencies if value not in excluded_inputs and value != held_path.name
        )
        dependency_graph = _active_dependency_graph(
            inbox_dir,
            _subdir(private_notes, WI_STATE_PROCESSING),
            _subdir(private_notes, WI_STATE_HOLD),
        )
        dependency_graph[held_path.name] = set(canonical_dependencies)
        if any(_dependency_reaches(dependency_graph, dependency, held_path.name) for dependency in canonical_dependencies):
            raise WebInputError(f"循環する依存を指定できません: {held_path.name}")
        if canonical_dependencies:
            updated_data["depends_on"] = list(canonical_dependencies)
        else:
            updated_data.pop("depends_on", None)
        updated_text = _frontmatter.serialize_frontmatter(
            updated_data,
            "\n" + message_body.strip("\n").rstrip() + "\n",
        )

        inbox_path = inbox_dir / held_path.name
        if inbox_path.exists():
            raise WebInputError(f"inboxに同名エントリが既に存在します: {held_path.name}")
        _atomic_write_text(inbox_path, updated_text)
        held_path.unlink()
        _commit_and_push(
            private_notes,
            "chore: convert awi item to plan",
            [str(held_path.relative_to(private_notes)), str(inbox_path.relative_to(private_notes))],
        )
        return _add._read_saved_entry_details(  # pylint: disable=protected-access
            inbox_path,
            expected_body=updated_text,
        )


def _assert_conversion_paths_clean(private_notes: pathlib.Path, paths: list[pathlib.Path]) -> None:
    """計画変換前に変換対象と保存先だけがcleanであることを確認する。"""
    relative_paths = [str(path.relative_to(private_notes)) for path in paths]
    if _atk_git_sync.is_worktree_dirty(private_notes, paths=relative_paths):
        raise WebInputError("計画変換前に変換対象と保存先の作業ツリー及びindexをcleanにしてください")


def _assert_conversion_targets_tracked(
    private_notes: pathlib.Path,
    paths: list[pathlib.Path],
) -> None:
    """計画変換対象が開始時HEADに登録済みであることを確認する。"""
    relative_paths = [str(path.relative_to(private_notes)) for path in paths]
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", *relative_paths],
        cwd=private_notes,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WebInputError("計画変換対象が管理repoの開始時HEADに登録されていません")


def _restore_conversion_paths(
    private_notes: pathlib.Path,
    start_head: str,
    relative_paths: tuple[str, ...],
    remove_relative_paths: tuple[str, ...] = (),
) -> None:
    """commit前失敗時に変換対象だけを開始時のHEADへ戻す。"""
    try:
        if _git_head(private_notes) != start_head:
            return
        reset_paths = tuple(dict.fromkeys((*relative_paths, *remove_relative_paths)))
        subprocess.run(
            ["git", "reset", "--mixed", start_head, "--", *reset_paths],
            cwd=private_notes,
            check=True,
        )
        tracked = subprocess.run(
            ["git", "ls-files", "--", *relative_paths],
            cwd=private_notes,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        if tracked:
            subprocess.run(
                ["git", "restore", f"--source={start_head}", "--staged", "--worktree", "--", *tracked],
                cwd=private_notes,
                check=True,
            )
        for relative_path in remove_relative_paths:
            (private_notes / relative_path).unlink(missing_ok=True)
        for relative_path in (*relative_paths, *remove_relative_paths):
            status = subprocess.run(
                ["git", "status", "--porcelain", "--", relative_path],
                cwd=private_notes,
                capture_output=True,
                text=True,
                check=True,
            )
            if status.stdout.strip():
                raise RuntimeError(f"計画変換対象の復元後に差分が残っています: {relative_path}")
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"計画変換対象の復元に失敗しました。手動確認が必要です: {error}", file=sys.stderr)


def _convert_held_entries(
    private_notes: pathlib.Path,
    *,
    paths: list[pathlib.Path],
    plan_path: pathlib.Path,
    stored_plan_file: _StoredPlanFile,
    message: str,
    normalized_dependencies: tuple[str, ...],
    normalized_target_repo: str | None,
    local_worktree: pathlib.Path | None,
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    hold_dir: pathlib.Path,
    skip_push: bool,
) -> dict[str, object]:
    """holdの全入力を最古の1件へ統合し、inboxへ原子的に保存する。"""
    material_names, source_description = _read_plan_input_filenames(plan_path)
    normalized_material_names = tuple(_validate_filename(name, inbox_dir).name for name in material_names)
    if len(set(normalized_material_names)) != len(normalized_material_names):
        raise WebInputError(f"{source_description}に重複したファイル名があります")
    material_paths = _plan_awi_paths(private_notes, normalized_material_names, source_description)
    input_names = tuple(path.name for path in paths)
    awi_names = tuple(path.name for path in material_paths)
    if tuple(sorted(input_names)) != tuple(sorted(awi_names)):
        raise WebInputError(f"convert-to-planの入力と{source_description}が一致しません")
    if local_worktree is None:
        raise WebInputError("holdの変換には対象リポジトリのローカルworktreeが必要です")
    _assert_conversion_targets_tracked(private_notes, paths)
    snapshots = [(path, path.read_text(encoding="utf-8")) for path in sorted(paths, key=lambda item: item.name)]

    repositories: set[str] = set()
    dependencies: list[str] = []
    parsed_entries: dict[pathlib.Path, tuple[dict[str, object], str]] = {}
    for path, text in snapshots:
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            raise WebInputError(f"frontmatterが破損しているため変換できません: {path.name}")
        data, body = parsed
        if _require_type(path, text) != WI_TYPE_AWI:
            raise WebInputError(f"AWIだけを計画実装型へ変換できます: {path.name}")
        raw_entry_repo = data.get("target_repo")
        if not isinstance(raw_entry_repo, str):
            raise WebInputError(f"target_repoが不正です: {path.name}")
        entry_repo = _resolve_repo_id(raw_entry_repo)
        repositories.add(entry_repo)
        if normalized_target_repo is not None and entry_repo != normalized_target_repo:
            raise WebInputError(f"target_repoが一致しません: {path.name}は{entry_repo}、指定値は{normalized_target_repo}")
        if "plan_file" in data:
            raise WebInputError(f"既に計画型のため再変換できません: {path.name}")
        entry_dependencies = _entry_dependencies_for_conversion(path, data)
        dependencies.extend(_validate_filename(value, inbox_dir).name for value in entry_dependencies)
        parsed_entries[path] = (data, body)
    if len(repositories) != 1:
        raise WebInputError("変換対象は同一target_repoで指定してください")
    material_repo = next(iter(repositories))
    if normalized_target_repo is not None and material_repo != normalized_target_repo:
        raise WebInputError(f"target_repoが一致しません: 期待={normalized_target_repo} 実際={material_repo}")
    if _local_worktree_repo_id(local_worktree) != material_repo:
        raise WebInputError("holdの変換対象repoとローカルworktreeが一致しません")
    target_commit = _resolve_plan_base_commit(plan_path, local_worktree)

    message_frontmatter, message_body = _add.parse_entry_message(message, entry_type=WI_TYPE_AWI)
    requested_type = message_frontmatter.get("type")
    if requested_type is not None and requested_type != WI_TYPE_AWI:
        raise WebInputError(f"holdの統合本文のtypeは{WI_TYPE_AWI}で指定してください")
    for key in ("target_commit", "depends_on", "plan_file", "queue_schedule", "cooldown_until"):
        if key in message_frontmatter:
            raise WebInputError(f"{key}はholdの統合処理が管理する予約キーです")
    updates = dict(message_frontmatter)
    if "target_repo" in updates:
        raw_target_repo = updates["target_repo"]
        if not isinstance(raw_target_repo, str):
            raise WebInputError("target_repoは文字列で指定してください")
        updates["target_repo"] = _resolve_repo_id(raw_target_repo)
        if updates["target_repo"] != material_repo:
            raise WebInputError(f"target_repoが一致しません: 期待={material_repo} 実際={updates['target_repo']}")

    oldest_path = min(snapshots, key=lambda item: item[0].name)[0]
    oldest_data, _oldest_body = parsed_entries[oldest_path]
    updated_data = {**oldest_data, **updates}
    updated_data["source"] = "plan-and-add-awi"
    _store_plan_file(updated_data, stored_plan_file)
    updated_data["target_commit"] = target_commit
    updated_data.pop("queue_schedule", None)
    updated_data.pop("cooldown_until", None)
    all_dependencies = dependencies + list(normalized_dependencies)
    excluded_inputs = set(input_names)
    canonical_dependencies = tuple(
        value for value in dict.fromkeys(all_dependencies) if value not in excluded_inputs and value != oldest_path.name
    )
    dependency_graph = _active_dependency_graph(inbox_dir, processing_dir, hold_dir)
    dependency_graph[oldest_path.name] = set(canonical_dependencies)
    if any(_dependency_reaches(dependency_graph, dependency, oldest_path.name) for dependency in canonical_dependencies):
        raise WebInputError(f"循環する依存を指定できません: {oldest_path.name}")
    if canonical_dependencies:
        updated_data["depends_on"] = list(canonical_dependencies)
    else:
        updated_data.pop("depends_on", None)
    updated_text = _frontmatter.serialize_frontmatter(
        updated_data,
        "\n" + message_body.strip("\n").rstrip() + "\n",
    )

    inbox_path = inbox_dir / oldest_path.name
    if inbox_path.exists():
        raise WebInputError(f"inboxに同名エントリが既に存在します: {oldest_path.name}")
    source_paths = tuple(path for path, _text in snapshots)
    source_relative_paths = tuple(str(path.relative_to(private_notes)) for path in source_paths)
    destination_relative_path = str(inbox_path.relative_to(private_notes))
    commit_paths = (*source_relative_paths, destination_relative_path)
    start_head = _git_head(private_notes)
    try:
        _atomic_write_text(inbox_path, updated_text)
        for path in source_paths:
            path.unlink()
        try:
            _commit_and_push(
                private_notes,
                "chore: convert awi items to plans",
                commit_paths,
                skip_push=skip_push,
            )
        except (OSError, subprocess.SubprocessError) as error:
            if _git_head(private_notes) == start_head:
                _restore_conversion_paths(
                    private_notes,
                    start_head,
                    source_relative_paths,
                    (destination_relative_path,),
                )
            raise error
        if not inbox_path.is_file() or any(path.exists() for path in source_paths):
            raise RuntimeError("計画型変換後の保存集合を検証できません")
        return {
            "entries": [
                _add._read_saved_entry_details(  # pylint: disable=protected-access
                    inbox_path,
                    expected_body=updated_text,
                )
            ],
            "plan_file": stored_plan_file,
            "commit": _git_head(private_notes),
            "integrated": True,
        }
    except Exception as error:
        command = getattr(error, "cmd", ())
        push_failure = any(part == "push" for part in command)
        if _git_head(private_notes) == start_head and not push_failure:
            _restore_conversion_paths(
                private_notes,
                start_head,
                source_relative_paths,
                (destination_relative_path,),
            )
        raise


def convert_entries_to_plan(
    private_notes: pathlib.Path,
    *,
    filenames: tuple[str, ...],
    plan_file: str,
    message: str | None = None,
    depends_on: tuple[str, ...] | None = None,
    target_repo: str | None = None,
    lock_timeout: float = -1,
    skip_push: bool = False,
    local_worktree: pathlib.Path | None = None,
) -> dict[str, object]:
    """状態別のawiを計画実装型へ変換し、holdは1件へ統合する。"""
    if not filenames:
        raise WebInputError("変換するFILENAMEを1件以上指定してください")
    try:
        stored_plan_file = _normalize_stored_plan_file(plan_file, private_notes=private_notes)
    except ValueError as error:
        raise WebInputError(f"plan_fileを解決できません: {plan_file}（{error}）") from error
    inbox_dir = private_notes / WI_STATE_INBOX
    processing_dir = private_notes / WI_STATE_PROCESSING
    normalized_filenames = tuple(dict.fromkeys(_validate_filename(name, inbox_dir).name for name in filenames))
    if len(normalized_filenames) != len(filenames):
        raise WebInputError("同じFILENAMEを重複して指定できません")
    normalized_dependencies = tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in (depends_on or ())))
    normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None

    with _repo_lock(private_notes, timeout=lock_timeout):
        _push_pending_commits(private_notes)
        _pull(private_notes)
        try:
            plan_path = _plan_file.require_saved_plan_file(stored_plan_file, private_notes=private_notes)
        except ValueError as error:
            raise WebInputError(f"plan_fileを解決できません: {plan_file}（{error}）") from error
        except OSError as error:
            raise WebInputError(f"plan_fileを検証できません: {plan_file}") from error
        hold_dir = _subdir(private_notes, WI_STATE_HOLD)
        state, paths = _resolve_conversion_targets(
            normalized_filenames,
            inbox_dir,
            processing_dir,
            hold_dir,
        )
        if state == WI_STATE_HOLD:
            if message is None:
                raise WebInputError("holdの入力には--messageを指定してください")
            destination = inbox_dir / min(paths, key=lambda path: path.name).name
            _assert_conversion_paths_clean(private_notes, [*paths, destination])
            return _convert_held_entries(
                private_notes,
                paths=paths,
                plan_path=plan_path,
                stored_plan_file=stored_plan_file,
                message=message,
                normalized_dependencies=normalized_dependencies,
                normalized_target_repo=normalized_target_repo,
                local_worktree=local_worktree,
                inbox_dir=inbox_dir,
                processing_dir=processing_dir,
                hold_dir=hold_dir,
                skip_push=skip_push,
            )
        if message is not None:
            raise WebInputError("inbox・processingの入力には--messageを指定できません")
        if len(paths) != len(normalized_filenames):
            raise WebInputError("変換対象を一意に特定できません")
        _assert_conversion_paths_clean(private_notes, paths)
        _assert_conversion_targets_tracked(private_notes, paths)
        snapshots = [(path, path.read_text(encoding="utf-8")) for path in paths]
        dependency_graph = _active_dependency_graph(inbox_dir, processing_dir)
        if normalized_dependencies:
            dependency_graph.update({path.name: set(normalized_dependencies) for path, _text in snapshots})
            for path, _text in snapshots:
                if path.name in normalized_dependencies:
                    raise WebInputError(f"自分自身を依存先へ指定できません: {path.name}")
            if any(
                _dependency_reaches(dependency_graph, dependency, path.name)
                for path, _text in snapshots
                for dependency in normalized_dependencies
            ):
                raise WebInputError("循環する依存を指定できません")

        updated: list[tuple[pathlib.Path, str, str]] = []
        repositories: set[str] = set()
        for path, text in snapshots:
            parsed = _frontmatter.parse_frontmatter(text)
            if parsed is None:
                raise WebInputError(f"frontmatterが破損しているため変換できません: {path.name}")
            data, body = parsed
            if _require_type(path, text) != WI_TYPE_AWI:
                raise WebInputError(f"AWIだけを計画実装型へ変換できます: {path.name}")
            raw_entry_repo = data.get("target_repo")
            if not isinstance(raw_entry_repo, str):
                raise WebInputError(f"target_repoが不正です: {path.name}")
            entry_repo = _resolve_repo_id(raw_entry_repo)
            repositories.add(entry_repo)
            if normalized_target_repo is not None and entry_repo != normalized_target_repo:
                raise WebInputError(f"target_repoが一致しません: {path.name}は{entry_repo}、指定値は{normalized_target_repo}")
            if "plan_file" in data:
                raise WebInputError(f"既に計画型のため再変換できません: {path.name}")
            if depends_on is None:
                stored_dependencies = _entry_dependencies_for_conversion(path, data)
                if stored_dependencies:
                    data["depends_on"] = list(stored_dependencies)
            if depends_on is not None:
                if path.name in normalized_dependencies:
                    raise WebInputError(f"自分自身を依存先へ指定できません: {path.name}")
                if normalized_dependencies:
                    data["depends_on"] = list(normalized_dependencies)
                else:
                    data.pop("depends_on", None)
            _store_plan_file(data, stored_plan_file)
            data.pop("queue_schedule", None)
            updated_text = _frontmatter.serialize_frontmatter(data, body)
            updated.append((path, text, updated_text))
        if len(repositories) != 1:
            raise WebInputError("変換対象は同一target_repoで指定してください")

        start_head = _git_head(private_notes)
        relative_paths = tuple(str(path.relative_to(private_notes)) for path, _old, _new in updated)
        try:
            for path, _old, new in updated:
                if new != _old:
                    _atomic_write_text(path, new)
            changed_paths = tuple(path for path, old, new in updated if old != new)
            if not changed_paths:
                return {
                    "entries": [
                        _add._read_saved_entry_details(path, expected_body=new)  # pylint: disable=protected-access
                        for path, _old, new in updated
                    ],
                    "plan_file": stored_plan_file,
                    "commit": None,
                    "integrated": False,
                }
            try:
                _commit_and_push(
                    private_notes,
                    "chore: convert awi items to plans",
                    relative_paths,
                    skip_push=skip_push,
                )
            except (OSError, subprocess.SubprocessError) as error:
                if _git_head(private_notes) == start_head:
                    _restore_conversion_paths(private_notes, start_head, relative_paths)
                raise error
            commit_oid = _git_head(private_notes)
            return {
                "entries": [
                    _add._read_saved_entry_details(path, expected_body=new)  # pylint: disable=protected-access
                    for path, _old, new in updated
                ],
                "plan_file": stored_plan_file,
                "commit": commit_oid,
                "integrated": False,
            }
        except Exception as error:
            command = getattr(error, "cmd", ())
            push_failure = any(part == "push" for part in command)
            if _git_head(private_notes) == start_head and not push_failure:
                _restore_conversion_paths(private_notes, start_head, relative_paths)
            raise


def convert_entry_to_plan(
    private_notes: pathlib.Path,
    *,
    filename: str,
    plan_file: str,
    depends_on: tuple[str, ...] | None = None,
    target_repo: str | None = None,
    lock_timeout: float = -1,
    skip_push: bool = False,
) -> dict[str, object]:
    """既存の単一項目APIを一括変換経路へ委譲する。"""
    result = convert_entries_to_plan(
        private_notes,
        filenames=(filename,),
        plan_file=plan_file,
        depends_on=depends_on,
        target_repo=target_repo,
        lock_timeout=lock_timeout,
        skip_push=skip_push,
    )
    entries = result["entries"]
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, dict)
    return entry


def _cmd_convert_to_plan(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """convert-to-planサブコマンドを実行する。"""
    message = getattr(args, "message", None)
    target_repo, local_worktree = _add.resolve_add_target(args.target_repo)
    if message is not None and local_worktree is None:
        local_worktree = _candidate_local_worktree(args.target_repo)
    skip_push = getattr(args, "skip_push", False)
    raw_filenames = args.filename
    filenames = (raw_filenames,) if isinstance(raw_filenames, str) else tuple(raw_filenames)
    single_compat = len(filenames) == 1
    dependencies = tuple(args.depends_on) if args.depends_on is not None else None
    try:
        result = convert_entries_to_plan(
            private_notes,
            filenames=filenames,
            plan_file=args.plan_file,
            message=message,
            depends_on=dependencies,
            target_repo=target_repo,
            skip_push=skip_push,
            local_worktree=local_worktree,
        )
        entries = result["entries"]
        if not isinstance(entries, list):
            raise RuntimeError("複数変換結果のentriesがリストではありません")
    except WebInputError as error:
        print(f"変換を拒否しました: {error}", file=sys.stderr)
        sys.exit(1)
    for filename in filenames:
        print(f"計画実装型へ変換: {filename}")
    if not single_compat or result.get("integrated") is True:
        commit_oid = result.get("commit")
        if commit_oid:
            print(f"変換commit: {commit_oid}")
        print("push: 省略 (--skip-push)" if skip_push else "push: 完了")
    for details in entries:
        _add._print_entry_details(details)  # pylint: disable=protected-access
