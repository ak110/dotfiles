from __future__ import annotations

import dataclasses
import pathlib
import re
import subprocess
import sys
import tempfile

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import branches as _wi_branches
from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.constants import WI_EDITABLE_STATES, WI_STATE_HOLD, WI_STATE_INBOX, WI_STATE_PROCESSING
from agent_toolkit._atk.wi.constants import unrepairable_entry_next_action as _unrepairable_entry_next_action
from agent_toolkit._atk.wi.entries import CommitMetadata
from agent_toolkit._atk.wi.layout import entry_state_of, iter_entry_paths, unique_dated_entry_path
from agent_toolkit._atk.wi.repo import (
    resolve_repo_id,
)
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common.next_action import recover_from_failure
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import remote as _git_remote

_GIT_TIMEOUT_SECONDS = 10.0
_MISSING_TARGET_NEXT_ACTION = "`atk wi list`で実在するファイル名を確かめて指定し直す"


def _resolve_state_targets(
    filenames: list[str], private_notes: pathlib.Path, states: tuple[str, ...], *, missing_is_conflict: bool
) -> list[pathlib.Path]:
    names = [_wi_filenames.validate_filename(name, private_notes).name for name in filenames]

    dated = {name: unique_dated_entry_path(private_notes, name) for name in names}
    if all(path is not None for path in dated.values()):
        candidates = {name: path for name, path in dated.items() if path is not None and entry_state_of(path) in states}
    else:
        candidates = {path.name: path for path in iter_entry_paths(private_notes) if entry_state_of(path) in states}
    missing = [name for name in names if name not in candidates]
    if missing:
        if missing_is_conflict:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        for name in missing:
            _outcome.report_failure(
                f"{'・'.join(states)}のいずれにも存在しない: {name}", next_action=_MISSING_TARGET_NEXT_ACTION
            )
        sys.exit(2)
    return [candidates[name] for name in names]


def entry_target_repo(path: pathlib.Path, text: str) -> str:
    parsed = _frontmatter.parse_frontmatter(text)
    if parsed is None:
        _outcome.report_failure(
            f"frontmatterを解析できないため処理を停止した: {path}",
            next_action=_unrepairable_entry_next_action(path.name),
        )
        sys.exit(2)
    raw_target_repo = parsed[0].get("target_repo")
    if not isinstance(raw_target_repo, str) or not raw_target_repo:
        _outcome.report_failure(
            f"frontmatterにtarget_repoが無いため処理を停止した: {path}",
            next_action=_unrepairable_entry_next_action(path.name),
        )
        sys.exit(2)
    return resolve_repo_id(raw_target_repo)


def candidate_local_worktree(target_repo: str | None) -> pathlib.Path | None:
    if target_repo is not None:
        path = pathlib.Path(target_repo).expanduser()
        if path.exists():
            return path.resolve()
    try:
        result = _git_command.run(
            ["rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False, timeout=_GIT_TIMEOUT_SECONDS
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    output = result.stdout.strip()
    return pathlib.Path(output) if result.returncode == 0 and output else None


def _local_worktree_repo_id(local_worktree: pathlib.Path) -> str | None:
    try:
        url = _git_remote.origin_url(local_worktree, timeout=_GIT_TIMEOUT_SECONDS)
    except OSError, subprocess.TimeoutExpired:
        return None
    if url is None:
        return None
    try:
        return _git_remote.normalize_remote_url(url)
    except ValueError:
        return None


def _resolve_commit_oid(local_worktree: pathlib.Path, revision: str) -> str:
    try:
        result = _git_command.run(
            ["-C", str(local_worktree), "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired:
        result = None
    commit = result.stdout.strip() if result is not None and result.returncode == 0 else ""
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        _outcome.report_failure(
            f"対応commitを解決できない: {local_worktree} ({revision})",
            next_action="対象作業ツリーでrevisionを取得して再実行する",
        )
        sys.exit(2)
    return commit


def resolve_commit(local_worktree: pathlib.Path, revision: str) -> CommitMetadata:
    commit = _resolve_commit_oid(local_worktree, revision)
    try:
        result = _git_command.run(
            ["-C", str(local_worktree), "show", "-s", "--format=%h%x00%s", commit],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired:
        result = None
    output = result.stdout.rstrip("\n") if result is not None and result.returncode == 0 else ""
    short_oid, separator, subject = output.partition("\0")
    if separator != "\0" or not commit.startswith(short_oid) or not short_oid or not subject or "\n" in subject:
        _outcome.report_failure(
            f"対応commitの短縮OIDと件名を取得できない: {local_worktree} ({revision})",
            next_action="--commitへ対象リポジトリで解決できるrevisionを指定し直す",
        )
        sys.exit(2)
    return CommitMetadata(short_oid=short_oid, subject=subject)


def commit_values_by_path(
    paths: list[pathlib.Path],
    revision: str | list[str] | None,
    local_worktree: pathlib.Path | None,
) -> dict[pathlib.Path, CommitMetadata | list[CommitMetadata] | None]:
    if revision is None:
        return {path: None for path in paths}
    target_repos = {path: entry_target_repo(path, path.read_text(encoding="utf-8")) for path in paths}
    candidate_repo = _local_worktree_repo_id(local_worktree) if local_worktree is not None else None
    unmatched = sorted({target_repo for target_repo in target_repos.values() if target_repo != candidate_repo})
    if local_worktree is None or candidate_repo is None or unmatched:
        targets = ", ".join(unmatched or sorted(set(target_repos.values())))
        _outcome.report_failure(
            f"対応commitを検証できる対象リポジトリの作業ツリーを特定できない: {targets}",
            next_action="対象リポジトリの作業ツリー内で実行するか、--target-repoへその作業ツリーのパスを指定して再実行する",
        )
        sys.exit(2)
    resolved = (
        [resolve_commit(local_worktree, item) for item in dict.fromkeys(revision)]
        if isinstance(revision, list)
        else resolve_commit(local_worktree, revision)
    )
    return dict.fromkeys(paths, resolved)


def invalidate_repo_bound_metadata(original: str, updated: str) -> str:
    original_parsed = _frontmatter.parse_frontmatter(original)
    updated_parsed = _frontmatter.parse_frontmatter(updated)
    if original_parsed is None or updated_parsed is None:
        return updated
    original_data, _ = original_parsed
    updated_data, updated_body = updated_parsed
    repo_changed = original_data.get("target_repo") != updated_data.get("target_repo")
    branch_changed = original_data.get(_wi_branches.KEY) != updated_data.get(_wi_branches.KEY)
    if not repo_changed and not branch_changed:
        return updated
    if repo_changed and not branch_changed:
        updated_data.pop(_wi_branches.KEY, None)
    branch = updated_data.get(_wi_branches.KEY)
    target_repo = updated_data.get("target_repo")
    if branch is not None:
        if not isinstance(branch, str) or not branch or not isinstance(target_repo, str):
            raise WebInputError(
                "target_branchは空でない文字列で指定してください",
                next_action="frontmatterのtarget_branchへ登録した系統の名前を指定するか、行を除いてdefault_branchの系統へ戻す",
            )
        _wi_branches.resolve_for_save(target_repo, branch, _wi_branches.registry())
    updated_data.pop("target_commit", None)
    return _frontmatter.serialize_frontmatter(updated_data, updated_body)


@dataclasses.dataclass(frozen=True)
class CommitEntriesResult:
    changed: bool
    has_remote: bool
    pushed_commits: int | None


def commit_entries(private_notes: pathlib.Path, *, lock_timeout: float | None = None) -> CommitEntriesResult:
    with _wi_sync.repo_lock(private_notes, timeout=lock_timeout):
        has_remote = _atk_git_sync.has_remote(private_notes)
        initial_pushed = _wi_sync.push_pending_commits(private_notes)
        _wi_sync.pull(private_notes)
        status = _git_command.run(["status", "--porcelain"], private_notes, check=True, capture_output=True, text=True)
        changed = bool(status.stdout.strip())
        final_pushed = (
            _wi_sync.commit_and_push(private_notes, "chore: edit private notes externally", ["."])
            if changed
            else _wi_sync.push_pending_commits(private_notes)
        )
        pushed = None if initial_pushed is None or final_pushed is None else initial_pushed + final_pushed
        return CommitEntriesResult(changed, has_remote, pushed)


def resolve_awi_targets(
    filenames: list[str],
    awi_dir: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
) -> list[pathlib.Path]:
    return _resolve_state_targets(filenames, awi_dir.parent, (awi_dir.name,), missing_is_conflict=missing_is_conflict)


def resolve_processable_targets(
    filenames: list[str],
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
) -> list[pathlib.Path]:
    del processing_dir
    return _resolve_state_targets(
        filenames, inbox_dir.parent, (WI_STATE_PROCESSING, WI_STATE_INBOX), missing_is_conflict=missing_is_conflict
    )


def resolve_editable_targets(
    filenames: list[str],
    private_notes: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
) -> list[pathlib.Path]:
    return _resolve_state_targets(filenames, private_notes, WI_EDITABLE_STATES, missing_is_conflict=missing_is_conflict)


def resolve_active_targets(
    filenames: list[str],
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
    states: tuple[str, ...] | None = None,
) -> list[pathlib.Path]:
    state_names = states or (WI_STATE_PROCESSING, WI_STATE_INBOX, WI_STATE_HOLD)
    del processing_dir
    return _resolve_state_targets(filenames, inbox_dir.parent, state_names, missing_is_conflict=missing_is_conflict)


def atomic_write_text(path: pathlib.Path, content: str) -> None:
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
    result = _git_command.run(
        ["rev-parse", "--verify", "HEAD^{commit}"], private_notes, capture_output=True, text=True, check=True
    )
    commit = result.stdout.strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        raise WebInputError(
            f"管理repoのHEADが40桁または64桁OIDではありません: {commit!r}",
            next_action=recover_from_failure(
                component="GitのHEAD",
                evidence="Gitの診断と元の出力",
                first_step=f"`git -C {private_notes} status`で管理repoの状態を確認する",
                retry="有効なOIDを確認してから未反映の処理を再試行する",
            ),
        )
    return commit


def cmd_commit(private_notes: pathlib.Path) -> None:
    result = commit_entries(private_notes)
    edited = "外部編集分をcommitした" if result.changed else "外部編集の差分は無い"
    if not result.has_remote:
        push = "remoteかupstreamが無いためpushしていない"
    elif result.pushed_commits is None:
        push = "pushを完了した（送信件数は取得できない）"
    elif result.pushed_commits == 0:
        push = "pushするcommitは無かった"
    else:
        push = f"{result.pushed_commits}件のcommitをpushした"
    _outcome.report_success(f"{edited}。{push}")
