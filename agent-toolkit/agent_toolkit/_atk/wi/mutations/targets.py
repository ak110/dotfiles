"""WIの変更処理が共有する、操作対象のエントリと対象リポジトリの解決、private-notesへのcommit。

`atk wi commit`の処理本体と、変更処理が対象のcommitを解決する手順も持つ。
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import subprocess
import sys
import tempfile

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi.common import (
    WI_EDITABLE_STATES,
    WI_STATE_HOLD,
    WI_STATE_INBOX,
    WI_STATE_PROCESSING,
    WebInputError,
    _commit_and_push,
    _CommitMetadata,
    _pull,
    _push_pending_commits,
    _repo_lock,
    _validate_filename,
)
from agent_toolkit._atk.wi.constants import unrepairable_entry_next_action as _unrepairable_entry_next_action
from agent_toolkit._atk.wi.repo import (
    _normalize_remote_url,
    _resolve_repo_id,
)

_GIT_TIMEOUT_SECONDS = 10.0
_MISSING_TARGET_NEXT_ACTION = "`atk wi list`で実在するファイル名を確かめて指定し直す"


def _entry_target_repo(path: pathlib.Path, text: str) -> str:
    """エントリの`target_repo`を検証し、正規化した識別子を返す。"""
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
            encoding="utf-8",
            errors="replace",
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
            encoding="utf-8",
            errors="replace",
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


def _resolve_commit_oid(local_worktree: pathlib.Path, revision: str) -> str:
    """作業ツリーでrevisionをcommitの40桁または64桁OIDへ解決する。"""
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
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    commit = result.stdout.strip() if result is not None and result.returncode == 0 else ""
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        _outcome.report_failure(
            f"対応commitを解決できない: {local_worktree} ({revision})",
            next_action="対象作業ツリーでrevisionを取得して再実行する",
        )
        sys.exit(2)
    return commit


def _resolve_commit(local_worktree: pathlib.Path, revision: str) -> _CommitMetadata:
    """作業ツリーでrevisionを解決し、永続記録用の一意な長さの短縮OIDと件名を返す。"""
    commit = _resolve_commit_oid(local_worktree, revision)
    try:
        result = subprocess.run(
            ["git", "-C", str(local_worktree), "show", "-s", "--format=%h%x00%s", commit],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    output = result.stdout.rstrip("\n") if result is not None and result.returncode == 0 else ""
    short_oid, separator, subject = output.partition("\0")
    if separator != "\0" or not commit.startswith(short_oid) or not short_oid or not subject or "\n" in subject:
        _outcome.report_failure(
            f"対応commitの短縮OIDと件名を取得できない: {local_worktree} ({revision})",
            next_action="--commitへ対象リポジトリで解決できるrevisionを指定し直す",
        )
        sys.exit(2)
    return _CommitMetadata(short_oid=short_oid, subject=subject)


def _commit_values_by_path(
    paths: list[pathlib.Path],
    revision: str | None,
    local_worktree: pathlib.Path | None,
) -> dict[pathlib.Path, _CommitMetadata | None]:
    """対象ごとに永続記録用のcommit情報を解決する。"""
    if revision is None:
        return {path: None for path in paths}
    target_repos = {path: _entry_target_repo(path, path.read_text(encoding="utf-8")) for path in paths}
    candidate_repo = _local_worktree_repo_id(local_worktree) if local_worktree is not None else None
    unmatched = sorted({target_repo for target_repo in target_repos.values() if target_repo != candidate_repo})
    if local_worktree is None or candidate_repo is None or unmatched:
        targets = ", ".join(unmatched or sorted(set(target_repos.values())))
        _outcome.report_failure(
            f"対応commitを検証できる対象リポジトリの作業ツリーを特定できない: {targets}",
            next_action="対象リポジトリの作業ツリー内で実行するか、--target-repoへその作業ツリーのパスを指定して再実行する",
        )
        sys.exit(2)
    resolved = _resolve_commit(local_worktree, revision)
    return dict.fromkeys(paths, resolved)


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


@dataclasses.dataclass(frozen=True)
class CommitEntriesResult:
    """外部編集のcommitと、その実行で送信したcommitの結果。"""

    changed: bool
    has_remote: bool
    pushed_commits: int | None


def commit_entries(private_notes: pathlib.Path, *, lock_timeout: float = -1) -> CommitEntriesResult:
    """平引数でprivate-notesの作業ツリー全体の外部編集差分をcommit・pushする。

    差分がない場合も滞留commitをpushし、外部編集とremoteの有無・送信件数を返す。
    """
    with _repo_lock(private_notes, timeout=lock_timeout):
        has_remote = _atk_git_sync.has_remote(private_notes)
        initial_pushed = _push_pending_commits(private_notes)
        _pull(private_notes)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=private_notes,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        changed = bool(status.stdout.strip())
        final_pushed = (
            _commit_and_push(private_notes, "chore: edit private notes externally", ["."])
            if changed
            else _push_pending_commits(private_notes)
        )
        pushed = None if initial_pushed is None or final_pushed is None else initial_pushed + final_pushed
        return CommitEntriesResult(changed, has_remote, pushed)


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
            _outcome.report_failure(f"{awi_dir.name}に存在しない: {p.name}", next_action=_MISSING_TARGET_NEXT_ACTION)
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
            _outcome.report_failure(f"inbox・processingのいずれにも存在しない: {name}", next_action=_MISSING_TARGET_NEXT_ACTION)
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
            _outcome.report_failure(
                f"inbox・processing・holdのいずれにも存在しない: {name}", next_action=_MISSING_TARGET_NEXT_ACTION
            )
        sys.exit(2)
    return resolved


def _resolve_active_targets(
    filenames: list[str],
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
    states: tuple[str, ...] | None = None,
) -> list[pathlib.Path]:
    """対象を指定状態の優先順で解決する。

    `rm`と`set-dependencies`のように、保存状態を変えずに未終端の項目へ作用する操作が使う。
    `states`省略時は従来どおりprocessing、inbox、holdの順で解決する。
    """
    state_names = states or (WI_STATE_PROCESSING, WI_STATE_INBOX, WI_STATE_HOLD)
    resolved: list[pathlib.Path] = []
    missing: list[str] = []
    for name in filenames:
        normalized = _validate_filename(name, inbox_dir).name
        candidates = (
            tuple(inbox_dir.parent / state_name / normalized for state_name in states)
            if states is not None
            else (
                processing_dir / normalized,
                inbox_dir / normalized,
                inbox_dir.parent / WI_STATE_HOLD / normalized,
            )
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
            _outcome.report_failure(
                f"{'・'.join(state_names)}のいずれにも存在しない: {name}", next_action=_MISSING_TARGET_NEXT_ACTION
            )
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
    """管理repoのHEADを40桁または64桁OIDで返す。"""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
        cwd=private_notes,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    commit = result.stdout.strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        raise WebInputError(
            f"管理repoのHEADが40桁または64桁OIDではありません: {commit!r}",
            next_action=f"`git -C {private_notes} status`で管理repoの状態を確認する。解消しない場合はユーザーへ報告する",
        )
    return commit


def _cmd_commit(private_notes: pathlib.Path) -> None:
    """commitサブコマンド: 外部編集後のprivate-notesの未コミット変更をコミット・push。

    未コミット変更がない場合も滞留commitをpushする。
    """
    result = commit_entries(private_notes)
    edited = "外部編集分をcommitした" if result.changed else "外部編集の差分は無い"
    if not result.has_remote:
        push = "remoteが無いためpushしていない"
    elif result.pushed_commits is None:
        push = "pushを完了した（送信件数は取得できない）"
    elif result.pushed_commits == 0:
        push = "pushするcommitは無かった"
    else:
        push = f"{result.pushed_commits}件のcommitをpushした"
    _outcome.report_success(f"{edited}。{push}")
