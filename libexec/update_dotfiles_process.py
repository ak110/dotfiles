"""update_dotfilesのGit子プロセスの実行と回収。"""

import contextlib
import os
import pathlib
import subprocess
import sys
import threading
import time
from typing import Self

import psutil
import update_dotfiles_runtime
from agent_toolkit._git import stale_lock

from pytools._internal import sync_report


def git_timeout() -> int | None:
    """Git pullの待機上限を環境変数から返す。`0`は上限なしを表す。"""
    raw_value = os.environ.get(update_dotfiles_runtime.GIT_TIMEOUT_ENV)
    if raw_value is None:
        return update_dotfiles_runtime.GIT_TIMEOUT_DEFAULT_SEC
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{update_dotfiles_runtime.GIT_TIMEOUT_ENV}={raw_value!r}は0以上の整数で指定してください。") from error
    if value < 0:
        raise ValueError(f"{update_dotfiles_runtime.GIT_TIMEOUT_ENV}={raw_value!r}は0以上の整数で指定してください。")
    return None if value == 0 else value


class GitReclaimError(RuntimeError):
    """更新段が起動したGitの子プロセスを回収できない。

    残ったGit操作がindexや作業ツリーを書き換え得るため、任意プラグイン更新の失敗として続行せず、
    chezmoiの適用へ進まずに更新全体を止める。
    """


class StageReclaimError(GitReclaimError):
    """待機上限を超えた段の子孫プロセスを回収できない。残った子孫が作業ツリーや配備先を書き換え得るため更新全体を止める。"""


class DescendantTracker:
    """段の実行中に自プロセスの子孫を一定間隔で控え、上限超過時にまとめて回収する。

    `subprocess.run`は上限超過時に直接の子だけを終了し、孫は親を失って列挙できなくなる。
    実行中に控えた子孫は、`psutil.Process`が作成時刻でPIDの再利用を見分けるため、後から安全に終了できる。
    """

    _SAMPLE_INTERVAL_SEC = 0.5

    def __init__(self) -> None:
        self._seen: dict[int, psutil.Process] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def __enter__(self) -> Self:
        """子孫の観測を開始する。"""
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        """観測スレッドの終了を待つ。"""
        self._stop.set()
        self._thread.join(timeout=5)

    def _sample(self) -> None:
        while True:
            with contextlib.suppress(psutil.Error):
                for child in psutil.Process().children(recursive=True):
                    self._seen.setdefault(child.pid, child)
            if self._stop.wait(self._SAMPLE_INTERVAL_SEC):
                return

    def kill_all(self) -> None:
        """控えた子孫のうち生存しているものを終了する。終了しないものが残れば`StageReclaimError`を送出する。"""
        self._stop.set()
        self._thread.join(timeout=5)
        with contextlib.suppress(psutil.Error):
            for child in psutil.Process().children(recursive=True):
                self._seen.setdefault(child.pid, child)
        targets = [process for process in self._seen.values() if process.is_running()]
        for process in targets:
            with contextlib.suppress(psutil.NoSuchProcess):
                process.kill()
        _gone, alive = psutil.wait_procs(targets, timeout=update_dotfiles_runtime.PROCESS_TREE_WAIT_TIMEOUT_SEC)
        if alive:
            raise StageReclaimError(
                "待機上限を超えた段の子プロセスを回収できないため更新を中止しました: "
                + ", ".join(str(item.pid) for item in alive)
                + "。表示されたPIDのプロセスを確認して終了させ、update-dotfilesを再実行してください。"
            )


def kill_process_tree(process: subprocess.Popen[str]) -> None:
    """直接の子を終了する前に子孫を列挙し、起動したプロセスツリーを回収する。回収できなければ`_GitReclaimError`を送出する。"""
    processes: list[psutil.Process] = []
    try:
        parent = psutil.Process(process.pid)
        processes = [*parent.children(recursive=True), parent]
    except psutil.NoSuchProcess:
        pass

    for child in processes:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            continue
    _gone, alive = psutil.wait_procs(processes, timeout=update_dotfiles_runtime.PROCESS_TREE_WAIT_TIMEOUT_SEC)
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    if alive:
        raise GitReclaimError(
            "Git操作の子プロセスを回収できないため更新を中止しました: "
            + ", ".join(str(item.pid) for item in alive)
            + "。表示されたPIDのプロセスを確認して終了させ、update-dotfilesを再実行してください。"
        )


def run_git_pull(
    step_no: int,
    total: int,
    *,
    timeout: float | None = update_dotfiles_runtime.GIT_TIMEOUT_DEFAULT_SEC,
    plugin: pathlib.Path | None = None,
) -> int:
    """Git更新段を実行する。プラグインのpullが標準ロックの競合で失敗した場合は、待機と回収の後に1回だけ再実行する。

    pullはロック作成に失敗した時点で変更を始めていないため、再実行は安全である。
    本体のpullは、`update_dotfiles_git`が保持するindexロックの外で実行されない（代替indexを使う）ため対象外とする。
    """
    started = time.monotonic()
    code = _run_git_pull_once(step_no, total, timeout=timeout, plugin=plugin)
    if code == 0 or plugin is None:
        return code

    def remaining() -> float | None:
        """初回実行で使った時間を差し引いた、待機と再実行に使える秒数。"""
        return None if timeout is None else max(timeout - (time.monotonic() - started), 1.0)

    recovery = stale_lock.recover_after_failure(
        update_dotfiles_runtime.last_stderr_tail or "",
        plugin,
        wait_seconds=stale_lock.lock_wait_seconds(remaining()),
    )
    if recovery.retry:
        return _run_git_pull_once(step_no, total, timeout=remaining(), plugin=plugin)
    if recovery.diagnosis is not None:
        message = recovery.diagnosis.text(
            "update-dotfiles logsで永続ログを確認し、所有者が終了していることを確かめてから再実行してください。"
        )
        print(message, file=sys.stderr)
        update_dotfiles_runtime.logger.warning(message)
        update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(
            f"{update_dotfiles_runtime.last_stderr_tail}\n{message}"
        )
    return code


def _run_git_pull_once(
    step_no: int,
    total: int,
    *,
    timeout: float | None,
    plugin: pathlib.Path | None,
) -> int:
    """Git更新段を1回実行し、正常終了時の出力を標準出力へ正規化する。

    `submodule.recurse=false`は、dotfilesリポジトリがsubmoduleを持たないため不要な再帰を無効化する。
    ユーザー設定でこの再帰が有効な場合、`git pull`が`git-submodule`を起動する。`git-submodule`は
    POSIX shで実行され、PATH上の`gettext.sh`を読み込むため、そのファイルがbash専用構文を含むと
    構文エラーで終了し、更新処理が最初の工程で止まる。
    `_child_env`の`MISE_AUTO_INSTALL=0`と同じく、工程がユーザー環境の設定を引き継いで停止する経路を抑止する。

    子のセッションとプロセスグループは変更せず、SSH鍵のパスフレーズを制御端末から入力できる状態を保つ。
    `timeout=None`は待機上限を設けない。上限超過時は子を終了する前に子孫を列挙して全て強制終了し、
    出力回収にも上限を設ける。POSIXでは回収に失敗しても呼出側が両pipeを閉じる。
    Windowsではcommunicateのreader threadが閉じるため、読み取りロックを待ち得る主スレッドのcloseは行わない。
    """
    title = "git pull" if plugin is None else f"dotfilesプラグイン {plugin.name}: git pull"
    update_dotfiles_runtime.current_stage_title = title
    update_dotfiles_runtime.last_stderr_tail = None
    print(f"=== [{step_no}/{total}] {title} ===", flush=True)
    update_dotfiles_runtime.logger.info("stage開始: %d/%d %s", step_no, update_dotfiles_runtime.LOG_STAGE_TOTAL, title)
    started_at = time.monotonic()
    # Windowsではreader threadがpipeを閉じる。主スレッドでpipeのロックを待つPopen.__exit__を避ける。
    try:
        process = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
            [
                "chezmoi",
                "git",
                f"--source={update_dotfiles_runtime.DOTFILES_ROOT}",
                "--",
                "-c",
                "submodule.recurse=false",
                "pull",
                "--rebase",
                "--quiet",
            ]
            if plugin is None
            else ["git", "-C", str(plugin), "-c", "submodule.recurse=false", "pull", "--rebase"],
            cwd=update_dotfiles_runtime.DOTFILES_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=update_dotfiles_runtime.git_env() if plugin is None else update_dotfiles_runtime.child_env(),
        )
    except OSError as error:
        update_dotfiles_runtime.logger.exception(
            "stage起動失敗: %d/%d %s", step_no, update_dotfiles_runtime.LOG_STAGE_TOTAL, title
        )
        print(
            f"{title}を開始できませんでした: {error}。"
            "chezmoi --versionとgit --versionの実行に成功する状態にしてupdate-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(f"{title}: {error}")
        return 1
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout) if timeout is not None else process.communicate()
        except KeyboardInterrupt:
            kill_process_tree(process)
            process.wait(timeout=update_dotfiles_runtime.PROCESS_TREE_WAIT_TIMEOUT_SEC)
            raise
        except subprocess.TimeoutExpired:
            kill_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=update_dotfiles_runtime.GIT_OUTPUT_RECOVERY_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            if stdout:
                sys.stdout.write(stdout)
            if stderr:
                sys.stderr.write(stderr)
            timeout_message = (
                f"{title}が{timeout}秒以内に完了しなかったため、子孫プロセスを終了しました。"
                f"未完了です。通信とSSH認証の問題を解消してupdate-dotfilesを再実行してください。"
                f"待機上限を延ばす場合は{update_dotfiles_runtime.GIT_TIMEOUT_ENV}へ1200（秒）を設定して再実行してください。"
            )
            print(timeout_message, file=sys.stderr)
            update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(
                f"{title}\n{stdout}\n{stderr}\n{timeout_message}"
            )
            update_dotfiles_runtime.logger.error(
                "stage終了: %d/%d %s exit=1 timeout=%s duration=%.3f stdout:\n%s\nstderr:\n%s",
                step_no,
                update_dotfiles_runtime.LOG_STAGE_TOTAL,
                title,
                timeout,
                time.monotonic() - started_at,
                stdout,
                stderr,
            )
            return 1
        if stdout:
            sys.stdout.write(stdout)
        if stderr:
            stream = sys.stdout if process.returncode == 0 else sys.stderr
            stream.write(stderr)
            update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(stderr)
        update_dotfiles_runtime.logger.info("%s stdout:\n%s\nstderr:\n%s", title, stdout, stderr)
        if plugin is not None and process.returncode != 0:
            update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(
                f"{stdout}\n{stderr}\n{title}: exit {process.returncode}"
            )
        update_dotfiles_runtime.logger.info(
            "stage終了: %d/%d %s exit=%d duration=%.3f",
            step_no,
            update_dotfiles_runtime.LOG_STAGE_TOTAL,
            title,
            process.returncode,
            time.monotonic() - started_at,
        )
        return process.returncode

    finally:
        if os.name != "nt":
            with contextlib.ExitStack() as cleanup:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        cleanup.callback(stream.close)


def _git_capture(*arguments: str) -> subprocess.CompletedProcess[str]:
    """dotfilesリポジトリでGitを実行し、標準出力と標準エラーを取得する。"""
    argv = ["git", "-C", str(update_dotfiles_runtime.DOTFILES_ROOT), *arguments]
    with subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=update_dotfiles_runtime.git_env(),
    ) as process:
        try:
            stdout, stderr = process.communicate()
        except KeyboardInterrupt:
            kill_process_tree(process)
            stdout, stderr = process.communicate(timeout=update_dotfiles_runtime.GIT_OUTPUT_RECOVERY_TIMEOUT_SEC)
            update_dotfiles_runtime.logger.warning("Git操作を中断: %s stdout:\n%s\nstderr:\n%s", argv, stdout, stderr)
            raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def git_value(*arguments: str, strip: bool = True) -> str | None:
    """Gitの成功した単一値を返し、失敗時は診断を転送する。"""
    result = _git_capture(*arguments)
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        return None
    return result.stdout.strip() if strip else result.stdout


def run_git_change(*arguments: str) -> bool:
    """単一のGit状態変更を実行し、失敗時の診断を転送する。"""
    update_dotfiles_runtime.logger.info("Git状態変更開始: git %s", " ".join(arguments))
    result = _git_capture(*arguments)
    update_dotfiles_runtime.logger.info(
        "git %s: exit=%d stdout:\n%s\nstderr:\n%s", " ".join(arguments), result.returncode, result.stdout, result.stderr
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        (sys.stdout if result.returncode == 0 else sys.stderr).write(result.stderr)
    if result.returncode != 0:
        update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(
            f"git {' '.join(arguments)}: exit={result.returncode}\n{result.stdout}\n{result.stderr}"
        )
    return result.returncode == 0
