"""update_dotfilesの未コミット内容の退避と復元。"""

import contextlib
import os
import subprocess
import sys
import time

import update_dotfiles_process
import update_dotfiles_runtime

from pytools._internal import sync_report


def _run_worktree_stash(*arguments: str) -> subprocess.CompletedProcess[str] | None:
    """同じcheckoutの退避CLIを有限時間で実行し、子孫と出力の回収にも上限を設ける。"""
    launcher = update_dotfiles_runtime.SOURCE_ROOT / "libexec" / "update_dotfiles_stash.py"
    argv = [sys.executable, str(launcher), *arguments]
    title = "worktree-stash " + " ".join(arguments)
    display_title = {
        "save": "未コミット変更の一時保存",
        "drop": "復元済みの一時保存データの回収",
        "protect-old": "過去の退避データの整理",
    }.get(arguments[0], "退避データの保護")
    update_dotfiles_runtime.current_stage_title = title
    update_dotfiles_runtime.last_stderr_tail = None
    if arguments[0] != "protect-old":
        print(f"{display_title}を行っています。", flush=True)
    started_at = time.monotonic()
    update_dotfiles_runtime.logger.info("退避操作開始: %s", title)
    # Windowsではreader threadへpipeの解放を任せ、Popen.__exit__による主スレッドのcloseを避ける。
    try:
        process = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
            argv,
            cwd=update_dotfiles_runtime.DOTFILES_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=update_dotfiles_runtime.git_env(),
        )
    except OSError as error:
        update_dotfiles_runtime.logger.exception(
            "worktree-stashの起動に失敗: worktree=%s launcher=%s", update_dotfiles_runtime.DOTFILES_ROOT, launcher
        )
        print(
            f"未コミット内容の退避操作を開始できませんでした ({update_dotfiles_runtime.DOTFILES_ROOT}): {error}。"
            f"{launcher}の実在と読取権限を確認し、update-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(f"{title}: {error}")
        return None
    try:
        try:
            stdout, stderr = process.communicate(timeout=update_dotfiles_runtime.WORKTREE_STASH_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            update_dotfiles_process.kill_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=update_dotfiles_runtime.GIT_OUTPUT_RECOVERY_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            outcome = (
                "子孫プロセスを終了しました。"
                if arguments[0] == "protect-old"
                else "子孫プロセスを終了して更新を中止しました。"
            )
            message = (
                f"{display_title}が{update_dotfiles_runtime.WORKTREE_STASH_TIMEOUT_SEC}秒以内に完了しなかったため、"
                f"{outcome}update-dotfiles logsで停止した操作と退避先を確認してください。"
            )
            update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(f"{stdout}\n{stderr}\n{message}")
            update_dotfiles_runtime.logger.error(
                "退避操作timeout: %s duration=%.3f stdout=%s stderr=%s", title, time.monotonic() - started_at, stdout, stderr
            )
            if stdout:
                sys.stdout.write(stdout)
            if stderr:
                sys.stderr.write(stderr)
            print(message, file=sys.stderr)
            return None
        except KeyboardInterrupt:
            update_dotfiles_process.kill_process_tree(process)
            process.wait(timeout=update_dotfiles_runtime.PROCESS_TREE_WAIT_TIMEOUT_SEC)
            raise
    finally:
        # Windowsのreader threadがpipeを保持している場合、主スレッドのcloseも待機し得る。
        if os.name != "nt":
            with contextlib.ExitStack() as cleanup:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        cleanup.callback(stream.close)
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    update_dotfiles_runtime.logger.info(
        "退避操作終了: %s exit=%d duration=%.3f stdout=%s stderr=%s",
        title,
        result.returncode,
        time.monotonic() - started_at,
        stdout,
        stderr,
    )
    if result.returncode != 0:
        update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(
            f"{title}: exit {result.returncode}\n{stdout}\n{stderr}"
        )
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return None
    return result


def save_worktree(label: str) -> str | None:
    """未コミット内容をworktree固有refへ退避し、ref名を返す。"""
    result = _run_worktree_stash("save", f"--label={label}")
    if result is None:
        return None
    ref = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if not ref.startswith("refs/worktree/"):
        print(
            "未コミット内容の退避先を取得できませんでした。git for-each-ref refs/worktree/で退避状態を確認し、"
            "未コミット内容が保護されていることを確かめてからupdate-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        return None
    update_dotfiles_runtime.logger.info("未コミット内容の退避先: %s", ref)
    print("未コミット変更を一時保存しました。更新後に復元します。")
    return ref


def restore_worktree(ref: str) -> bool:
    """worktree固有refからindexを含む未コミット内容を復元する。"""
    restored = update_dotfiles_process.run_git_change("stash", "apply", "--index", ref)
    if restored:
        print("一時保存していた未コミット変更を復元しました。")
    else:
        record_git_recovery(
            f"未コミット内容の復元に失敗しました。未コミット内容は{ref}から復旧できます。"
            "git statusで競合を確認し、競合を解消した作業ツリーで"
            f"git stash apply --index {ref}を実行してください。"
        )
    return restored


def record_git_recovery(message: str) -> None:
    """復元・回復の結果と次の操作を画面、永続ログ、同期結果へ残す。"""
    update_dotfiles_runtime.logger.warning(message)
    update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(
        f"{update_dotfiles_runtime.last_stderr_tail or ''}\n{message}".strip()
    )
    print(message, file=sys.stderr)


def drop_restored_worktree(ref: str) -> bool:
    """復元済みの今回退避だけ回収し、失敗時も復元済み内容を変更しない。"""
    if _run_worktree_stash("drop", ref) is None:
        print(
            f"復元は成功しましたが退避の回収は未完了です: {ref}。"
            f"復元済み内容は保持しています。atk worktree-stash drop {ref}で再試行してください。",
            file=sys.stderr,
        )
        return False
    print("復元済みの一時保存データを回収しました。")
    return True


def protect_old_worktree_stashes() -> bool:
    """旧版の未保護退避を1回の子起動で移行し、保護済みの退避は再処理しない。"""
    result = _run_worktree_stash("protect-old")
    if result is None:
        return False
    if result.stdout.strip():
        # 個別refの成功行は永続ログに保持し、画面へは最後の移行結果だけを出力する。
        print(result.stdout.strip().splitlines()[-1], flush=True)
    return True


def clean_saved_untracked(paths: tuple[str, ...]) -> bool:
    """退避済みの未追跡パスだけを作業ツリーから除く。"""
    return not paths or update_dotfiles_process.run_git_change("clean", "-fd", "--", *paths)
