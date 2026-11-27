"""private-notesを共有する`atk`コマンドのGit同期基盤。

MQ固有のfrontmatterや状態遷移を持たず、同じGit作業コピーを更新する処理に共通する
ロック、対象限定commit、remote同期、push再試行だけを提供する。呼び出し元は
`repo_lock()`保持下でファイル変更とGit操作を行う。

private-notesへcommitしたか、未pushのcommitの送信を試みた実行は、副作用を確定した後も未pushのcommitが
残る場合に、呼び出し元のCLIが専用の終了コードで同期未達を通知する。commitも送信も試みない実行
（保存しない検証、差分の無い編集など）は、他の主体が残した未pushを自分の結果として通知しない。
commitと送信の試行、同期の保留は本モジュールの内部分岐で決まり、各サブコマンドからは観測できないため、
通知の判定材料（`sync_attempted`、`push_was_deferred`）を本モジュールが提供する。
pullとpushの失敗も呼び出し元の実行主体が次の操作を決められる必要があるため、
失敗理由を示すgitの出力に続けて、本モジュールが確認コマンドと再実行の手順を出力する。
前提条件の確認と失敗時の復元は、その操作が書き込む対象パスへ限定する。共有Git作業コピーへ
複数の実行主体が並行して書き込むため、repo全体を対象とする前提と復元は他の主体の成果を
壊すか、その操作を恒常的に成立させなくする。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from typing import Protocol

from agent_toolkit._atk import git_sync_diagnostics as _diagnostics
from agent_toolkit._atk import git_sync_lock as _lock
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.environment import is_human_terminal
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._git import command as _git_command

GIT_TIMEOUT_NEXT_ACTION = _diagnostics.GIT_TIMEOUT_NEXT_ACTION
GitSyncError = _diagnostics.GitSyncError
RebaseInProgressError = _diagnostics.RebaseInProgressError
git_timeout_reason = _diagnostics.git_timeout_reason
is_reported = _diagnostics.is_reported
mark_reported = _diagnostics.mark_reported
LOCK_HOLD_TIMEOUT_SECONDS = _lock.LOCK_HOLD_TIMEOUT_SECONDS
LOCK_OBSERVER = _lock.LOCK_OBSERVER
LOCK_WAIT_TIMEOUT_SECONDS = _lock.LOCK_WAIT_TIMEOUT_SECONDS
RepoLockTimeoutError = _lock.RepoLockTimeoutError
assert_repo_lock_held = _lock.assert_repo_lock_held
git_common_dir = _lock.git_common_dir
repo_lock = _lock.repo_lock
repo_lock_path = _lock.repo_lock_path
_GitResultRunner = _diagnostics.GitResultRunner
_report_sync_failure = _diagnostics.report_sync_failure


def keep_remote_initial_file(notes: pathlib.Path, filename: str, migration_message: str) -> bool:
    """排他中に競合した初回移行だけを除き、後続のローカルcommitを共有先に対して適用する。  pyfltr: allow-comment"""
    commits = _git_command.output(["rev-list", "--reverse", "--parents", "@{u}..HEAD"], notes).splitlines()
    parents = [line.split() for line in commits]
    if not parents or any(len(parts) != 2 for parts in parents):
        return False
    migration, base = parents[0]
    if _git_command.output(["log", "-1", "--format=%s", migration], notes) != migration_message:
        return False
    delta = _git_command.output(["diff-tree", "--no-commit-id", "--name-status", "-r", migration], notes)
    if delta != f"A\t{filename}" or _git_command.output(["status", "--porcelain"], notes):
        return False
    if _git_command.run(["cat-file", "-e", f"@{{u}}:{filename}"], cwd=notes, check=False, capture_output=True).returncode:
        return False
    if _git_command.run(["merge-base", "--is-ancestor", base, "@{u}"], cwd=notes, check=False).returncode:
        return False
    args = (
        ["reset", "--merge", "@{u}"]
        if len(commits) == 1
        else ["-c", "submodule.recurse=false", "rebase", "--onto", "@{u}", migration]
    )
    _git_command.run(args, cwd=notes, check=True, capture_output=True)
    return True


PUSH_DEFERRED_MESSAGE = "commitは完了したが、別の未コミット差分があるため分岐の自動解消とpushを保留した"
"""履歴が分岐し、無関係な差分もある場合に、commitの完了とpushの保留を伝える文言。"""

PUSH_DEFERRED_NEXT_ACTION = (
    "`git -C {private_notes} status`で無関係な差分を確認し、commitするか取り除いてcleanにしてから"
    "`atk wi commit`を実行する。分岐の自動解消とpushを行う"
)
"""`PUSH_DEFERRED_MESSAGE`に続けて案内する次の操作。"""

_DivergenceRecovery = Callable[[pathlib.Path], bool]
"""ローカル側のcommitが冗長であることを呼び出し元の基準で判定する関数。"""


class _GitRunner(Protocol):
    def __call__(self, args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None: ...


def _run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
    """Gitコマンドを実行し、失敗時に例外を送出する。"""
    _git_command.run_quiet(args, cwd, forward_error_output=forward_error_output)


def _forward_error_output(error: subprocess.CalledProcessError) -> None:
    for output in (error.output, error.stderr):
        if isinstance(output, str) and output:
            print(output, file=sys.stderr, end="")


def _run_git_result(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return _git_command.run(args, cwd, check=False, capture_output=True, text=True)


class _SyncAttempt(threading.local):
    def __init__(self) -> None:
        self.push_deferred: set[pathlib.Path] = set()
        self.sync_attempted = False


_SYNC_ATTEMPT = _SyncAttempt()


def has_remote(private_notes: pathlib.Path) -> bool:
    """現在のbranchのupstreamを解決でき、remote同期を行えるか判定する。"""
    return _run_git_result(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], private_notes).returncode == 0


def _report_missing_upstream(private_notes: pathlib.Path) -> None:
    """remoteだけを設定した構成で、ローカル操作の後に同期を有効にする方法を示す。"""
    remotes = _run_git_result(["remote"], private_notes)
    if remotes.returncode == 0 and remotes.stdout.strip():
        _next_action.report(
            "注記: private-notesのupstreamが無いため、remoteと同期していない。ローカルの操作は継続する。",
            next_action=(
                f"`git -C {private_notes} branch --set-upstream-to=<remote>/<branch>`でupstreamを設定するか、"
                f"`git -C {private_notes} push -u <remote> <branch>`でbranchを送信してから同期する"
            ),
        )


def push_was_deferred(private_notes: pathlib.Path) -> bool:
    return private_notes.resolve() in _SYNC_ATTEMPT.push_deferred


def reset_sync_attempt() -> None:
    _SYNC_ATTEMPT.sync_attempted = False


def sync_attempted() -> bool:
    return _SYNC_ATTEMPT.sync_attempted


def _git_path(private_notes: pathlib.Path, name: str, *, result_runner: _GitResultRunner) -> pathlib.Path | None:
    result = result_runner(["rev-parse", "--git-path", name], private_notes)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    path = pathlib.Path(result.stdout.strip())
    if not path.is_absolute():
        path = private_notes / path
    return path


def is_rebase_in_progress(
    private_notes: pathlib.Path,
    *,
    result_runner: _GitResultRunner = _run_git_result,
) -> bool:
    if not (private_notes / ".git").exists():
        return False
    for name in ("rebase-merge", "rebase-apply"):
        try:
            path = _git_path(private_notes, name, result_runner=result_runner)
        except OSError, subprocess.SubprocessError:
            path = None
        if path is not None and path.exists():
            return True
    return False


def ensure_not_rebasing(private_notes: pathlib.Path) -> None:
    if is_rebase_in_progress(private_notes):
        raise RebaseInProgressError("rebase中のため新しい更新を開始できない", private_notes)


def pull(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner = _run_git,
    result_runner: _GitResultRunner = _run_git_result,
    redundant_divergence: _DivergenceRecovery | None = None,
) -> None:
    """remoteをfetchし、fast-forwardと安全な回復で明示したupstreamへ統合する。"""
    try:
        _pull_impl(
            private_notes,
            run_git=run_git,
            result_runner=result_runner,
            redundant_divergence=redundant_divergence,
        )
    except subprocess.CalledProcessError as error:
        if not is_reported(error):
            _report_sync_failure(private_notes, "pull", error)
            mark_reported(error)
        raise
    except subprocess.TimeoutExpired as error:
        _diagnostics.report_sync_timeout(private_notes, "pull", error)
        raise


def _pull_impl(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner,
    result_runner: _GitResultRunner,
    redundant_divergence: _DivergenceRecovery | None,
) -> None:
    assert_repo_lock_held(private_notes)
    ensure_not_rebasing(private_notes)
    if not has_remote(private_notes):
        _report_missing_upstream(private_notes)
        return
    if is_human_terminal():
        print(f"remoteから取得・統合して同期中: {private_notes}", file=sys.stderr, flush=True)
    run_git(["fetch"], private_notes)
    try:
        run_git(["merge", "--ff-only", "@{u}"], private_notes, forward_error_output=False)
        return
    except subprocess.CalledProcessError as error:
        merge_error = error

    try:
        diverged = _history_has_diverged(private_notes, run_git=run_git)
    except subprocess.CalledProcessError:
        _forward_error_output(merge_error)
        raise merge_error from None
    if not diverged:
        _forward_error_output(merge_error)
        raise merge_error
    if _recover_matching_tree_divergence(
        private_notes,
        run_git=run_git,
        result_runner=result_runner,
    ):
        return
    if _recover_redundant_divergence(
        private_notes,
        redundant_divergence=redundant_divergence,
        run_git=run_git,
        result_runner=result_runner,
    ):
        return
    if is_worktree_dirty(private_notes, result_runner=result_runner):
        _forward_error_output(merge_error)
        _diagnostics.report_divergence(private_notes, result_runner=result_runner, dirty=True)
        mark_reported(merge_error)
        raise merge_error
    try:
        run_git(["-c", "submodule.recurse=false", "rebase", "@{u}"], private_notes)
    except subprocess.CalledProcessError:
        _forward_error_output(merge_error)
        _diagnostics.report_rebase_failure(private_notes, result_runner=result_runner)
        mark_reported(merge_error)
        raise merge_error from None


def _is_ancestor(
    older: str,
    newer: str,
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner,
) -> bool:
    try:
        run_git(["merge-base", "--is-ancestor", older, newer], private_notes)
    except subprocess.CalledProcessError as error:
        if error.returncode == 1:
            return False
        raise
    return True


def _history_has_diverged(private_notes: pathlib.Path, *, run_git: _GitRunner) -> bool:
    local_is_ancestor = _is_ancestor("HEAD", "@{u}", private_notes, run_git=run_git)
    remote_is_ancestor = _is_ancestor("@{u}", "HEAD", private_notes, run_git=run_git)
    return not local_is_ancestor and not remote_is_ancestor


def is_worktree_dirty(
    private_notes: pathlib.Path,
    *,
    paths: Iterable[str] | None = None,
    result_runner: _GitResultRunner = _run_git_result,
) -> bool:
    args = ["status", "--porcelain"]
    if paths is not None:
        args.extend(("--", *paths))
    result = result_runner(args, private_notes)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, _git_command.command_line(args), result.stdout, result.stderr)
    return bool(result.stdout.strip())


def pending_commit_count(
    private_notes: pathlib.Path,
    *,
    result_runner: _GitResultRunner = _run_git_result,
) -> int | None:
    if not has_remote(private_notes):
        return 0
    result = result_runner(["rev-list", "--count", "@{u}..HEAD"], private_notes)
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _recover_redundant_divergence(
    private_notes: pathlib.Path,
    *,
    redundant_divergence: _DivergenceRecovery | None,
    run_git: _GitRunner,
    result_runner: _GitResultRunner,
) -> bool:
    """cleanかつ呼び出し元が冗長性を証明した分岐だけをupstreamへ揃える。"""
    if redundant_divergence is None or is_worktree_dirty(private_notes, result_runner=result_runner):
        return False
    if not redundant_divergence(private_notes):
        return False
    run_git(["-c", "submodule.recurse=false", "reset", "--keep", "@{u}"], private_notes)
    return True


def _recover_matching_tree_divergence(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner,
    result_runner: _GitResultRunner,
) -> bool:
    result = result_runner(["diff", "--quiet", "HEAD", "@{u}"], private_notes)
    if result.returncode != 0:
        return False
    run_git(["reset", "--soft", "@{u}"], private_notes)
    return True


def push_pending_commits(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner = _run_git,
    result_runner: _GitResultRunner = _run_git_result,
    redundant_divergence: _DivergenceRecovery | None = None,
) -> int | None:
    _SYNC_ATTEMPT.push_deferred.discard(private_notes.resolve())
    try:
        return _push_pending_commits_impl(
            private_notes,
            run_git=run_git,
            result_runner=result_runner,
            redundant_divergence=redundant_divergence,
        )
    except subprocess.CalledProcessError as error:
        if not is_reported(error):
            _report_sync_failure(private_notes, "push", error)
            mark_reported(error)
        raise
    except subprocess.TimeoutExpired as error:
        _diagnostics.report_sync_timeout(private_notes, "push", error)
        raise


def _push_pending_commits_impl(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner,
    result_runner: _GitResultRunner,
    redundant_divergence: _DivergenceRecovery | None,
) -> int | None:
    assert_repo_lock_held(private_notes)
    ensure_not_rebasing(private_notes)
    if not has_remote(private_notes):
        _report_missing_upstream(private_notes)
        return 0
    if is_human_terminal():
        print(f"remoteへ送信して同期中: {private_notes}", file=sys.stderr, flush=True)
    original_error: subprocess.CalledProcessError
    count = pending_commit_count(private_notes, result_runner=result_runner)
    _SYNC_ATTEMPT.sync_attempted = True
    try:
        run_git(["push"], private_notes, forward_error_output=False)
        return count
    except subprocess.CalledProcessError as error:
        original_error = error

    try:
        run_git(["fetch"], private_notes)
        local_is_ancestor = _is_ancestor("HEAD", "@{u}", private_notes, run_git=run_git)
        remote_is_ancestor = _is_ancestor("@{u}", "HEAD", private_notes, run_git=run_git)
    except subprocess.CalledProcessError:
        # upstream未設定・通信失敗・認証失敗など、履歴分岐を確定できない場合は
        # 最初のpush失敗を保持し、別の失敗を原因として見せない。
        _forward_error_output(original_error)
        raise original_error from None

    if local_is_ancestor and not remote_is_ancestor:
        run_git(["merge", "--ff-only", "@{u}"], private_notes)
        run_git(["push"], private_notes)
        return 0
    if remote_is_ancestor or (local_is_ancestor and remote_is_ancestor):
        _forward_error_output(original_error)
        raise original_error

    if _recover_matching_tree_divergence(
        private_notes,
        run_git=run_git,
        result_runner=result_runner,
    ):
        return 0

    if is_worktree_dirty(private_notes, result_runner=result_runner):
        _forward_error_output(original_error)
        count = pending_commit_count(private_notes, result_runner=result_runner)
        count_text = "取得できない" if count is None else f"{count}件"
        _outcome.report_warning(
            f"{PUSH_DEFERRED_MESSAGE}。未push commit: {count_text}\n"
            + _diagnostics.divergence_details(private_notes, result_runner=result_runner),
            next_action=PUSH_DEFERRED_NEXT_ACTION.format(private_notes=private_notes),
        )
        _SYNC_ATTEMPT.push_deferred.add(private_notes.resolve())
        return 0

    if _recover_redundant_divergence(
        private_notes,
        redundant_divergence=redundant_divergence,
        run_git=run_git,
        result_runner=result_runner,
    ):
        return 0

    try:
        run_git(["-c", "submodule.recurse=false", "rebase", "@{u}"], private_notes)
    except subprocess.CalledProcessError as error:
        _diagnostics.report_rebase_failure(private_notes, result_runner=result_runner)
        mark_reported(error)
        raise
    count = pending_commit_count(private_notes, result_runner=result_runner)
    run_git(["push"], private_notes)
    return count


def _target_has_staged_changes(
    private_notes: pathlib.Path,
    paths: list[str],
    *,
    result_runner: _GitResultRunner,
) -> bool | None:
    if not (private_notes / ".git").exists():
        return None
    try:
        result = result_runner(["diff", "--cached", "--quiet", "--", *paths], private_notes)
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    if result.returncode == 128:
        return None
    raise subprocess.CalledProcessError(
        result.returncode,
        _git_command.command_line(["diff", "--cached", "--quiet", "--", *paths]),
        result.stdout,
        result.stderr,
    )


def _usable_pathspecs(
    private_notes: pathlib.Path,
    paths: list[str],
    *,
    result_runner: _GitResultRunner,
) -> list[str]:
    if not (private_notes / ".git").exists():
        return paths
    usable: list[str] = []
    for relative in paths:
        candidate = private_notes / relative
        if candidate.exists():
            if candidate.is_dir() and not any(candidate.iterdir()):
                result = result_runner(["ls-files", "--", relative], private_notes)
                if result.returncode != 0 or not result.stdout.strip():
                    continue
            usable.append(relative)
            continue
        result = result_runner(["ls-files", "--error-unmatch", "--", relative], private_notes)
        if result.returncode == 0:
            usable.append(relative)
        elif result.returncode != 1:
            raise subprocess.CalledProcessError(
                result.returncode,
                _git_command.command_line(["ls-files", "--error-unmatch", "--", relative]),
                result.stdout,
                result.stderr,
            )
    return usable


def commit_and_push(
    private_notes: pathlib.Path,
    message: str,
    rel_paths: Iterable[str],
    *,
    skip_push: bool = False,
    run_git: _GitRunner = _run_git,
    result_runner: _GitResultRunner = _run_git_result,
    push_pending_fn: Callable[[pathlib.Path], int | None] | None = None,
) -> int | None:
    assert_repo_lock_held(private_notes)
    ensure_not_rebasing(private_notes)
    paths = list(dict.fromkeys(rel_paths))
    push_fn = push_pending_fn or (lambda path: push_pending_commits(path, run_git=run_git, result_runner=result_runner))
    if not paths:
        if not skip_push:
            return push_fn(private_notes)
        return 0
    stage_paths = _usable_pathspecs(private_notes, paths, result_runner=result_runner)
    if not stage_paths:
        if not skip_push:
            return push_fn(private_notes)
        return 0
    run_git(["add", "--all", "--", *stage_paths], private_notes)
    staged = _target_has_staged_changes(private_notes, stage_paths, result_runner=result_runner)
    if staged is False:
        if not skip_push:
            return push_fn(private_notes)
        return 0
    run_git(["commit", "-m", message, "--", *stage_paths], private_notes)
    _SYNC_ATTEMPT.sync_attempted = True
    if skip_push:
        return 0
    return push_fn(private_notes)


def require_upstream(
    private_notes: pathlib.Path,
    *,
    result_runner: _GitResultRunner = _run_git_result,
) -> str:
    result = result_runner(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        private_notes,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise GitSyncError(
            "upstreamを解決できないため移行を開始できません",
            next_action=(f"`git -C {private_notes} branch --set-upstream-to=<remote>/<branch>`で上流を設定して再実行する"),
        )
    return result.stdout.strip()


def remote_contains_head(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner = _run_git,
) -> bool:
    return _is_ancestor("HEAD", "@{u}", private_notes, run_git=run_git)


_git_common_dir = git_common_dir
_repo_lock_path = repo_lock_path
_repo_lock = repo_lock
_assert_repo_lock_held = assert_repo_lock_held
_has_remote = has_remote
_is_rebase_in_progress = is_rebase_in_progress
_ensure_not_rebasing = ensure_not_rebasing
_pull = pull
_push_pending_commits = push_pending_commits
_commit_and_push = commit_and_push
