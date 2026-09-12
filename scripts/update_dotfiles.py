#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["filelock>=3.30", "platformdirs>=4.0", "psutil"]
# ///
r"""dotfilesリポジトリを最新化するPEP 723スクリプト。

`chezmoi git pull --rebase` → `chezmoi init`（テンプレート再展開） →
`chezmoi status`（apply予定ファイルの表示） → `chezmoi diff --no-pager` →
`chezmoi apply --force`の5段を、プロセス間排他ロック下で直列実行する。
pull又は退避復元が競合した場合は、元HEADと未コミット内容を専用参照へ保存し、
設定済み上流へ作業branchを合わせて更新を継続する。

複数の`update-dotfiles`起動（`atk wi process-loop`の複数常駐・手動実行との重複等）が
同時に`git pull`・`chezmoi apply`を実行するとpullとファイル操作の競合を招くため、
`filelock`でプロセス間直列化する。ロック取得に失敗（タイムアウト）した場合は
exit code 1で終了し、他プロセスの完了を待って再実行するよう促す。

薄いランチャー`bin/update-dotfiles`・`bin/update-dotfiles.cmd`から
`uv run --no-project --script`形式で起動される。dotfilesルートは本ファイルの配置
（`scripts/update_dotfiles.py`）から`Path(__file__)`起点で解決する
（`Path.home()`起点は`$HOME`と実チェックアウト先の不一致を招くため使わない）。

各段の失敗はexit codeをそのまま伝播し以降の段を実行しない。`chezmoi status`段
（表示専用）も含め全段をfail-fast対象とし、既存bash実装が`set -euo pipefail`で
持っていた「いずれかの段が失敗すれば中断する」挙動をそのまま踏襲する。
Gitが進捗を標準エラー出力へ書く場合も、Git更新段が正常終了した場合は
`update-dotfiles`の標準出力へ転送する。失敗時はGitの標準エラー出力を維持する。
各段のサブプロセスへ`MISE_AUTO_INSTALL=0`を渡し、miseのshimが呼び出したコマンドと
無関係なツールを自動導入して更新処理を停止させる経路を抑止する。
取得したchezmoi出力は、プラットフォームの既定値に依存せずUTF-8として厳格にデコードする。
git pull工程は`UPDATE_DOTFILES_GIT_TIMEOUT_SEC`秒で打ち切る。未設定時は600秒、
`0`は上限なしとし、負数又は整数でない値は終了コード2で拒否する。
"""

import argparse
import contextlib
import os
import pathlib
import subprocess
import sys
import time

import filelock
import platformdirs
import psutil

_DOTFILES_ROOT = pathlib.Path(__file__).resolve().parent.parent
_LOCK_PATH = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "locks" / "update-dotfiles.lock"
_LOCK_TIMEOUT_SEC = 600.0
_GIT_TIMEOUT_DEFAULT_SEC = 600
_GIT_OUTPUT_RECOVERY_TIMEOUT_SEC = 30
_PROCESS_TREE_WAIT_TIMEOUT_SEC = 5
_GIT_TIMEOUT_ENV = "UPDATE_DOTFILES_GIT_TIMEOUT_SEC"


def _child_env() -> dict[str, str]:
    """各工程のサブプロセスへ渡す環境を構成する。

    `MISE_AUTO_INSTALL=0`は、実行ファイル名で起動したコマンドがmiseのshimへ解決された場合に、
    呼び出したコマンドと無関係なツールの自動導入が実行されるのを防ぐ。当該導入が失敗すると
    shimが非ゼロ終了し、更新処理が最初の工程で止まる。
    post-apply工程が実行する明示的な`mise install`は当該設定の影響を受けないため、
    ツールの導入自体は従来どおり行われる。
    """
    env = os.environ.copy()
    env["MISE_AUTO_INSTALL"] = "0"
    return env


def _run_step(step_no: int, total: int, title: str, argv: list[str], *, capture: bool = False) -> tuple[int, str]:
    """1段を実行し見出しを表示する。`capture=True`時のみ標準出力を文字列で返す。"""
    print(f"=== [{step_no}/{total}] {title} ===")
    result = subprocess.run(
        argv,
        cwd=_DOTFILES_ROOT,
        check=False,
        capture_output=capture,
        encoding="utf-8" if capture else None,
        env=_child_env(),
    )
    if capture and result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode, (result.stdout if capture else "")


def _git_timeout() -> int | None:
    """Git pullの待機上限を環境変数から返す。`0`は上限なしを表す。"""
    raw_value = os.environ.get(_GIT_TIMEOUT_ENV)
    if raw_value is None:
        return _GIT_TIMEOUT_DEFAULT_SEC
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{_GIT_TIMEOUT_ENV}={raw_value!r}は0以上の整数で指定してください。") from error
    if value < 0:
        raise ValueError(f"{_GIT_TIMEOUT_ENV}={raw_value!r}は0以上の整数で指定してください。")
    return None if value == 0 else value


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    """直接の子を終了する前に子孫を列挙し、起動したプロセスツリーを回収する。"""
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
    psutil.wait_procs(processes, timeout=_PROCESS_TREE_WAIT_TIMEOUT_SEC)
    with contextlib.suppress(ProcessLookupError):
        process.kill()


def _run_git_pull(step_no: int, total: int, *, timeout: int | None = _GIT_TIMEOUT_DEFAULT_SEC) -> int:
    """Git更新段を実行し、正常終了時の出力を標準出力へ正規化する。

    `submodule.recurse=false`は、dotfilesリポジトリがsubmoduleを持たないため不要な再帰を無効化する。
    利用者設定で当該再帰が有効な場合、`git pull`が`git-submodule`を起動する。`git-submodule`は
    POSIX shで実行され、PATH上の`gettext.sh`を読み込むため、当該ファイルがbash専用構文を含むと
    構文エラーで終了し、更新処理が最初の工程で止まる。
    `_child_env`の`MISE_AUTO_INSTALL=0`と同じく、工程が利用者環境の設定を引き継いで停止する経路を抑止する。

    子のセッションとプロセスグループは変更せず、SSH鍵のパスフレーズを制御端末から入力できる状態を保つ。
    `timeout=None`は待機上限を設けない。上限超過時は子を終了する前に子孫を列挙して全て強制終了し、
    出力回収にも上限を設ける。
    """
    print(f"=== [{step_no}/{total}] git pull ===")
    # 上限超過時に子孫を列挙してから直接子を回収するため、プロセスを明示的に保持する。
    process = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
        ["chezmoi", "git", f"--source={_DOTFILES_ROOT}", "--", "-c", "submodule.recurse=false", "pull", "--rebase", "--quiet"],
        cwd=_DOTFILES_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        env=_child_env(),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout) if timeout is not None else process.communicate()
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=_GIT_OUTPUT_RECOVERY_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        if stdout:
            sys.stdout.write(stdout)
        if stderr:
            sys.stderr.write(stderr)
        print(
            f"git pullが{timeout}秒以内に完了しなかったため、子孫プロセスを終了しました。"
            f"未完了です。必要に応じて{_GIT_TIMEOUT_ENV}を調整してください。",
            file=sys.stderr,
        )
        return 1
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        stream = sys.stdout if process.returncode == 0 else sys.stderr
        stream.write(stderr)
    return process.returncode


def _git_capture(*arguments: str) -> subprocess.CompletedProcess[str]:
    """dotfilesリポジトリでGitを実行し、標準出力と標準エラーを取得する。"""
    return subprocess.run(
        ["git", "-C", str(_DOTFILES_ROOT), *arguments],
        check=False,
        capture_output=True,
        encoding="utf-8",
        env=_child_env(),
    )


def _git_value(*arguments: str) -> str | None:
    """Gitの成功した単一値を返し、失敗時は診断を転送する。"""
    result = _git_capture(*arguments)
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        return None
    return result.stdout.strip()


def _git_operation_in_progress() -> bool:
    """既存のmerge又はrebaseが進行中の場合に真を返す。"""
    for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply"):
        path = _git_value("rev-parse", "--git-path", name)
        if path is None:
            return True
        candidate = pathlib.Path(path)
        if not candidate.is_absolute():
            candidate = _DOTFILES_ROOT / candidate
        if candidate.exists():
            return True
    return False


def _git_path_exists(name: str) -> bool:
    """Git管理パスを作業ツリー基準へ解決し、実在を返す。"""
    path = _git_value("rev-parse", "--git-path", name)
    if path is None:
        return False
    candidate = pathlib.Path(path)
    if not candidate.is_absolute():
        candidate = _DOTFILES_ROOT / candidate
    return candidate.exists()


def _run_git_change(*arguments: str) -> bool:
    """単一のGit状態変更を実行し、失敗時の診断を転送する。"""
    result = _git_capture(*arguments)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        (sys.stdout if result.returncode == 0 else sys.stderr).write(result.stderr)
    return result.returncode == 0


def _save_worktree(label: str) -> str | None:
    """未コミット内容をworktree固有refへ退避し、ref名を返す。"""
    result = subprocess.run(
        ["atk", "worktree-stash", "save", f"--label={label}"],
        cwd=_DOTFILES_ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        env=_child_env(),
    )
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        return None
    ref = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if not ref.startswith("refs/worktree/"):
        print("未コミット内容の退避先refを取得できませんでした。", file=sys.stderr)
        return None
    print(f"未コミット内容を{ref}へ退避しました。")
    return ref


def _restore_worktree(ref: str) -> bool:
    """worktree固有refからindexを含む未コミット内容を復元する。"""
    restored = _run_git_change("stash", "apply", "--index", ref)
    if restored:
        print(f"未コミット内容を復元しました。復旧用refは保持します: {ref}")
    return restored


def _clean_saved_untracked(paths: tuple[str, ...]) -> bool:
    """退避済みの未追跡パスだけを作業ツリーから除く。"""
    return not paths or _run_git_change("clean", "-fd", "--", *paths)


def _update_git_with_recovery(step_no: int, total: int, *, timeout: int | None) -> int:
    """Git更新を実行し、競合時は復旧参照を保持して上流へ合わせる。"""
    if _git_operation_in_progress():
        print("既存のmerge又はrebaseが進行中のため、更新を開始しません。", file=sys.stderr)
        return 1
    branch = _git_value("symbolic-ref", "--quiet", "--short", "HEAD")
    upstream = _git_value("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    original_head = _git_value("rev-parse", "HEAD")
    if not branch or not upstream or not original_head:
        print("現在branch、設定済み上流又はHEADを解決できません。", file=sys.stderr)
        return 1
    status = _git_value("status", "--porcelain=v1", "--untracked-files=all")
    if status is None:
        return 1
    untracked_output = _git_value("ls-files", "--others", "--exclude-standard")
    if untracked_output is None:
        return 1
    untracked = tuple(line for line in untracked_output.splitlines() if line)
    suffix = f"{time.time_ns()}-{os.getpid()}"
    stash_ref = _save_worktree(f"update-dotfiles-{suffix}") if status else None
    if status and stash_ref is None:
        return 1

    pull_code = _run_git_pull(step_no, total, timeout=timeout)
    rebase_in_progress = any(_git_path_exists(name) for name in ("rebase-merge", "rebase-apply"))
    if pull_code != 0 and not rebase_in_progress:
        if stash_ref is not None and not _restore_worktree(stash_ref):
            print(f"未コミット内容は{stash_ref}から復旧できます。", file=sys.stderr)
        return pull_code
    if pull_code == 0 and (stash_ref is None or _restore_worktree(stash_ref)):
        return 0

    recovery_branch = f"update-dotfiles-recovery-{suffix}"
    if not _run_git_change("branch", recovery_branch, original_head):
        print("復旧用branchを保存できなかったため、自動回復を中止します。", file=sys.stderr)
        return 1
    if rebase_in_progress and not _run_git_change("rebase", "--abort"):
        return 1
    if not _run_git_change("reset", "--hard", upstream):
        return 1
    if not _clean_saved_untracked(untracked):
        return 1
    print(
        f"競合から回復し、{branch}を{upstream}へ合わせました。"
        f"元のcommitは{recovery_branch}、未コミット内容は{stash_ref or 'なし'}から復旧できます。"
    )
    return 0


def _filter_apply_pending(status_output: str) -> list[str]:
    """`chezmoi status`出力から2列目（apply予定を表す列）が空白以外の行のみ抽出する。

    `chezmoi status`は2列構成。1列目=前回chezmoiが書いた状態vs現在のdestination実ファイル、
    2列目=現在の実ファイルvsターゲット状態（＝これからapplyで起きる変更）。
    2文字未満の行はスライスが空文字列を返すため常に対象外とする。
    """
    return [line for line in status_output.splitlines() if len(line) > 1 and line[1] != " "]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(description="dotfilesを取得し、chezmoiで反映する")
    return parser.parse_args([] if argv is None else argv)


def main(argv: list[str] | None = None) -> int:
    """更新処理を排他ロック下で直列実行し、最終exit codeを返す。"""
    _parse_args(argv)
    try:
        git_timeout = _git_timeout()
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    total = 5
    lock_dir = _LOCK_PATH.parent
    lock_dir.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.FileLock(str(_LOCK_PATH), timeout=_LOCK_TIMEOUT_SEC):
            returncode = _update_git_with_recovery(1, total, timeout=git_timeout)
            if returncode != 0:
                return returncode

            returncode, _ = _run_step(
                2,
                total,
                "chezmoi init (テンプレート再展開)",
                ["chezmoi", "init", f"--source={_DOTFILES_ROOT}"],
            )
            if returncode != 0:
                return returncode

            returncode, status_output = _run_step(
                3,
                total,
                "chezmoi status (apply予定のファイル)",
                ["chezmoi", "status", "-x", "scripts"],
                capture=True,
            )
            if returncode != 0:
                return returncode
            for line in _filter_apply_pending(status_output):
                print(line)

            returncode, diff_output = _run_step(
                4,
                total,
                "chezmoi diff (上書き前の差分)",
                ["chezmoi", "diff", "--no-pager"],
                capture=True,
            )
            if diff_output:
                sys.stdout.write(diff_output)
            if returncode != 0:
                return returncode

            returncode, _ = _run_step(
                total,
                total,
                "chezmoi apply (post-apply実行)",
                ["chezmoi", "apply", "--force"],
            )
            if returncode != 0:
                return returncode
    except filelock.Timeout:
        print(
            f"ロック取得に失敗しました（{_LOCK_TIMEOUT_SEC:.0f}秒待機後もタイムアウト）。"
            "他のupdate-dotfiles実行の完了を待って再実行してください。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
