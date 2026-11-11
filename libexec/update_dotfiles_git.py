"""update_dotfilesのGit所有・index保護と競合回復。"""

import contextlib
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator
from types import FrameType

import psutil
import update_dotfiles_process
import update_dotfiles_runtime
import update_dotfiles_stashing

from pytools._internal import sync_report


def _git_operation_in_progress() -> str | bool:
    """対象worktreeの操作状態と、index準備後も存続するGitプロセスを調べる。"""
    for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD", "sequencer"):
        path = update_dotfiles_process.git_value("rev-parse", "--git-path", name)
        if path is None:
            return True
        candidate = pathlib.Path(path)
        if not candidate.is_absolute():
            candidate = update_dotfiles_runtime.DOTFILES_ROOT / candidate
        if candidate.exists():
            return name
    git_dir = update_dotfiles_process.git_value("rev-parse", "--absolute-git-dir")
    if git_dir is None:
        return True
    target = pathlib.Path(git_dir).resolve()
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            command = process.info["cmdline"] or []
            if not command or pathlib.Path(command[0]).name.lower() not in {"git", "git.exe"}:
                continue
            # -C、--git-dir等の解釈とlinked worktreeの対応はGit自身へ委ねる。
            options: list[str] = []
            position = 1
            while position < len(command) and command[position].startswith("-"):
                option = command[position]
                options.append(option)
                position += 1
                if option in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}:
                    options.append(command[position])
                    position += 1
            env = update_dotfiles_runtime.child_env()
            process_env = process.environ()
            for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
                env.pop(name, None)
                if name in process_env:
                    env[name] = process_env[name]
            env["GIT_OPTIONAL_LOCKS"] = "0"
            # 走査中に消える他プロセスのcwdはGit自身に検査させる。
            # probeの起動cwdを固定し、消失をプロセス起動のOSErrorへ変換しない。
            result = subprocess.run(
                ["git", "-C", process.cwd(), *options, "rev-parse", "--absolute-git-dir"],
                cwd=update_dotfiles_runtime.DOTFILES_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            if result.returncode == 0 and pathlib.Path(result.stdout.strip()).resolve() == target:
                return True
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            continue
        except psutil.AccessDenied, OSError, IndexError:
            # Gitを確認できない場合は内容変更を開始しない。
            return True
    return False


def _skip_git_update(reason: str, operation: str | bool = False) -> int:
    """未完了の理由と再試行契機を全ての診断先へ残す。"""
    update_dotfiles_runtime.current_stage_title = "Git更新の所有確認"
    commands = {
        "MERGE_HEAD": "merge",
        "rebase-merge": "rebase",
        "rebase-apply": "rebase",
        "CHERRY_PICK_HEAD": "cherry-pick",
        "REVERT_HEAD": "revert",
    }
    command = commands.get(operation) if isinstance(operation, str) else None
    if command:
        action = (
            f"git statusで進行中の操作を確認し、git {command} --continueで完了するか、"
            f"git {command} --abortで中止してからupdate-dotfilesを再実行してください。"
        )
    elif operation == "sequencer":
        action = (
            "git statusで進行中の操作を確認し、git cherry-pickまたはgit revertの"
            "--continueで完了するか、同じ操作の--abortで中止してからupdate-dotfilesを再実行してください。"
        )
    else:
        action = "進行中のGit操作が終了してからupdate-dotfilesを再実行してください。"
    message = f"{reason}のためGit更新は未完了です。{action}"
    update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(message)
    update_dotfiles_runtime.logger.warning(message)
    print(message, file=sys.stderr)
    return 1


@contextlib.contextmanager
def _git_update_signals() -> Generator[None]:
    """サービス停止と端末切断を、Git子とindexの回収を実行する中断へ変換する。"""

    def interrupt(signum: int, _frame: FrameType | None) -> None:
        raise KeyboardInterrupt(f"Git更新をシグナル{signum}で中断しました")

    signals = [signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        signals.append(signal.SIGHUP)
    previous = {number: signal.getsignal(number) for number in signals}
    try:
        for number in signals:
            signal.signal(number, interrupt)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


@contextlib.contextmanager
def _locked_git_index(index: pathlib.Path) -> Generator[None]:
    """標準ロックを保持し、終了状態の代替indexを公開して自身の一時物を回収する。"""
    lock = index.with_name(index.name + ".lock")
    # 排他的作成に成功したロックだけをfinallyで扱う。
    with lock.open("xb") as owned_lock:
        identity = os.fstat(owned_lock.fileno())
    alternate: pathlib.Path | None = None
    prepared = False
    try:
        with tempfile.NamedTemporaryFile(prefix="update-dotfiles-index-", dir=index.parent, delete=False) as temporary:
            alternate = pathlib.Path(temporary.name)
        if index.exists():
            shutil.copyfile(index, alternate)
        else:
            alternate.unlink()
        update_dotfiles_runtime.update_index = alternate
        prepared = True
        yield
    finally:
        update_dotfiles_runtime.update_index = None
        # 更新子は全て回収してからここへ戻る。完了済みGit操作のindexは失敗・中断時も公開する。
        try:
            if alternate is not None:
                current = lock.stat()
                if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                    raise RuntimeError(f"Git更新のロック所有者が変わりました: {lock}")
                if prepared and alternate.exists():
                    os.replace(alternate, index)
        finally:
            try:
                if alternate is not None:
                    try:
                        alternate.unlink(missing_ok=True)
                    finally:
                        alternate.with_name(alternate.name + ".lock").unlink(missing_ok=True)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    current = lock.stat()
                    if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                        lock.unlink()


def _git_path_exists(name: str) -> bool:
    """Git管理パスを作業ツリー基準へ解決し、実在を返す。"""
    path = update_dotfiles_process.git_value("rev-parse", "--git-path", name)
    if path is None:
        return False
    candidate = pathlib.Path(path)
    if not candidate.is_absolute():
        candidate = update_dotfiles_runtime.DOTFILES_ROOT / candidate
    return candidate.exists()


def _discard_mise_lock_changes() -> bool:
    """ルート`mise.lock`のindexと作業ツリーの差分をHEADの内容へ戻す。差分が無ければ何もしない。"""
    status = update_dotfiles_process.git_value("status", "--porcelain=v1", "--", update_dotfiles_runtime.MISE_LOCK)
    if status is None:
        return False
    return not status or update_dotfiles_process.run_git_change("checkout", "HEAD", "--", update_dotfiles_runtime.MISE_LOCK)


def update_git_with_recovery(step_no: int, total: int, *, timeout: int | None) -> int:
    """最初の変更前に所有を確認し、Git更新区間を標準indexロックで保護する。"""
    operation = _git_operation_in_progress()
    if operation:
        return _skip_git_update("既存のGit操作が進行中", operation)
    value = update_dotfiles_process.git_value("rev-parse", "--git-path", "index")
    if value is None:
        return _skip_git_update("対象indexを解決できない")
    index = pathlib.Path(value)
    if not index.is_absolute():
        index = update_dotfiles_runtime.DOTFILES_ROOT / index
    try:
        with _git_update_signals(), _locked_git_index(index):
            operation = _git_operation_in_progress()
            if operation:
                return _skip_git_update("ロック取得前に開始したGit操作が進行中", operation)
            return _update_owned_git(step_no, total, timeout=timeout)
    except FileExistsError:
        return _skip_git_update("対象indexの標準ロックが使用中")
    except OSError as error:
        update_dotfiles_runtime.logger.exception("Git更新のindex管理に失敗")
        return _skip_git_update(f"対象indexの管理に失敗: {error}")


def _update_owned_git(step_no: int, total: int, *, timeout: int | None) -> int:
    """所有区間内で更新し、競合時は復旧参照を保持して上流へ合わせる。"""
    if update_dotfiles_runtime.update_index is None:
        raise RuntimeError("Git更新にはindexの所有が必要です")
    if not update_dotfiles_stashing.protect_old_worktree_stashes():
        print(
            "過去の退避データを確認できませんでしたが、更新を続けます。旧形式の参照は変更されず残り、今回の退避は新しい参照へ保存されます。",
            file=sys.stderr,
        )
        update_dotfiles_runtime.continue_after_failure(
            "過去の退避データの整理",
            "git statusとupdate-dotfiles logsで原因を確かめ、解消しない場合はログを添えて報告してください。",
            update_dotfiles_runtime.last_stderr_tail,
        )
    if not _discard_mise_lock_changes():
        print(
            "mise.lockの差分を破棄できませんでしたが、更新を続けます。破棄できなかった差分は未コミット内容として退避・復元されます。",
            file=sys.stderr,
        )
        update_dotfiles_runtime.continue_after_failure(
            "mise.lockの差分の破棄",
            "git status --short -- mise.lockで状態を確認し、Gitのエラーと書込権限の問題を解消して"
            "update-dotfilesを再実行してください。",
            update_dotfiles_runtime.last_stderr_tail,
        )
    branch = update_dotfiles_process.git_value("symbolic-ref", "--quiet", "--short", "HEAD")
    upstream = update_dotfiles_process.git_value("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    original_head = update_dotfiles_process.git_value("rev-parse", "HEAD")
    if not branch or not upstream or not original_head:
        print(
            "現在branch、設定済み上流またはHEADを解決できません。git statusとgit branch -vvで状態を確認し、"
            "branchを選びgit branch --set-upstream-to=<remote>/<branch>で上流を設定して"
            "update-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        return 1
    status = update_dotfiles_process.git_value("status", "--porcelain=v1", "--untracked-files=all")
    if status is None:
        return 1
    untracked_output = update_dotfiles_process.git_value("ls-files", "-z", "--others", "--exclude-standard", strip=False)
    if untracked_output is None:
        return 1
    untracked = tuple(path for path in untracked_output.split("\0") if path)
    suffix = f"{time.time_ns()}-{os.getpid()}"
    stash_ref = update_dotfiles_stashing.save_worktree(f"update-dotfiles-{suffix}") if status else None
    if status and stash_ref is None:
        return 1

    pull_code = update_dotfiles_process.run_git_pull(step_no, total, timeout=timeout)
    rebase_in_progress = any(_git_path_exists(name) for name in ("rebase-merge", "rebase-apply"))
    if pull_code != 0 and not rebase_in_progress:
        if stash_ref is not None and update_dotfiles_stashing.restore_worktree(stash_ref):
            update_dotfiles_stashing.drop_restored_worktree(stash_ref)
        return pull_code
    if pull_code == 0 and (stash_ref is None or update_dotfiles_stashing.restore_worktree(stash_ref)):
        return 0 if stash_ref is None or update_dotfiles_stashing.drop_restored_worktree(stash_ref) else 1

    recovery_branch = f"update-dotfiles-recovery-{suffix}"
    if not update_dotfiles_process.run_git_change("branch", recovery_branch, original_head):
        update_dotfiles_stashing.record_git_recovery(
            "復旧用branchを保存できなかったため、自動回復を中止します。"
            f"git branch {recovery_branch} {original_head}で元HEADを保存し、git statusで競合を確認してください。"
            "解消できない場合は、このログをdotfilesの作者へ報告してください。",
        )
        return 1
    if rebase_in_progress and not update_dotfiles_process.run_git_change("rebase", "--abort"):
        return 1
    if not update_dotfiles_process.run_git_change("reset", "--hard", upstream):
        return 1
    if not update_dotfiles_stashing.clean_saved_untracked(untracked):
        return 1
    update_dotfiles_stashing.record_git_recovery(
        f"競合から回復し、{branch}を{upstream}へ合わせました。"
        f"元のcommitは{recovery_branch}、未コミット内容は{stash_ref or 'なし'}から復旧できます。"
        "更新後の内容を使う場合は対応不要です。元のcommitへ戻す場合は"
        f"git switch {recovery_branch}を実行してください。"
        + (f"未コミット内容を戻す場合はgit stash apply --index {stash_ref}を実行してください。" if stash_ref else "")
    )
    return 0
