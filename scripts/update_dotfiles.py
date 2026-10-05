#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["filelock>=3.30", "platformdirs>=4.0", "psutil"]
# ///
r"""dotfilesリポジトリを最新化するPEP 723スクリプト。

`chezmoi git pull --rebase` → `chezmoi init`（テンプレート再展開） →
`chezmoi status`（apply予定ファイルの表示） → `chezmoi diff --no-pager` →
`chezmoi apply --force`を、プロセス間排他ロック下で直列実行する。
画面には4段の進捗を示し、diffの詳細は永続ログへ記録する。
pull前にルート`mise.lock`の差分を破棄する。`mise.lock`はコミット済みの`mise.toml`から
再生成でき、更新処理のmise操作が書き戻した差分をユーザーの未コミット内容として保持しないためである。
それ以外の未コミット内容は退避してpull後に復元する。
pullまたは退避復元が競合した場合は、元HEADと未コミット内容を専用参照へ保存し、
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
無関係なツールを自動導入して更新処理を停止させる経路を抑止する。親プロセスの
仮想環境は子へ引き継がず、各工程が自身の設定から環境を解決する。
取得したchezmoi出力は、プラットフォームのロケール設定が決める文字コードに依存せずUTF-8として厳格にデコードする。
git pull工程は`UPDATE_DOTFILES_GIT_TIMEOUT_SEC`秒で打ち切る。未設定時は600秒、
`0`は上限なしとし、負数または整数でない値は終了コード2で拒否する。

実行の開始時と終了時に、同期結果を`scripts/sync_report.py`が定める構造化ファイルへ記録する。
次に起動するコーディングエージェントが、失敗した段と標準エラーの末尾からAWIの処理を
完遂できるかを判定するための記録であり、失敗の内容を人間の目視に頼らず残す。
取得した段の標準エラーは、表示のために親の標準エラーへ転送したうえで末尾を記録へ残す。

Linuxでは`chezmoi apply`の直前に、Codexの管理daemonだけが稼働し、利用セッションが無く、
daemonの遠隔接続機能が`disabled`の場合に限り、公開CLI`codex app-server daemon stop`で管理daemonを
一時停止する。post-applyのplugin更新と診断ログ復元が、利用セッションの無い管理daemonを理由に延期されないためである。
停止を試みた場合は`chezmoi apply`の成否にかかわらず`codex app-server daemon start`で起動状態を戻し、
停止または再起動の失敗を終了コードと同期結果へ反映する。利用セッションが残る場合、遠隔接続機能の状態が
`disabled`以外か判定できない場合、および`DOTFILES_CODEX_DAEMON_AUTO_RESTART=1`の場合は停止しない。
"""

# pylint: disable=global-statement

import argparse
import base64
import contextlib
import dataclasses
import io
import json
import logging
import logging.handlers
import os
import pathlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import time

import filelock
import platformdirs
import psutil
import sync_report

_SOURCE_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SOURCE_ROOT))

from pytools._internal import codex_processes  # noqa: E402  # pylint: disable=wrong-import-position

_DOTFILES_ROOT = _SOURCE_ROOT
_LOCK_PATH = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "locks" / "update-dotfiles.lock"
_LOG_PATH = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "update-dotfiles.log"
_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUP_COUNT = 3
_RUN_ID_ENV = "UPDATE_DOTFILES_RUN_ID"
_LOCK_TIMEOUT_SEC = 600.0
_GIT_TIMEOUT_DEFAULT_SEC = 600
_GIT_OUTPUT_RECOVERY_TIMEOUT_SEC = 30
_PROCESS_TREE_WAIT_TIMEOUT_SEC = 5
_GIT_TIMEOUT_ENV = "UPDATE_DOTFILES_GIT_TIMEOUT_SEC"
_LOG_STAGE_TOTAL = 5
_CODEX_AUTO_RESTART_ENV = "DOTFILES_CODEX_DAEMON_AUTO_RESTART"
# 公式READMEが定める停止猶予`shutdownGraceSeconds`の上限300秒に、強制終了と応答の余裕を加える。
_CODEX_DAEMON_STOP_TIMEOUT_SEC = 360
_CODEX_DAEMON_COMMAND_TIMEOUT_SEC = 120
_CODEX_RPC_TIMEOUT_SEC = 10
_CODEX_DAEMON_START_COMMAND = "codex app-server daemon start"
_CODEX_DAEMON_STAGE_TITLE = "Codex管理daemonの一時停止と再起動"

logger = logging.getLogger(__name__)
_current_run_id: str | None = None
_persistent_log_ready = False
_current_stage_title: str | None = None
_last_stderr_tail: str | None = None


def _configure_persistent_log(run_id: str) -> logging.Handler | None:
    """更新診断用のサイズ制限付きログを構成し、構成不能でも更新処理は継続する。"""
    global _persistent_log_ready  # noqa: PLW0603
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            _LOG_PATH,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as error:
        print(f"永続ログを開始できませんでした: {_LOG_PATH}: {error}", file=sys.stderr)
        _persistent_log_ready = False
        return None
    handler.setFormatter(logging.Formatter(f"%(asctime)s run={run_id} %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _persistent_log_ready = True
    return handler


def _finish(returncode: int) -> int:
    """実行終了を記録し、失敗時は診断ログの位置と表示コマンドを案内する。

    同期結果の構造化記録も本関数だけが確定させる。全ての終了経路が本関数を通るため、
    記録の欠落と、成功した段を失敗として残す書き分けの誤りを避けられる。
    """
    logger.info("update-dotfiles終了: exit=%d", returncode)
    if _current_run_id is not None:
        sync_report.write_finish(
            _current_run_id,
            status="succeeded" if returncode == 0 else "failed",
            exit_code=returncode,
            failed_stage=None if returncode == 0 else _current_stage_title,
            stderr_tail=None if returncode == 0 else _last_stderr_tail,
            finished_at=sync_report.now_text(),
        )
    if returncode != 0 and _persistent_log_ready:
        print(f"永続ログ: {_LOG_PATH}", file=sys.stderr)
        print("失敗の詳細は update-dotfiles logs で直近1回の実行ログを表示して確認できる。", file=sys.stderr)
    return returncode


def _child_env() -> dict[str, str]:
    """各工程のサブプロセスへ渡す環境を構成する。

    `MISE_AUTO_INSTALL=0`は、実行ファイル名で起動したコマンドがmiseのshimへ解決された場合に、
    呼び出したコマンドと無関係なツールの自動導入が実行されるのを防ぐ。この自動導入が失敗すると
    shimが非ゼロ終了し、更新処理が最初の工程で止まる。
    post-apply工程が実行する明示的な`mise install`はこの設定の影響を受けないため、
    ツールの導入自体は従来どおり行われる。
    """
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.pop("VIRTUAL_ENV_PROMPT", None)
    env["MISE_AUTO_INSTALL"] = "0"
    env["PYTHONIOENCODING"] = "utf-8:replace"
    if _current_run_id is not None:
        env[_RUN_ID_ENV] = _current_run_id
    user_bin = str(pathlib.Path.home() / ".local" / "bin")
    current_path = env.get("PATH", "")
    path_entries = current_path.split(os.pathsep) if current_path else []
    if user_bin not in path_entries:
        path_entries.append(user_bin)
    env["PATH"] = os.pathsep.join(path_entries)
    return env


def _run_step(
    step_no: int,
    total: int,
    title: str,
    argv: list[str],
    *,
    capture: bool = False,
    show_heading: bool = True,
    log_step_no: int | None = None,
) -> tuple[int, str]:
    """1段を実行し、画面と診断ログへそれぞれの段番号を記録する。

    `capture=False`の段でも標準エラーだけは取得し、段の終了後に親の標準エラーへ転送する。
    同期結果の記録へ失敗した段の標準エラーを残すためである。進捗を表す標準出力は取得せず、
    子プロセスの出力先を親から引き継いだまま保つ。
    `capture=True`時のみ標準出力を文字列で返す。
    """
    global _current_stage_title, _last_stderr_tail  # noqa: PLW0603
    _current_stage_title = title
    _last_stderr_tail = None
    log_step_no = step_no if log_step_no is None else log_step_no
    if show_heading:
        # 子プロセスが同じ標準出力へ直接書くため、見出しを子プロセスの起動前に書き込む。
        # 端末以外（サービスのjournal、ファイル）への出力はブロックバッファで、flushしないと見出しが後段の出力より後に並ぶ。
        print(f"=== [{step_no}/{total}] {title} ===", flush=True)
    logger.info("stage開始: %d/%d %s", log_step_no, _LOG_STAGE_TOTAL, title)
    started_at = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            cwd=_DOTFILES_ROOT,
            check=False,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
        )
    except OSError as error:
        logger.exception("stage起動失敗: %d/%d %s", log_step_no, _LOG_STAGE_TOTAL, title)
        print(f"{title}を開始できませんでした: {error}", file=sys.stderr)
        _last_stderr_tail = sync_report.truncate_tail(str(error))
        return 1, ""
    logger.info(
        "stage終了: %d/%d %s exit=%d duration=%.3f",
        log_step_no,
        _LOG_STAGE_TOTAL,
        title,
        result.returncode,
        time.monotonic() - started_at,
    )
    if result.stderr:
        sys.stderr.write(result.stderr)
        _last_stderr_tail = sync_report.truncate_tail(result.stderr)
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
    ユーザー設定でこの再帰が有効な場合、`git pull`が`git-submodule`を起動する。`git-submodule`は
    POSIX shで実行され、PATH上の`gettext.sh`を読み込むため、そのファイルがbash専用構文を含むと
    構文エラーで終了し、更新処理が最初の工程で止まる。
    `_child_env`の`MISE_AUTO_INSTALL=0`と同じく、工程がユーザー環境の設定を引き継いで停止する経路を抑止する。

    子のセッションとプロセスグループは変更せず、SSH鍵のパスフレーズを制御端末から入力できる状態を保つ。
    `timeout=None`は待機上限を設けない。上限超過時は子を終了する前に子孫を列挙して全て強制終了し、
    出力回収にも上限を設ける。
    """
    global _current_stage_title, _last_stderr_tail  # noqa: PLW0603
    _current_stage_title = "git pull"
    _last_stderr_tail = None
    print(f"=== [{step_no}/{total}] git pull ===", flush=True)
    logger.info("stage開始: %d/%d git pull", step_no, _LOG_STAGE_TOTAL)
    started_at = time.monotonic()
    # 上限超過時に子孫を列挙してから直接子を回収するため、プロセスを明示的に保持する。
    try:
        process = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
            [
                "chezmoi",
                "git",
                f"--source={_DOTFILES_ROOT}",
                "--",
                "-c",
                "submodule.recurse=false",
                "pull",
                "--rebase",
                "--quiet",
            ],
            cwd=_DOTFILES_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
        )
    except OSError as error:
        logger.exception("stage起動失敗: %d/%d git pull", step_no, _LOG_STAGE_TOTAL)
        print(f"git pullを開始できませんでした: {error}", file=sys.stderr)
        _last_stderr_tail = sync_report.truncate_tail(str(error))
        return 1
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
        timeout_message = (
            f"git pullが{timeout}秒以内に完了しなかったため、子孫プロセスを終了しました。"
            f"未完了です。必要に応じて{_GIT_TIMEOUT_ENV}を調整してください。"
        )
        print(timeout_message, file=sys.stderr)
        _last_stderr_tail = sync_report.truncate_tail(f"{stderr}\n{timeout_message}")
        logger.error(
            "stage終了: %d/%d git pull exit=1 timeout=%s duration=%.3f",
            step_no,
            _LOG_STAGE_TOTAL,
            timeout,
            time.monotonic() - started_at,
        )
        return 1
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        stream = sys.stdout if process.returncode == 0 else sys.stderr
        stream.write(stderr)
        _last_stderr_tail = sync_report.truncate_tail(stderr)
    logger.info(
        "stage終了: %d/%d git pull exit=%d duration=%.3f",
        step_no,
        _LOG_STAGE_TOTAL,
        process.returncode,
        time.monotonic() - started_at,
    )
    return process.returncode


def _git_capture(*arguments: str) -> subprocess.CompletedProcess[str]:
    """dotfilesリポジトリでGitを実行し、標準出力と標準エラーを取得する。"""
    return subprocess.run(
        ["git", "-C", str(_DOTFILES_ROOT), *arguments],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
    )


# 更新処理が差分を保持せずHEADへ戻すlockfile（作業ツリーのルートからの相対パス）。
_MISE_LOCK = "mise.lock"


def _git_value(*arguments: str) -> str | None:
    """Gitの成功した単一値を返し、失敗時は診断を転送する。"""
    result = _git_capture(*arguments)
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        return None
    return result.stdout.strip()


def _git_operation_in_progress() -> bool:
    """既存のmergeまたはrebaseが進行中の場合に真を返す。"""
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
    launcher = _SOURCE_ROOT / "agent-toolkit" / "bin" / ("atk.cmd" if os.name == "nt" else "atk")
    try:
        result = subprocess.run(
            [str(launcher), "worktree-stash", "save", f"--label={label}"],
            cwd=_DOTFILES_ROOT,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
        )
    except OSError as error:
        logger.exception("worktree-stash saveの起動に失敗: worktree=%s launcher=%s", _DOTFILES_ROOT, launcher)
        print(f"未コミット内容の退避を開始できませんでした ({_DOTFILES_ROOT}): {error}", file=sys.stderr)
        return None
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


def _discard_mise_lock_changes() -> bool:
    """ルート`mise.lock`のindexと作業ツリーの差分をHEADの内容へ戻す。差分が無ければ何もしない。"""
    status = _git_value("status", "--porcelain=v1", "--", _MISE_LOCK)
    if status is None:
        return False
    return not status or _run_git_change("checkout", "HEAD", "--", _MISE_LOCK)


def _update_git_with_recovery(step_no: int, total: int, *, timeout: int | None) -> int:
    """Git更新を実行し、競合時は復旧参照を保持して上流へ合わせる。"""
    if _git_operation_in_progress():
        print("既存のmergeまたはrebaseが進行中のため、更新を開始しません。", file=sys.stderr)
        return 1
    if not _discard_mise_lock_changes():
        print("mise.lockの差分を破棄できなかったため、更新を中止します。", file=sys.stderr)
        return 1
    branch = _git_value("symbolic-ref", "--quiet", "--short", "HEAD")
    upstream = _git_value("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    original_head = _git_value("rev-parse", "HEAD")
    if not branch or not upstream or not original_head:
        print("現在branch、設定済み上流またはHEADを解決できません。", file=sys.stderr)
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


@dataclasses.dataclass(frozen=True)
class _CodexDaemonPause:
    """`chezmoi apply`の前に停止を試みた管理daemonと、停止の失敗内容。"""

    codex: str
    stop_error: str | None


def _pause_codex_daemon() -> _CodexDaemonPause | None:
    """利用セッションが無く管理daemonだけが稼働する場合に停止し、再起動に必要な情報を返す。

    停止しない場合は`None`を返す。管理daemonが稼働していない場合（更新ループだけが残る場合を含む）は
    停止も再起動も要らない。停止しない理由がある場合は、post-applyの延期と結び付けられるよう表示とログへ残す。
    """
    if sys.platform != "linux":
        return None
    processes = codex_processes.codex_processes()
    if not any(process.role == "managed-daemon" for process in processes):
        return None
    sessions = tuple(process.label for process in processes if process.role == "session")
    reason: str | None = None
    codex = shutil.which("codex", path=_child_env()["PATH"])
    if os.environ.get(_CODEX_AUTO_RESTART_ENV) == "1":
        reason = f"{_CODEX_AUTO_RESTART_ENV}=1が設定されている"
    elif sessions:
        reason = f"Codexの利用セッションが稼働中: {codex_processes.format_running_processes(sessions)}"
    elif codex is None:
        reason = "codex CLIが見つからない"
    else:
        socket_path = _codex_daemon_socket(codex)
        status = None if socket_path is None else _read_remote_control_status(socket_path)
        if status != "disabled":
            reason = f"管理daemonの遠隔接続機能の状態が{status or '判定不能'}"
    if reason is not None or codex is None:
        message = (
            f"Codex管理daemonを停止せずに更新します（{reason}）。"
            "plugin更新または診断ログ復元が延期された場合は、Codexの利用を終えてからupdate-dotfilesを再実行してください。"
        )
        logger.info(message)
        print(message, flush=True)
        return None
    logger.info("Codex管理daemonを一時停止: %s", codex)
    print("Codex管理daemonを一時停止します（更新後に再起動します）。", flush=True)
    stop_error = _run_codex_daemon_command(codex, "stop", _CODEX_DAEMON_STOP_TIMEOUT_SEC)
    if stop_error is not None:
        stop_error = (
            f"Codex管理daemonを停止できませんでした（{stop_error}）。"
            "plugin更新と診断ログ復元が延期された場合は、update-dotfilesを再実行してください。"
        )
        logger.error(stop_error)
        print(stop_error, file=sys.stderr)
    return _CodexDaemonPause(codex=codex, stop_error=stop_error)


def _resume_codex_daemon(pause: _CodexDaemonPause) -> str | None:
    """停止を試みた管理daemonを起動し、失敗時は手動の復帰操作を含む文面を返す。"""
    error = _run_codex_daemon_command(pause.codex, "start", _CODEX_DAEMON_COMMAND_TIMEOUT_SEC)
    if error is None:
        logger.info("Codex管理daemonを再起動")
        print("Codex管理daemonを再起動しました。", flush=True)
        return None
    message = (
        f"Codex管理daemonを再起動できませんでした（{error}）。次のコマンドで起動してください: {_CODEX_DAEMON_START_COMMAND}"
    )
    logger.error(message)
    print(message, file=sys.stderr)
    return message


def _run_codex_daemon_command(codex: str, action: str, timeout: int) -> str | None:
    """`codex app-server daemon <action>`を実行し、失敗時だけ理由を返す。"""
    try:
        result = subprocess.run(
            [codex, "app-server", "daemon", action],
            cwd=_DOTFILES_ROOT,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"{timeout}秒以内に完了しなかった"
    except OSError as error:
        return f"起動できない: {error}"
    logger.info("codex app-server daemon %s: exit=%d stdout=%s", action, result.returncode, result.stdout.strip())
    if result.stderr:
        logger.info("codex app-server daemon %sの標準エラー:\n%s", action, result.stderr)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else ""
        return f"exit {result.returncode}" + (f": {detail}" if detail else "")
    return None


def _codex_daemon_socket(codex: str) -> str | None:
    """`codex app-server daemon version`のJSONから、稼働中の管理daemonの制御socketを返す。"""
    try:
        result = subprocess.run(
            [codex, "app-server", "daemon", "version"],
            cwd=_DOTFILES_ROOT,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
            timeout=_CODEX_DAEMON_COMMAND_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.exception("codex app-server daemon versionの実行に失敗")
        return None
    if result.returncode != 0:
        logger.info("codex app-server daemon version: exit=%d stderr=%s", result.returncode, result.stderr.strip())
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.info("codex app-server daemon versionの出力がJSONでない: %s", result.stdout.strip())
        return None
    if not isinstance(data, dict) or data.get("status") != "running" or not isinstance(data.get("socketPath"), str):
        logger.info("codex app-server daemon versionが稼働中の制御socketを示さない: %s", result.stdout.strip())
        return None
    return data["socketPath"]


def _read_remote_control_status(socket_path: str) -> str | None:
    """管理daemonの制御socketへApp Serverの`remoteControl/status/read`を送り、状態を返す。

    制御socketはUnix domain socket上のWebSocketでJSON-RPCを受ける（公式`app-server-daemon`の
    クライアント実装と同じ接続形）。照会は読み取りだけで、daemonの状態を変えない。
    接続、応答、形式のいずれかが想定と異なる場合は`None`を返し、呼び出し側は停止しない。
    """
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(_CODEX_RPC_TIMEOUT_SEC)
            connection.connect(socket_path)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            connection.sendall(
                (
                    "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                ).encode("ascii")
            )
            reader = connection.makefile("rb")
            if b" 101 " not in reader.readline():
                logger.info("Codex管理daemonの制御socketがWebSocketへ切り替わらない: %s", socket_path)
                return None
            while reader.readline() not in (b"\r\n", b""):
                pass
            initialize = {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "dotfiles-update", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            }
            for message in (initialize, {"method": "initialized"}, {"id": 2, "method": "remoteControl/status/read"}):
                _send_websocket_text(connection, json.dumps(message))
            while (payload := _read_websocket_text(connection, reader)) is not None:
                try:
                    response = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(response, dict) or response.get("id") not in (1, 2):
                    continue
                if "error" in response:
                    logger.info("Codex管理daemonがJSON-RPCの要求を拒否: %s", payload)
                    return None
                if response["id"] == 2:
                    result = response.get("result")
                    status = result.get("status") if isinstance(result, dict) else None
                    return status if isinstance(status, str) else None
    except OSError:
        logger.exception("Codex管理daemonの遠隔接続機能の状態を取得できない: %s", socket_path)
    return None


def _send_websocket_text(connection: socket.socket, text: str) -> None:
    """クライアントからのWebSocketテキストフレームをマスク付きで送る。"""
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x81, 0x80 | length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x81, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", 0x81, 0x80 | 127, length)
    connection.sendall(header + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))


def _read_websocket_text(connection: socket.socket, reader: io.BufferedIOBase) -> str | None:
    """次のテキストメッセージを返し、接続の終了時は`None`を返す。pingにはpongで応じる。"""
    fragments: list[bytes] = []
    while True:
        header = reader.read(2)
        if len(header) < 2:
            return None
        opcode, length = header[0] & 0x0F, header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", reader.read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", reader.read(8))[0]
        mask = reader.read(4) if header[1] & 0x80 else b""
        payload = reader.read(length)
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            connection.sendall(struct.pack("!BB", 0x8A, 0x80 | len(payload)) + b"\0\0\0\0" + payload)
            continue
        if opcode in (0x0, 0x1):
            fragments.append(payload)
            if header[0] & 0x80:
                return b"".join(fragments).decode("utf-8", errors="replace")


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
    parser.add_argument("command", nargs="?", choices=["logs"], help="logs: 直近1回の更新実行の保存ログを表示する")
    return parser.parse_args([] if argv is None else argv)


def _show_logs() -> int:
    """保存済みの直近の更新実行を、複数行レコードと世代境界を保って表示する。"""
    header = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} run=(\S+) \S+ ")
    records: list[tuple[str, str]] = []
    run_id = ""
    for generation in range(_LOG_BACKUP_COUNT, -1, -1):
        path = _LOG_PATH.with_name(f"{_LOG_PATH.name}.{generation}") if generation else _LOG_PATH
        try:
            with path.open(encoding="utf-8") as log_file:
                for line in log_file:
                    match = header.match(line)
                    if match is not None:
                        run_id = match[1]
                    records.append((run_id, line))
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError) as error:
            print(
                f"保存ログを読み取れませんでした: {path}: {error}。読み取り権限とファイルの内容を確認してください。",
                file=sys.stderr,
            )
            return 1
    # 単独post-applyを除き、開始記録が既に削除された更新も残っている実行IDから選ぶ。
    update_ids = {identifier for identifier, _ in records if re.fullmatch(r"\d+-\d+", identifier)}
    if not update_ids:
        print("保存済みの更新ログはありません。")
        return 0
    latest_id = max(update_ids, key=lambda identifier: tuple(map(int, identifier.split("-"))))
    for identifier, line in records:
        if identifier == latest_id:
            sys.stdout.write(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    """更新処理を排他ロック下で直列実行し、最終exit codeを返す。"""
    global _current_run_id, _persistent_log_ready, _current_stage_title, _last_stderr_tail  # noqa: PLW0603
    args = _parse_args(argv)
    if args.command == "logs":
        return _show_logs()
    _current_run_id = f"{time.time_ns()}-{os.getpid()}"
    _current_stage_title = None
    _last_stderr_tail = None
    log_handler = _configure_persistent_log(_current_run_id)
    sync_report.write_start(_current_run_id, sync_report.now_text())
    logger.info("update-dotfiles開始: root=%s", _DOTFILES_ROOT)
    try:
        if log_handler is None:
            _last_stderr_tail = "永続ログを開始できない"
            return _finish(1)
        try:
            git_timeout = _git_timeout()
        except ValueError as error:
            logger.error("git timeout設定が不正: %s", error)
            print(error, file=sys.stderr)
            _last_stderr_tail = sync_report.truncate_tail(str(error))
            return _finish(2)
        total = 4
        lock_dir = _LOCK_PATH.parent
        lock_dir.mkdir(parents=True, exist_ok=True)
        try:
            with filelock.FileLock(str(_LOCK_PATH), timeout=_LOCK_TIMEOUT_SEC):
                returncode = _update_git_with_recovery(1, total, timeout=git_timeout)
                if returncode != 0:
                    return _finish(returncode)

                returncode, _ = _run_step(
                    2,
                    total,
                    "chezmoi init (テンプレート再展開)",
                    ["chezmoi", "init", f"--source={_DOTFILES_ROOT}"],
                )
                if returncode != 0:
                    return _finish(returncode)

                returncode, status_output = _run_step(
                    3,
                    total,
                    "chezmoi status (apply予定のファイル)",
                    ["chezmoi", "status", "-x", "scripts"],
                    capture=True,
                )
                if returncode != 0:
                    return _finish(returncode)
                for line in _filter_apply_pending(status_output):
                    print(line)

                returncode, diff_output = _run_step(
                    4,
                    total,
                    "chezmoi diff (上書き前の差分)",
                    ["chezmoi", "diff", "--no-pager"],
                    capture=True,
                    show_heading=False,
                )
                logger.info("chezmoi diffの出力:\n%s", diff_output)
                if returncode != 0:
                    return _finish(returncode)

                daemon_pause = _pause_codex_daemon()
                try:
                    returncode, _ = _run_step(
                        total,
                        total,
                        "chezmoi apply (post-apply実行)",
                        ["chezmoi", "apply", "--force"],
                        log_step_no=_LOG_STAGE_TOTAL,
                    )
                finally:
                    resume_error = None if daemon_pause is None else _resume_codex_daemon(daemon_pause)
                if returncode != 0:
                    return _finish(returncode)
                stop_error = None if daemon_pause is None else daemon_pause.stop_error
                daemon_errors = [error for error in (stop_error, resume_error) if error is not None]
                if daemon_errors:
                    _current_stage_title = _CODEX_DAEMON_STAGE_TITLE
                    _last_stderr_tail = sync_report.truncate_tail("\n".join(daemon_errors))
                    return _finish(1)
        except filelock.Timeout:
            logger.exception("update-dotfilesロック取得失敗")
            lock_message = (
                f"ロック取得に失敗しました（{_LOCK_TIMEOUT_SEC:.0f}秒待機後もタイムアウト）。"
                "他のupdate-dotfiles実行の完了を待って再実行してください。"
            )
            print(lock_message, file=sys.stderr)
            _last_stderr_tail = sync_report.truncate_tail(lock_message)
            return _finish(1)
        return _finish(0)
    finally:
        if log_handler is not None:
            logger.removeHandler(log_handler)
            log_handler.close()
        _current_run_id = None
        _persistent_log_ready = False
        _current_stage_title = None
        _last_stderr_tail = None


if __name__ == "__main__":
    for output_stream in (sys.stdout, sys.stderr):
        if isinstance(output_stream, io.TextIOWrapper):
            output_stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
