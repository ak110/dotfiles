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
    from _atk.wi.mutations.dependencies import (
        _active_dependency_graph,
        _cmd_set_dependencies,
        _dependency_reaches,
        _entry_dependencies,
        _entry_dependencies_for_conversion,
        set_entry_dependencies,
    )

_GIT_TIMEOUT_SECONDS = 10.0


def _entry_target_repo(path: pathlib.Path, text: str) -> str:
    """エントリの`target_repo`を検証し、正規化した識別子を返す。"""
    parsed = _frontmatter.parse_frontmatter(text)
    if parsed is None:
        print(f"frontmatterを解析できないため処理を停止しました: {path}", file=sys.stderr)
        sys.exit(2)
    raw_target_repo = parsed[0].get("target_repo")
    if not isinstance(raw_target_repo, str) or not raw_target_repo:
        print(f"frontmatterにtarget_repoがないため処理を停止しました: {path}", file=sys.stderr)
        sys.exit(2)
    return _resolve_repo_id(raw_target_repo)


def _candidate_local_worktree(target_repo: str | None) -> pathlib.Path | None:
    """実在パスの引数を優先し、それ以外は現在位置から対応候補の作業ツリーを返す。"""
    if target_repo is not None:
        path = pathlib.Path(target_repo).expanduser()
        if path.exists():
            return path.resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = result.stdout.strip()
    return pathlib.Path(output) if result.returncode == 0 and output else None


def _local_worktree_repo_id(local_worktree: pathlib.Path) -> str | None:
    """作業ツリーのoriginから対象リポジトリ識別子を返す。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(local_worktree), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return _normalize_remote_url(result.stdout.strip())
    except ValueError:
        return None


def _resolve_commit(local_worktree: pathlib.Path, revision: str) -> str:
    """作業ツリーでrevisionを完全なcommit OIDへ解決する。"""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(local_worktree),
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{revision}^{{commit}}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    commit = result.stdout.strip() if result is not None and result.returncode == 0 else ""
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        print(
            f"対応commitを解決できませんでした。対象作業ツリーでrevisionを取得して再実行してください: "
            f"{local_worktree} ({revision})",
            file=sys.stderr,
        )
        sys.exit(2)
    return commit


def _commit_values_by_path(
    paths: list[pathlib.Path],
    revision: str | None,
    local_worktree: pathlib.Path | None,
) -> dict[pathlib.Path, str | None]:
    """対象ごとに記録するcommitを解決し、対応不能な群は警告する。"""
    if revision is None:
        return {path: None for path in paths}
    target_repos = {path: _entry_target_repo(path, path.read_text(encoding="utf-8")) for path in paths}
    candidate_repo = _local_worktree_repo_id(local_worktree) if local_worktree is not None else None
    resolved = (
        _resolve_commit(local_worktree, revision)
        if local_worktree is not None and candidate_repo in target_repos.values()
        else None
    )
    values: dict[pathlib.Path, str | None] = {}
    warned: set[str] = set()
    for path, target_repo in target_repos.items():
        if resolved is not None and target_repo == candidate_repo:
            values[path] = resolved
            continue
        values[path] = revision
        if target_repo not in warned:
            print(
                f"警告: 対象リポジトリに対応するローカル作業ツリーを特定できないため、"
                f"対応commitを未検証のまま記録します: {target_repo}",
                file=sys.stderr,
            )
            warned.add(target_repo)
    return values


def _invalidate_repo_bound_metadata(original: str, updated: str) -> str:
    """target_repo変更時に旧リポジトリへ結び付くメタデータを削除する。"""
    original_parsed = _frontmatter.parse_frontmatter(original)
    updated_parsed = _frontmatter.parse_frontmatter(updated)
    if original_parsed is None or updated_parsed is None:
        return updated
    original_data, _ = original_parsed
    updated_data, updated_body = updated_parsed
    if original_data.get("target_repo") == updated_data.get("target_repo"):
        return updated
    updated_data.pop("target_commit", None)
    return _frontmatter.serialize_frontmatter(updated_data, updated_body)


def commit_entries(private_notes: pathlib.Path, *, lock_timeout: float = -1) -> bool:
    """平引数でprivate-notesの作業ツリー全体の外部編集差分をcommit・pushする。

    差分がない場合も滞留commitをpushし、外部編集によるcommitを行ったかを返す。
    """
    with _repo_lock(private_notes, timeout=lock_timeout):
        _push_pending_commits(private_notes)
        _pull(private_notes)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=private_notes,
            check=True,
            capture_output=True,
            text=True,
        )
        if not status.stdout.strip():
            _push_pending_commits(private_notes)
            return False
        _commit_and_push(private_notes, "chore: edit private notes externally", ["."])
    return True


def _resolve_awi_targets(
    filenames: list[str],
    awi_dir: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
) -> list[pathlib.Path]:
    """`awi_dir`配下のファイル名群を検証・解決し、未存在があればexit 2する。

    `awi_dir`には`start-processing`はinbox、`return-to-inbox`はprocessingが渡される。
    エラーメッセージは`awi_dir.name`から動的に状態名を組み込み、呼び出し元の状態と一致させる。
    """
    paths = [_validate_filename(f, awi_dir) for f in filenames]
    missing = [p for p in paths if not p.exists()]
    if missing:
        if missing_is_conflict:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        for p in missing:
            print(f"{awi_dir.name}に存在しません: {p.name}", file=sys.stderr)
        sys.exit(2)
    return paths


def _resolve_processable_targets(
    filenames: list[str],
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
) -> list[pathlib.Path]:
    """inboxまたはprocessing配下のファイル名群を検証・解決し、未存在があればexit 2する。

    同一ファイルがinbox・processingの双方に存在する場合はprocessingを優先する
    （`start-processing`後の中断復帰時にprocessing側が最新状態のため）。
    """
    resolved: list[pathlib.Path] = []
    missing: list[str] = []
    for name in filenames:
        # 検証はinbox基準ディレクトリで行うが、実体はいずれか片方の状態フォルダに存在する。
        # `_validate_filename`側で拡張子`.md`の省略を正規形へ補完する。
        inbox_path = _validate_filename(name, inbox_dir)
        processing_path = _validate_filename(inbox_path.name, processing_dir)
        if processing_path.exists():
            resolved.append(processing_path)
        elif inbox_path.exists():
            resolved.append(inbox_path)
        else:
            missing.append(inbox_path.name)
    if missing:
        if missing_is_conflict:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        for name in missing:
            print(f"inbox・processingのいずれにも存在しません: {name}", file=sys.stderr)
        sys.exit(2)
    return resolved


def _resolve_editable_targets(
    filenames: list[str],
    private_notes: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
) -> list[pathlib.Path]:
    """編集対象を解決し、解決した保存状態のまま本文を書き戻す。"""
    resolved: list[pathlib.Path] = []
    missing: list[str] = []
    for name in filenames:
        normalized = _validate_filename(name, private_notes / WI_STATE_INBOX).name
        path = next(
            (
                private_notes / state_name / normalized
                for state_name in WI_EDITABLE_STATES
                if (private_notes / state_name / normalized).exists()
            ),
            None,
        )
        if path is None:
            missing.append(normalized)
        else:
            resolved.append(path)
    if missing:
        if missing_is_conflict:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        for name in missing:
            print(f"inbox・processing・holdのいずれにも存在しません: {name}", file=sys.stderr)
        sys.exit(2)
    return resolved


def _resolve_conversion_targets(
    filenames: tuple[str, ...],
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    hold_dir: pathlib.Path,
) -> tuple[str, list[pathlib.Path]]:
    """convert-to-planの入力を状態ごとに解決し、混在を拒否する。"""
    resolved: list[pathlib.Path] = []
    missing: list[str] = []
    for name in filenames:
        inbox_path = _validate_filename(name, inbox_dir)
        processing_path = _validate_filename(inbox_path.name, processing_dir)
        hold_path = _validate_filename(inbox_path.name, hold_dir)
        if hold_path.exists():
            if inbox_path.exists() or processing_path.exists():
                raise WebInputError(f"異なる状態の同名項目が存在するため変換できません: {hold_path.name}")
            resolved.append(hold_path)
        elif processing_path.exists():
            resolved.append(processing_path)
        elif inbox_path.exists():
            resolved.append(inbox_path)
        else:
            missing.append(inbox_path.name)
    if missing:
        for name in missing:
            print(f"inbox・processing・holdのいずれにも存在しません: {name}", file=sys.stderr)
        sys.exit(2)
    states = {path.parent.name for path in resolved}
    if len(states) != 1:
        raise WebInputError("異なる状態の入力を混在させて変換できません")
    return next(iter(states)), resolved


def _resolve_removable_targets(
    filenames: list[str],
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
) -> list[pathlib.Path]:
    """rmの対象をprocessing、inbox、holdの優先順で解決する。"""
    resolved: list[pathlib.Path] = []
    missing: list[str] = []
    for name in filenames:
        normalized = _validate_filename(name, inbox_dir).name
        candidates = (
            processing_dir / normalized,
            inbox_dir / normalized,
            inbox_dir.parent / WI_STATE_HOLD / normalized,
        )
        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            missing.append(normalized)
        else:
            resolved.append(path)
    if missing:
        if missing_is_conflict:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        for name in missing:
            print(f"inbox・processing・holdのいずれにも存在しません: {name}", file=sys.stderr)
        sys.exit(2)
    return resolved


def _atomic_write_text(path: pathlib.Path, content: str) -> None:
    """同一ディレクトリの一時ファイルから置換してUTF-8本文を原子的に保存する。"""
    encoded = _frontmatter.normalize_newlines(content).encode("utf-8")
    temporary_path: pathlib.Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(encoded)
            temporary.flush()
            temporary_path = pathlib.Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _git_head(private_notes: pathlib.Path) -> str:
    """管理repoのHEADを完全OIDで返す。"""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
        cwd=private_notes,
        capture_output=True,
        text=True,
        check=True,
    )
    commit = result.stdout.strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        raise RuntimeError(f"管理repoのHEADが完全OIDではありません: {commit!r}")
    return commit


def _cmd_commit(private_notes: pathlib.Path) -> None:
    """commitサブコマンド: 外部編集後のprivate-notesの未コミット変更をコミット・push。

    未コミット変更がない場合も滞留commitをpushする。
    """
    if commit_entries(private_notes):
        print("private-notesの外部編集分をコミット・pushしました。")
    else:
        print("差分なし。滞留commitをpushしました。")
