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

import contextlib
import contextvars
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from typing import Protocol

import filelock
import psutil

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.environment import is_human_terminal
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._git import command as _git_command

PUSH_DEFERRED_MESSAGE = "commitは完了したが、別の未コミット差分があるため分岐の自動解消とpushを保留した"
"""履歴が分岐し、無関係な差分もある場合に、commitの完了とpushの保留を伝える文言。"""

PUSH_DEFERRED_NEXT_ACTION = (
    "`git -C {private_notes} status`で無関係な差分を確認し、commitするか取り除いてcleanにしてから"
    "`atk wi commit`を実行する。分岐の自動解消とpushを行う"
)
"""`PUSH_DEFERRED_MESSAGE`に続けて案内する次の操作。"""

_REPORTED_ATTRIBUTE = "_atk_sync_failure_reported"
"""失敗の原因と次の操作を本モジュールが出力済みであることを例外へ記録する属性名。"""

LOCK_HOLD_TIMEOUT_SECONDS = 120.0
"""`repo_lock`を保持している区間のgit操作全体の時間上限。

1つの保持区間ではfetch・merge・commit・pushと、push拒否時の再試行まで続くため、操作ごとの上限だけでは
保持が数分に及ぶ。区間全体を有限にすると、待つ側の上限を「正常な保持者が解放するまで」の値に決められる。
"""

LOCK_WAIT_TIMEOUT_SECONDS = 150.0
"""`repo_lock`の待機上限を省略した場合の値。保持区間の上限より長くし、正常に動いている保持者の解放まで待てるようにする。"""

GIT_TIMEOUT_NEXT_ACTION = "リモートへの接続（ネットワーク、VPN、認証）を確かめてから、失敗した操作を再実行する"
"""gitの時間切れの失敗行に続けて案内する次の操作。"""

_DivergenceRecovery = Callable[[pathlib.Path], bool]
"""ローカル側のcommitが冗長であることを呼び出し元の基準で判定する関数。"""


class _GitRunner(Protocol):
    """終了時に例外を送出するGit実行関数の型。"""

    def __call__(self, args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None: ...


class _GitResultRunner(Protocol):
    """終了コードを保持するGit実行関数の型。"""

    def __call__(self, args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]: ...


class RebaseInProgressError(RuntimeError):
    """rebase中の作業コピーで新しい更新を開始しようとした。

    `str()`は理由だけを返す。次の操作は原因が1つに決まるため`next_action`へ固定する。
    """

    next_action = (
        "現在の競合を解消して`git add <path>`、`git -c submodule.recurse=false rebase --continue`、`git push`を実行するか、"
        "不要であれば`git -c submodule.recurse=false rebase --abort`を実行してから、元の`atk`操作を再実行する"
    )


class GitSyncError(RuntimeError):
    """Git同期の前提を満たせない。`str()`は理由だけを返し、`next_action`に次の操作を持つ。"""

    def __init__(self, reason: str, *, next_action: str) -> None:
        _next_action.next_action_line(next_action)
        super().__init__(reason)
        self.reason = reason
        self.next_action = next_action


class RepoLockTimeoutError(filelock.Timeout):
    """`repo_lock`を待機上限まで取得できなかった。`reason`に待った対象と保持者、`next_action`に次の操作を持つ。"""

    next_action = (
        "保持者のプロセスが動いているか確かめ、終了を待つか、応答しない場合は停止してから再実行する。"
        "保持者が存在しない場合はそのまま再実行する"
    )

    def __init__(self, lock_file: str, *, waited: float, holder: str) -> None:
        super().__init__(lock_file)
        self.waited = waited
        self.holder = holder

    @property
    def reason(self) -> str:
        """失敗行へ載せる理由を返す。"""
        return f"共有ロックを{self.waited:.0f}秒待ったが取得できなかった: {self.lock_file}。保持者: {self.holder}"


def git_timeout_reason(error: subprocess.TimeoutExpired) -> str:
    """gitの時間切れについて、打ち切ったコマンド、上限秒数、作業ディレクトリを示す理由を返す。"""
    command = " ".join(error.cmd) if isinstance(error.cmd, list | tuple) else str(error.cmd)
    return (
        f"gitが時間上限の{error.timeout:.0f}秒に達したため、子プロセスまで止めて打ち切った: `{command}`"
        f"（作業ディレクトリ: {_git_command.timeout_cwd(error)}）"
    )


def mark_reported(error: subprocess.SubprocessError) -> None:
    """失敗の原因と次の操作を出力済みであることを例外へ記録する。"""
    setattr(error, _REPORTED_ATTRIBUTE, True)


def is_reported(error: subprocess.SubprocessError) -> bool:
    """本モジュールが失敗の原因と次の操作を出力済みの例外か返す。

    呼び出し元のCLIは出力済みの失敗へ汎用の失敗行を重ねず、原因別の失敗行を1本に保つ。
    """
    return bool(getattr(error, _REPORTED_ATTRIBUTE, False))


def _run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
    """Gitコマンドを実行し、失敗時に例外を送出する。"""
    _git_command.run_quiet(args, cwd, forward_error_output=forward_error_output)


def _forward_error_output(error: subprocess.CalledProcessError) -> None:
    """保持したGitの標準出力と標準エラーを呼び出し側の標準エラーへ転送する。"""
    for output in (error.output, error.stderr):
        if isinstance(output, str) and output:
            print(output, file=sys.stderr, end="")


def _run_git_result(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """Gitコマンドを終了コード付きで実行する。"""
    return _git_command.run(args, cwd, check=False, capture_output=True, text=True)


class _ThreadLocalHeldPaths(threading.local):
    """現在のスレッドが保持しているGit common directoryを記録する。"""

    def __init__(self) -> None:
        self.paths: dict[pathlib.Path, int] = {}
        self.push_deferred: set[pathlib.Path] = set()
        self.sync_attempted = False


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
    except OSError, subprocess.SubprocessError:
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
    lock_dir = _state_paths.lock_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{digest}.lock"


class LockObserver(Protocol):
    """`repo_lock`の待機と保持の秒数を受け取る観測先。"""

    def add_lock_wait(self, seconds: float) -> None: ...

    def add_lock_hold(self, seconds: float) -> None: ...


LOCK_OBSERVER: contextvars.ContextVar[LockObserver | None] = contextvars.ContextVar("atk_repo_lock_observer", default=None)
"""`repo_lock`の待機・保持を記録する観測先。`atk serve`が要求ごとに設定し、設定の無い呼出しでは計測しない。"""


def _holder_path(lock_file: str) -> pathlib.Path:
    """ロックの保持者を記録するファイルのパスを返す。

    ロックファイル自体へは書かない。保持中のファイルを他のプロセスが読めるかはOSで異なるためである。
    """
    return pathlib.Path(lock_file + ".holder")


def _write_holder(lock_file: str) -> None:
    """待つ側が原因を示せるよう、ロックを取得したプロセスのPID・起動時刻・コマンドラインを記録する。"""
    try:
        process = psutil.Process()
        record = {
            "pid": process.pid,
            "started_at": datetime.datetime.fromtimestamp(process.create_time()).astimezone().isoformat(timespec="seconds"),
            "command": " ".join(process.cmdline()),
        }
        _holder_path(lock_file).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    except OSError, psutil.Error:
        # 記録は診断の補助であり、書けないことでロックの取得を失敗させない。
        pass


def _describe_holder(lock_file: str) -> str:
    """保持者の記録から、失敗行へ載せる保持者の説明を返す。"""
    try:
        record = json.loads(_holder_path(lock_file).read_text(encoding="utf-8"))
        pid = int(record["pid"])
        description = f"PID {pid}、起動 {record['started_at']}、コマンド `{record['command']}`"
    except OSError, ValueError, KeyError, TypeError:
        return "記録が無いため特定できない"
    if not psutil.pid_exists(pid):
        return f"{description}（このPIDのプロセスは存在しない）"
    return description


class _RepoLock(filelock.FileLock):
    """Git common directory単位で保持記録を管理するFileLock。"""

    def __init__(self, repo_path: pathlib.Path, *, timeout: float) -> None:
        self._repo_path = repo_path
        self._target = _lock_key(repo_path)
        self._observer: LockObserver | None = None
        self._acquired_at: float | None = None
        self._hold_scope: contextlib.ExitStack | None = None
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
        result: filelock.AcquireReturnProxy | None = None
        observer = LOCK_OBSERVER.get()
        wait_started = time.perf_counter()
        if blocking is not False and is_human_terminal():
            # 他の主体が保持していて待つ場合だけ、待機中であることを人の端末へ届ける。
            try:
                result = super().acquire(
                    0, poll_interval, poll_intervall=poll_intervall, blocking=False, cancel_check=cancel_check
                )
            except filelock.Timeout:
                print(f"共有ロックの取得を待機中: {self._repo_path}", file=sys.stderr, flush=True)
        if result is None:
            try:
                result = super().acquire(
                    timeout,
                    poll_interval,
                    poll_intervall=poll_intervall,
                    blocking=blocking,
                    cancel_check=cancel_check,
                )
            except filelock.Timeout:
                # 取得できずに終わった待ちも、要求の所要のうちロック待ちが占めた区間として記録する。
                waited = time.perf_counter() - wait_started
                if observer is not None and self._acquired_at is None:
                    observer.add_lock_wait(waited)
                raise RepoLockTimeoutError(self.lock_file, waited=waited, holder=_describe_holder(self.lock_file)) from None
        _LOCK_HELD_PATHS.paths[self._target] = _LOCK_HELD_PATHS.paths.get(self._target, 0) + 1
        if self.lock_counter == 1:
            _write_holder(self.lock_file)
            # 呼び出し側ごとの指定に頼らず、保持区間のgit操作全体を有限にする。
            self._hold_scope = contextlib.ExitStack()
            self._hold_scope.enter_context(_git_command.timeout_scope(LOCK_HOLD_TIMEOUT_SECONDS))
        if observer is not None and self._acquired_at is None:
            acquired_at = time.perf_counter()
            observer.add_lock_wait(acquired_at - wait_started)
            self._observer = observer
            self._acquired_at = acquired_at
        return result

    def release(self, force: bool = False) -> None:
        if self.is_locked and (force or self.lock_counter == 1):
            # 保持者の記録は、次の取得者の記録を消さないようロックの解放より前に消す。
            with contextlib.suppress(OSError):
                _holder_path(self.lock_file).unlink(missing_ok=True)
            if self._hold_scope is not None:
                # 取得と別の文脈で解放された場合、期限の復元は行えないが、その文脈の終了とともに期限も消える。
                with contextlib.suppress(ValueError):
                    self._hold_scope.close()
                self._hold_scope = None
        super().release(force)
        if not self.is_locked:
            _LOCK_HELD_PATHS.paths.pop(self._target, None)
            if self._observer is not None and self._acquired_at is not None:
                self._observer.add_lock_hold(time.perf_counter() - self._acquired_at)
            self._observer = None
            self._acquired_at = None


def repo_lock(repo_path: pathlib.Path, *, timeout: float | None = None) -> filelock.FileLock:
    """指定作業コピーへ対応するGit共通ロックを返す。

    `timeout`を省略すると`LOCK_WAIT_TIMEOUT_SECONDS`まで待つ。
    待機上限に達すると、保持者を示す`RepoLockTimeoutError`（`filelock.Timeout`の派生）を送出する。
    保持している間のgit操作は、合計で`LOCK_HOLD_TIMEOUT_SECONDS`を上限とする。
    """
    # filelockは生成時の引数を検証するため、省略値はここで秒数へ解決する。
    return _RepoLock(repo_path, timeout=LOCK_WAIT_TIMEOUT_SECONDS if timeout is None else timeout)


def assert_repo_lock_held(repo_path: pathlib.Path) -> None:
    """現在のスレッドが対象common directoryのロックを保持していることを検証する。"""
    if _LOCK_HELD_PATHS.paths.get(_lock_key(repo_path), 0) <= 0:
        raise RuntimeError(
            "不変条件違反: private_notesへのgit操作・ファイル変更は_repo_lock保持下でのみ実行できる。"
            "呼び出し元でwith _repo_lock(private_notes):を用いること。"
        )


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
    """現在のスレッドの直近pushで保留を報告したか返す。"""
    return private_notes.resolve() in _LOCK_HELD_PATHS.push_deferred


def reset_sync_attempt() -> None:
    """現在のスレッドの、commitと送信の試行の記録を消す。CLIが1回の操作を始める前に呼ぶ。"""
    _LOCK_HELD_PATHS.sync_attempted = False


def sync_attempted() -> bool:
    """`reset_sync_attempt`の後に、現在のスレッドがcommitしたか未pushのcommitの送信を試みたかを返す。"""
    return _LOCK_HELD_PATHS.sync_attempted


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
        except OSError, subprocess.SubprocessError:
            path = None
        if path is not None and path.exists():
            return True
    return False


def ensure_not_rebasing(private_notes: pathlib.Path) -> None:
    """新しい更新を受け付けられる状態か検証する。"""
    if is_rebase_in_progress(private_notes):
        raise RebaseInProgressError("rebase中のため新しい更新を開始できない")


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
        _report_sync_timeout(private_notes, "pull", error)
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
        _report_divergence(private_notes, result_runner=result_runner, dirty=True)
        mark_reported(merge_error)
        raise merge_error
    try:
        run_git(["-c", "submodule.recurse=false", "rebase", "@{u}"], private_notes)
    except subprocess.CalledProcessError:
        _forward_error_output(merge_error)
        _report_rebase_failure(private_notes, result_runner=result_runner)
        mark_reported(merge_error)
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
        raise subprocess.CalledProcessError(result.returncode, _git_command.command_line(args), result.stdout, result.stderr)
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
    run_git(["-c", "submodule.recurse=false", "reset", "--keep", "@{u}"], private_notes)
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
    return True


def _divergence_details(
    private_notes: pathlib.Path,
    *,
    result_runner: _GitResultRunner,
) -> str:
    """分岐の件数と内容差を、出力先と成否を決めずに組み立てる。"""
    count_text = "取得できなかった"
    try:
        counts = result_runner(["rev-list", "--left-right", "--count", "HEAD...@{u}"], private_notes)
        if counts.returncode == 0:
            fields = counts.stdout.split()
            if len(fields) == 2:
                count_text = f"ローカルのみ{fields[0]}件、upstreamのみ{fields[1]}件"
    except OSError, subprocess.SubprocessError:
        pass
    reason_lines = [
        f"Git履歴が分岐している（{count_text}）: {private_notes}。"
        "ローカルの未push commitを残した間に、別のcloneからupstreamが更新された状態である",
    ]
    try:
        differences = result_runner(["diff", "--name-status", "HEAD", "@{u}"], private_notes)
        if differences.returncode == 0 and differences.stdout.strip():
            reason_lines.append("内容差のあるファイル:")
            reason_lines.append(differences.stdout.rstrip())
    except OSError, subprocess.SubprocessError:
        pass
    return "\n".join(reason_lines)


def _report_divergence(
    private_notes: pathlib.Path,
    *,
    result_runner: _GitResultRunner,
    dirty: bool = False,
) -> None:
    """分岐による失敗を、未コミット差分の解消順に合わせて案内する。"""
    preparation = (
        f"`git -C {private_notes} status`で無関係な差分を確認してcleanにし、元の操作を再実行する。"
        "cleanにしても自動解消できない場合は、"
        if dirty
        else ""
    )
    _outcome.report_failure(
        _divergence_details(private_notes, result_runner=result_runner),
        next_action=preparation
        + (
            "`git log --left-right --oneline HEAD...@{u}`で分岐を確認し、"
            "`git -c submodule.recurse=false rebase @{u}`を実行する。"
            "競合したら解消して`git add <path>`、`git -c submodule.recurse=false rebase --continue`、"
            "`git push`の順に実行する。\n"
            "同じ変更がupstreamに存在する重複commitなら、内容を確認して"
            "`git -c submodule.recurse=false rebase --skip`を実行できる。"
            "中止する場合は`git -c submodule.recurse=false rebase --abort`を実行する"
        ),
    )


def _report_rebase_failure(private_notes: pathlib.Path, *, result_runner: _GitResultRunner) -> None:
    """rebase失敗時に状態保持と復旧手順を表示する。"""
    try:
        conflicts = result_runner(
            ["diff", "--name-only", "--diff-filter=U", "-z"],
            private_notes,
        )
        names = [name for name in conflicts.stdout.split("\0") if name]
    except OSError, subprocess.SubprocessError:
        names = []
    _outcome.report_failure(
        "rebaseに失敗したため、rebase状態を保持した。自動abortは行っていない\n"
        "競合ファイル: " + ("、".join(names) if names else "取得できなかった"),
        next_action=(
            "競合を解消した後、`git add <競合解消済みパス>`、"
            "`git -c submodule.recurse=false rebase --continue`、`git push`の順に実行する。\n"
            "同じ変更がupstreamへ反映済みの重複commitなら、内容を確認して"
            "`git -c submodule.recurse=false rebase --skip`を実行できる。"
            "中止する場合は`git -c submodule.recurse=false rebase --abort`を実行する"
        ),
    )


_AUTH_FAILURE_MARKERS = (
    "Authentication failed",
    "Permission denied",
    "could not read Username",
    "could not read Password",
    "The requested URL returned error: 401",
    "The requested URL returned error: 403",
)
"""gitの出力のうち認証の失敗を示す語句。"""

_NETWORK_FAILURE_MARKERS = (
    "Could not resolve host",
    "Could not resolve hostname",
    "Connection timed out",
    "Connection refused",
    "Network is unreachable",
    "Operation timed out",
    "unable to access",
)
"""gitの出力のうちネットワーク接続の失敗を示す語句。"""

_NON_FAST_FORWARD_MARKERS = ("non-fast-forward", "[rejected]", "fetch first", "Not possible to fast-forward")
"""gitの出力のうちupstreamがローカルより進んでいることを示す語句。"""


def _sync_failure_next_action(resolved: pathlib.Path, error: subprocess.CalledProcessError) -> tuple[str, str]:
    """gitの出力から失敗の原因を分類し、原因の説明と次の操作を返す。"""
    outputs = [output for output in (error.output, error.stderr) if isinstance(output, str)]
    text = "\n".join(outputs)
    retry = "解消後、失敗した`atk`操作を再実行すると同期を完了できる"
    if any(marker in text for marker in _AUTH_FAILURE_MARKERS):
        return (
            "remoteの認証に失敗した",
            f"`git -C {resolved} remote -v`で接続先を確認し、認証情報（SSH鍵または資格情報）を設定する。{retry}。"
            "認証情報を設定できない場合はユーザーへ報告する",
        )
    if any(marker in text for marker in _NETWORK_FAILURE_MARKERS):
        return (
            "remoteへ接続できなかった",
            f"ネットワーク接続を確認し、時間をおいて失敗した`atk`操作を再実行する。"
            f"繰り返す場合は`git -C {resolved} remote -v`の接続先を確認してユーザーへ報告する",
        )
    if any(marker in text for marker in _NON_FAST_FORWARD_MARKERS):
        return (
            "upstreamにローカルへ無いcommitがある",
            f"`git -C {resolved} status`と`git -C {resolved} log --left-right --oneline HEAD...@{{u}}`で差分を確認し、"
            f"`atk wi commit`でロック下の取込と送信を行ってから失敗した`atk`操作を再実行する",
        )
    return (
        "直前のgitの出力が失敗理由である。認証、ネットワーク接続、remoteの状態のいずれかを解消する",
        f"確認: `git -C {resolved} status`\n{retry}",
    )


def _report_sync_failure(
    private_notes: pathlib.Path,
    operation: str,
    error: subprocess.CalledProcessError,
) -> None:
    """pullまたはpush失敗後に原因別の確認と再実行の手順を表示する。"""
    resolved = private_notes.resolve()
    cause, next_action = _sync_failure_next_action(resolved, error)
    _outcome.report_failure(f"private-notesの{operation}に失敗した: {resolved}。{cause}", next_action=next_action)


def _report_sync_timeout(private_notes: pathlib.Path, operation: str, error: subprocess.TimeoutExpired) -> None:
    """pullまたはpushのgitが時間切れになった場合に、同期の失敗として1本の失敗行を出力する。"""
    if is_reported(error):
        return
    _outcome.report_failure(
        f"private-notesの{operation}に失敗した: {private_notes.resolve()}。{git_timeout_reason(error)}",
        next_action=GIT_TIMEOUT_NEXT_ACTION,
    )
    mark_reported(error)


def push_pending_commits(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner = _run_git,
    result_runner: _GitResultRunner = _run_git_result,
    redundant_divergence: _DivergenceRecovery | None = None,
) -> int | None:
    """branch上の未push commitを送信し、履歴分岐だけをrebaseして再送する。"""
    _LOCK_HELD_PATHS.push_deferred.discard(private_notes.resolve())
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
        _report_sync_timeout(private_notes, "push", error)
        raise


def _push_pending_commits_impl(
    private_notes: pathlib.Path,
    *,
    run_git: _GitRunner,
    result_runner: _GitResultRunner,
    redundant_divergence: _DivergenceRecovery | None,
) -> int | None:
    """pushのGit操作本体を実行する。"""
    assert_repo_lock_held(private_notes)
    ensure_not_rebasing(private_notes)
    if not has_remote(private_notes):
        _report_missing_upstream(private_notes)
        return 0
    if is_human_terminal():
        print(f"remoteへ送信して同期中: {private_notes}", file=sys.stderr, flush=True)
    original_error: subprocess.CalledProcessError
    count = pending_commit_count(private_notes, result_runner=result_runner)
    _LOCK_HELD_PATHS.sync_attempted = True
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
            + _divergence_details(private_notes, result_runner=result_runner),
            next_action=PUSH_DEFERRED_NEXT_ACTION.format(private_notes=private_notes),
        )
        _LOCK_HELD_PATHS.push_deferred.add(private_notes.resolve())
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
        _report_rebase_failure(private_notes, result_runner=result_runner)
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
    """対象pathだけのstage差分を終了コードで判定する。

    Gitリポジトリでないテスト用fake runnerでは`None`を返し、従来どおりcommitを試す。
    実Gitの予期しない終了コードは例外として扱う。
    """
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
    """実在するパスか、追跡済みで削除されたパスに限定したpathspecを返す。"""
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
    """許可pathだけをstage・commitし、branch上のpending commitをpushする。"""
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
    _LOCK_HELD_PATHS.sync_attempted = True
    if skip_push:
        return 0
    return push_fn(private_notes)


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
