"""private-notesを共有する`atk`コマンドのGit同期基盤。

MQ固有のfrontmatterや状態遷移を持たず、同じGit作業コピーを更新する処理に共通する
ロック、対象限定commit、remote同期、push再試行だけを提供する。呼び出し元は
`repo_lock()`保持下でファイル変更とGit操作を行う。

private-notesへcommitする操作は、副作用を確定した後も未pushのcommitが残る場合に、
呼び出し元のCLIが専用の終了コードで同期未達を通知する。同期の保留は
`push_pending_commits`の内部分岐で決まり、各サブコマンドからは観測できないため、
通知の判定材料を本モジュールが提供する。
pullとpushの失敗も呼び出し元の実行主体が次の操作を決められる必要があるため、
失敗理由を示すgitの出力に続けて、本モジュールが確認コマンドと再実行の手順を出力する。
前提検査と失敗時の復元は、当該操作が書き込む対象パスへ限定する。共有Git作業コピーへ
複数の実行主体が並行して書き込むため、repo全体を対象とする前提と復元は他の主体の成果を
壊すか、当該操作を恒常的に成立させなくする。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from typing import Protocol, cast

import _git_command
import filelock
import platformdirs

LOCAL_ONLY_MARKER = ".agent-toolkit-local-only"
"""自動生成されたremoteなしリポジトリを示すマーカー。"""

PUSH_DEFERRED_MESSAGE = (
    "commitは完了したが、別の未コミット差分があるため分岐の自動解消とpushを保留した。\n"
    "`git status`で差分を確認してcommit等でcleanにした後、元の`atk`操作を再実行してください。"
)
"""履歴分岐時に無関係な差分がある場合の確定通知。"""

_DivergenceRecovery = Callable[[pathlib.Path], bool]
"""呼び出し元の意味論でローカル側commitの冗長性を証明する判定関数。"""


class _GitRunner(Protocol):
    """終了時に例外を送出するGit実行関数の型。"""

    def __call__(self, args: list[str], cwd: pathlib.Path) -> None: ...


class _GitResultRunner(Protocol):
    """終了コードを保持するGit実行関数の型。"""

    def __call__(self, args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]: ...


class RebaseInProgressError(RuntimeError):
    """rebase中の作業コピーへ新しいmutationを開始しようとした。"""


class GitSyncError(RuntimeError):
    """Git同期の前提を満たせない。"""


def _run_git(args: list[str], cwd: pathlib.Path) -> None:
    """Gitコマンドを実行し、失敗時に例外を送出する。"""
    _git_command.run(args, cwd, check=True)


def _run_git_result(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """Gitコマンドを終了コード付きで実行する。"""
    result = _git_command.run(args, cwd, check=False, capture_output=True, text=True)
    return cast(subprocess.CompletedProcess[str], result)


class _ThreadLocalHeldPaths(threading.local):
    """現在のスレッドが保持しているGit common directoryを記録する。"""

    def __init__(self) -> None:
        self.paths: dict[pathlib.Path, int] = {}


_LOCK_HELD_PATHS = _ThreadLocalHeldPaths()


def git_common_dir(repo_path: pathlib.Path) -> pathlib.Path:
    """作業コピーが共有するGit common directoryを返す。

    `git rev-parse`を実行できないテスト用パスや初期化前のパスでは、対象パス自身を
    fallbackとして用いる。通常の作業コピーでは、複数worktreeが同じロックを共有する。
    """
    repo_path = repo_path.resolve()
    if not (repo_path / ".git").exists():
        return repo_path
    try:
        result = _run_git_result(["rev-parse", "--git-common-dir"], repo_path)
    except (OSError, subprocess.SubprocessError):
        return repo_path
    if result.returncode != 0 or not result.stdout.strip():
        return repo_path
    common = pathlib.Path(result.stdout.strip())
    if not common.is_absolute():
        common = repo_path / common
    try:
        return common.resolve()
    except OSError:
        return common.absolute()


def _lock_key(repo_path: pathlib.Path) -> pathlib.Path:
    """ロック記録用のcommon directoryを返す。"""
    return git_common_dir(repo_path)


def repo_lock_path(repo_path: pathlib.Path) -> pathlib.Path:
    """Git common directoryへ対応するロックファイルの絶対パスを返す。"""
    common = str(_lock_key(repo_path))
    digest = hashlib.sha1(common.encode("utf-8"), usedforsecurity=False).hexdigest()
    lock_dir = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{digest}.lock"


class _RepoLock(filelock.FileLock):
    """Git common directory単位で保持記録を管理するFileLock。"""

    def __init__(self, repo_path: pathlib.Path, *, timeout: float = -1) -> None:
        self._target = _lock_key(repo_path)
        super().__init__(str(repo_lock_path(repo_path)), timeout=timeout)

    def acquire(
        self,
        timeout: float | None = None,
        poll_interval: float | None = None,
        *,
        poll_intervall: float | None = None,
        blocking: bool | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> filelock.AcquireReturnProxy:
        result = super().acquire(
            timeout,
            poll_interval,
            poll_intervall=poll_intervall,
            blocking=blocking,
            cancel_check=cancel_check,
        )
        _LOCK_HELD_PATHS.paths[self._target] = _LOCK_HELD_PATHS.paths.get(self._target, 0) + 1
        return result

    def release(self, force: bool = False) -> None:
        super().release(force)
        if not self.is_locked:
            _LOCK_HELD_PATHS.paths.pop(self._target, None)


def repo_lock(repo_path: pathlib.Path, *, timeout: float = -1) -> filelock.FileLock:
    """指定作業コピーへ対応するGit共通ロックを返す。"""
    return _RepoLock(repo_path, timeout=timeout)


def assert_repo_lock_held(repo_path: pathlib.Path) -> None:
    """現在のスレッドが対象common directoryのロックを保持していることを検証する。"""
    if _LOCK_HELD_PATHS.paths.get(_lock_key(repo_path), 0) <= 0:
        raise RuntimeError(
            "不変条件違反: private_notesへのgit操作・ファイル変更は_repo_lock保持下でのみ実行できる。"
            "呼び出し元でwith _repo_lock(private_notes):を用いること。"
        )


def has_remote(private_notes: pathlib.Path) -> bool:
    """remote同期を行う通常リポジトリか判定する。"""
    return not (private_notes / LOCAL_ONLY_MARKER).exists()


def _git_path(private_notes: pathlib.Path, name: str, *, result_runner: _GitResultRunner) -> pathlib.Path | None:
    """Git管理下の特殊パスを解決する。"""
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
    """Git common directoryにrebase中間状態が存在するか判定する。"""
    if not (private_notes / ".git").exists():
        return False
    for name in ("rebase-merge", "rebase-apply"):
        try:
            path = _git_path(private_notes, name, result_runner=result_runner)
        except (OSError, subprocess.SubprocessError):
            path = None
        if path is not None and path.exists():
            return True
    return False


def ensure_not_rebasing(private_notes: pathlib.Path) -> None:
    """新しいmutationを受け付けられる状態か検証する。"""
    if is_rebase_in_progress(private_notes):
        raise RebaseInProgressError(
            "rebase中のため新しい更新を開始できません。現在の競合を解消して"
            "`git add <path>`、`git rebase --continue`、`git push`を実行するか、"
            "不要であれば`git rebase --abort`を実行してください。"
        )


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
    except subprocess.CalledProcessError:
        _report_sync_failure(private_notes, "pull")
        raise


def _pull_impl(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner,
    result_runner: _GitResultRunner,
    redundant_divergence: _DivergenceRecovery | None,
) -> None:
    """pullのGit操作本体を実行する。"""
    assert_repo_lock_held(private_notes)
    ensure_not_rebasing(private_notes)
    if not has_remote(private_notes):
        return
    run_git(["fetch"], private_notes)
    try:
        run_git(["merge", "--ff-only", "@{u}"], private_notes)
        return
    except subprocess.CalledProcessError as error:
        merge_error = error

    try:
        diverged = _history_has_diverged(private_notes, run_git=run_git)
    except subprocess.CalledProcessError:
        raise merge_error from None
    if not diverged:
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
        _report_divergence(private_notes, result_runner=result_runner)
        raise merge_error
    try:
        run_git(["rebase", "@{u}"], private_notes)
    except subprocess.CalledProcessError:
        _report_rebase_failure(private_notes, result_runner=result_runner)
        raise merge_error from None


def _is_ancestor(
    older: str,
    newer: str,
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner,
) -> bool:
    """Gitの終了コードで祖先関係を判定する。"""
    try:
        run_git(["merge-base", "--is-ancestor", older, newer], private_notes)
    except subprocess.CalledProcessError as error:
        if error.returncode == 1:
            return False
        raise
    return True


def _history_has_diverged(private_notes: pathlib.Path, *, run_git: _GitRunner) -> bool:
    """HEADとupstreamの双方に相手へ含まれないcommitがあるか返す。"""
    local_is_ancestor = _is_ancestor("HEAD", "@{u}", private_notes, run_git=run_git)
    remote_is_ancestor = _is_ancestor("@{u}", "HEAD", private_notes, run_git=run_git)
    return not local_is_ancestor and not remote_is_ancestor


def is_worktree_dirty(
    private_notes: pathlib.Path,
    *,
    paths: Iterable[str] | None = None,
    result_runner: _GitResultRunner = _run_git_result,
) -> bool:
    """index・worktree・未追跡ファイルを含む変更の有無を返す。"""
    args = ["status", "--porcelain"]
    if paths is not None:
        args.extend(("--", *paths))
    result = result_runner(args, private_notes)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, ["git", *args], result.stdout, result.stderr)
    return bool(result.stdout.strip())


def pending_commit_count(
    private_notes: pathlib.Path,
    *,
    result_runner: _GitResultRunner = _run_git_result,
) -> int | None:
    """upstreamへ未送信のローカルcommit数を返す。"""
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
    run_git(["reset", "--keep", "@{u}"], private_notes)
    print(
        "同等の変更がupstreamへ反映済みであることを確認したため、冗長なローカルcommitを除外して自動同期しました。",
        file=sys.stderr,
    )
    return True


def _recover_matching_tree_divergence(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner,
    result_runner: _GitResultRunner,
) -> bool:
    """HEADとupstreamの木が一致する分岐をindexを保ったまま解消する。"""
    result = result_runner(["diff", "--quiet", "HEAD", "@{u}"], private_notes)
    if result.returncode != 0:
        return False
    run_git(["reset", "--soft", "@{u}"], private_notes)
    print(
        "HEADとupstreamの内容が一致するため、冗長なローカルcommitを除外して自動同期しました。",
        file=sys.stderr,
    )
    return True


def _report_divergence(
    private_notes: pathlib.Path,
    *,
    result_runner: _GitResultRunner,
) -> None:
    """自動回復できない分岐の原因と手動回復手順を表示する。"""
    count_text = "取得できませんでした"
    try:
        counts = result_runner(["rev-list", "--left-right", "--count", "HEAD...@{u}"], private_notes)
        if counts.returncode == 0:
            fields = counts.stdout.split()
            if len(fields) == 2:
                count_text = f"ローカルのみ{fields[0]}件、upstreamのみ{fields[1]}件"
    except (OSError, subprocess.SubprocessError):
        pass
    print(f"Git履歴が分岐しています（{count_text}）: {private_notes}", file=sys.stderr)
    try:
        differences = result_runner(["diff", "--name-status", "HEAD", "@{u}"], private_notes)
        if differences.returncode == 0 and differences.stdout.strip():
            print("内容差のあるファイル:", file=sys.stderr)
            print(differences.stdout.rstrip(), file=sys.stderr)
    except (OSError, subprocess.SubprocessError):
        pass
    print(
        "ローカルの未push commitを残した間に、別のcloneからupstreamが更新された状態です。",
        file=sys.stderr,
    )
    print("確認: `git log --left-right --oneline HEAD...@{u}`", file=sys.stderr)
    print(
        "回復: `git rebase @{u}`を実行し、競合を解消して`git add <path>`、"
        "`git rebase --continue`、`git push`の順に実行してください。",
        file=sys.stderr,
    )
    print(
        "同じ変更がupstreamに存在する重複commitなら、内容を確認して`git rebase --skip`を実行できます。"
        "中止する場合は`git rebase --abort`を実行してください。",
        file=sys.stderr,
    )


def _report_rebase_failure(private_notes: pathlib.Path, *, result_runner: _GitResultRunner) -> None:
    """rebase失敗時に状態保持と復旧手順を表示する。"""
    try:
        conflicts = result_runner(
            ["diff", "--name-only", "--diff-filter=U"],
            private_notes,
        )
        names = [line for line in conflicts.stdout.splitlines() if line]
    except (OSError, subprocess.SubprocessError):
        names = []
    print("rebaseに失敗したため、rebase状態を保持しています。自動abortは行っていません。", file=sys.stderr)
    print(
        "競合ファイル: " + ("、".join(names) if names else "取得できませんでした"),
        file=sys.stderr,
    )
    print("競合を解消した後、次の順に実行してください: `git add <競合解消済みパス>`、", file=sys.stderr)
    print("`git rebase --continue`、`git push`。", file=sys.stderr)
    print(
        "同じ変更がupstreamへ反映済みの重複commitなら、内容を確認して`git rebase --skip`を実行できます。"
        "中止する場合は`git rebase --abort`を実行してください。",
        file=sys.stderr,
    )


def _report_sync_failure(private_notes: pathlib.Path, operation: str) -> None:
    """pull又はpush失敗後に確認と再実行の手順を表示する。"""
    resolved = private_notes.resolve()
    print(f"private-notesの{operation}に失敗しました: {resolved}", file=sys.stderr)
    print(
        "直前のgitの出力が失敗理由です。認証、ネットワーク接続、remoteの状態のいずれかを解消してください。",
        file=sys.stderr,
    )
    print(f"確認: `git -C {resolved} status`", file=sys.stderr)
    print("解消後、失敗した`atk`操作を再実行すると同期を完了できます。", file=sys.stderr)


def push_pending_commits(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner = _run_git,
    result_runner: _GitResultRunner = _run_git_result,
    redundant_divergence: _DivergenceRecovery | None = None,
) -> None:
    """branch上の未push commitを送信し、履歴分岐だけをrebaseして再送する。"""
    try:
        _push_pending_commits_impl(
            private_notes,
            run_git=run_git,
            result_runner=result_runner,
            redundant_divergence=redundant_divergence,
        )
    except subprocess.CalledProcessError:
        _report_sync_failure(private_notes, "push")
        raise


def _push_pending_commits_impl(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner,
    result_runner: _GitResultRunner,
    redundant_divergence: _DivergenceRecovery | None,
) -> None:
    """pushのGit操作本体を実行する。"""
    assert_repo_lock_held(private_notes)
    ensure_not_rebasing(private_notes)
    if not has_remote(private_notes):
        return
    original_error: subprocess.CalledProcessError
    try:
        run_git(["push"], private_notes)
        return
    except subprocess.CalledProcessError as error:
        original_error = error

    try:
        run_git(["fetch"], private_notes)
        local_is_ancestor = _is_ancestor("HEAD", "@{u}", private_notes, run_git=run_git)
        remote_is_ancestor = _is_ancestor("@{u}", "HEAD", private_notes, run_git=run_git)
    except subprocess.CalledProcessError:
        # upstream未設定・通信失敗・認証失敗など、履歴分岐を確定できない場合は
        # 最初のpush失敗を保持し、別の失敗を原因として見せない。
        raise original_error from None

    if local_is_ancestor and not remote_is_ancestor:
        run_git(["merge", "--ff-only", "@{u}"], private_notes)
        run_git(["push"], private_notes)
        return
    if remote_is_ancestor or (local_is_ancestor and remote_is_ancestor):
        raise original_error

    if _recover_matching_tree_divergence(
        private_notes,
        run_git=run_git,
        result_runner=result_runner,
    ):
        return

    if is_worktree_dirty(private_notes, result_runner=result_runner):
        _report_divergence(private_notes, result_runner=result_runner)
        print(PUSH_DEFERRED_MESSAGE, file=sys.stderr)
        return

    if _recover_redundant_divergence(
        private_notes,
        redundant_divergence=redundant_divergence,
        run_git=run_git,
        result_runner=result_runner,
    ):
        return

    try:
        run_git(["rebase", "@{u}"], private_notes)
    except subprocess.CalledProcessError:
        _report_rebase_failure(private_notes, result_runner=result_runner)
        raise
    run_git(["push"], private_notes)


def _target_has_staged_changes(
    private_notes: pathlib.Path,
    paths: list[str],
    *,
    result_runner: _GitResultRunner,
) -> bool | None:
    """対象pathだけのstage差分を終了コードで判定する。

    Gitリポジトリでないテスト用fake runnerでは`None`を返し、従来どおりcommitを試す。
    実Gitの予期しない終了コードは例外として扱う。
    """
    if not (private_notes / ".git").exists():
        return None
    try:
        result = result_runner(["diff", "--cached", "--quiet", "--", *paths], private_notes)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    if result.returncode == 128:
        return None
    raise subprocess.CalledProcessError(
        result.returncode,
        ["git", "diff", "--cached", "--quiet", "--", *paths],
        result.stdout,
        result.stderr,
    )


def _usable_pathspecs(
    private_notes: pathlib.Path,
    paths: list[str],
    *,
    result_runner: _GitResultRunner,
) -> list[str]:
    """実在または追跡済み削除対象に限定したpathspecを返す。"""
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
                ["git", "ls-files", "--error-unmatch", "--", relative],
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
    push_pending_fn: Callable[[pathlib.Path], None] | None = None,
) -> None:
    """許可pathだけをstage・commitし、branch上のpending commitをpushする。"""
    assert_repo_lock_held(private_notes)
    ensure_not_rebasing(private_notes)
    paths = list(dict.fromkeys(rel_paths))
    push_fn = push_pending_fn or (lambda path: push_pending_commits(path, run_git=run_git, result_runner=result_runner))
    if not paths:
        if not skip_push:
            push_fn(private_notes)
        return
    stage_paths = _usable_pathspecs(private_notes, paths, result_runner=result_runner)
    if not stage_paths:
        if not skip_push:
            push_fn(private_notes)
        return
    run_git(["add", "--all", "--", *stage_paths], private_notes)
    staged = _target_has_staged_changes(private_notes, stage_paths, result_runner=result_runner)
    if staged is False:
        if not skip_push:
            push_fn(private_notes)
        return
    run_git(["commit", "-m", message, "--", *stage_paths], private_notes)
    if skip_push:
        if has_remote(private_notes):
            print(
                "注記: --skip-pushにより未pushのcommitをローカルへ残し、pushを省略しました。"
                "最後の操作は--skip-pushなしで実行するか、atk wi commitを実行して滞留commitをpushしてください。",
                file=sys.stderr,
            )
        return
    push_fn(private_notes)


def require_upstream(
    private_notes: pathlib.Path,
    *,
    result_runner: _GitResultRunner = _run_git_result,
) -> str:
    """upstreamが解決できることを検証し、表示用の参照名を返す。"""
    result = result_runner(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        private_notes,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise GitSyncError("upstreamを解決できないため移行を開始できません")
    return result.stdout.strip()


def remote_contains_head(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner = _run_git,
) -> bool:
    """upstreamが現在のHEADを含むか終了コードで検証する。"""
    return _is_ancestor("HEAD", "@{u}", private_notes, run_git=run_git)


# 既存のプライベート呼び出し元が段階的に移行できるよう、旧命名も公開する。
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
