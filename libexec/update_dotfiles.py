#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15"
# dependencies = ["filelock>=3.30", "platformdirs>=4.0", "psutil"]
# ///
r"""dotfilesリポジトリを最新化するPEP 723スクリプト。

`chezmoi git pull --rebase` → Git管理のdotfilesプラグインを名前順にpull → `chezmoi init`（テンプレート再展開） →
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
（`libexec/update_dotfiles.py`）から`Path(__file__)`起点で解決する
（`Path.home()`起点は`$HOME`と実チェックアウト先の不一致を招くため使わない）。

各段は、後続の段の前提になる段と、失敗しても後続の段の前提を壊さない段に分ける。
前提になる段は、ロックの取得、git timeout設定の検証、dotfiles自身のGit更新（所有の確認・index管理・
退避・pull・競合回復）、`chezmoi init`、`chezmoi apply`であり、失敗したexit codeをそのまま伝播して以降の段を実行しない。
ソースの状態が不確定なまま`chezmoi init`・`chezmoi apply`へ進むと不確定な内容を配備するためである。
前提を壊さない段は、失敗を記録して後続の段を続け、全段の実行後に失敗した段と次の操作の一覧を標準エラーへ表示して終了コード1で終える。
該当する段は、過去の退避データの整理（旧形式の参照は残り、今回の退避は新しい参照へ保存される）、
`mise.lock`の差分の破棄（破棄できなかった差分は未コミット内容として退避・復元される）、
`plugins/`配下の各リポジトリのpull、`chezmoi status`（表示専用）、`chezmoi diff`（表示と記録専用）、
Codex管理daemonの一時停止と再起動、永続ログの開始（開始できない場合は標準出力・標準エラーだけで続ける）である。
段を追加するときは、失敗した状態で後続の段へ進むと不確定な内容を配備するかで分類する。
Gitが進捗を標準エラー出力へ書く場合も、Git更新段が正常終了した場合は
`update-dotfiles`の標準出力へ転送する。失敗時はGitの標準エラー出力を維持する。
各段のサブプロセスへ`MISE_AUTO_INSTALL=0`を渡し、miseのshimが呼び出したコマンドと
無関係なツールを自動導入して更新処理を停止させる経路を抑止する。親プロセスの
仮想環境は子へ引き継がず、各工程が自身の設定から環境を解決する。
取得したchezmoi出力は、プラットフォームのロケール設定が決める文字コードによらずUTF-8として厳格にデコードする。
git pull工程は`UPDATE_DOTFILES_GIT_TIMEOUT_SEC`秒で打ち切る。未設定時は600秒、
`0`は上限なしとし、負数または整数でない値は終了コード2で拒否する。

実行の開始時と終了時に、同期結果を`pytools/_internal/sync_report.py`が定める構造化ファイルへ記録する。
次に起動するコーディングエージェントが、失敗した段と標準エラーの末尾からAWIの処理を
完遂できるかを判定するための記録であり、失敗の内容を人間の目視に頼らず残す。
`chezmoi init`と`chezmoi apply`の段は、子が標準出力と標準エラーを継承する。
この2段の同期結果には終了コードを残し、post-applyの詳細はpost-apply自身の記録と永続ログで確認する。

Linuxでは`chezmoi apply`の直前に、Codexの管理daemonだけが稼働し、利用セッションが無い場合に限り、
公開CLI`codex app-server daemon stop`で管理daemonを一時停止する。
post-applyの診断ログ復元と不要pluginの除去が、利用セッションの無い管理daemonを理由に延期されないためである。
停止を試みた場合は`chezmoi apply`の成否にかかわらず`codex app-server daemon start`で起動状態を戻し、
停止または再起動の失敗を終了コードと同期結果へ反映する。利用セッションが残る場合と
`DOTFILES_CODEX_DAEMON_AUTO_RESTART=1`の場合は停止しない。
"""

# pylint: disable=global-statement

import argparse
import contextlib
import dataclasses
import io
import logging
import logging.handlers
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator
from types import FrameType

import filelock
import platformdirs
import psutil

_SOURCE_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SOURCE_ROOT))
sys.path.insert(0, str(_SOURCE_ROOT / "agent-toolkit"))

from agent_toolkit._common import dotfiles_mode  # noqa: E402  # pylint: disable=wrong-import-position

from pytools._internal import codex_processes, sync_report  # noqa: E402  # pylint: disable=wrong-import-position

_DOTFILES_ROOT = _SOURCE_ROOT
_LOCK_PATH = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "locks" / "update-dotfiles.lock"
_LOG_PATH = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "update-dotfiles.log"
_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUP_COUNT = 3
_RUN_ID_ENV = "UPDATE_DOTFILES_RUN_ID"
_LOCK_TIMEOUT_SEC = 600.0
_GIT_TIMEOUT_DEFAULT_SEC = 600
_GIT_OUTPUT_RECOVERY_TIMEOUT_SEC = 30
_WORKTREE_STASH_TIMEOUT_SEC = 120
_PROCESS_TREE_WAIT_TIMEOUT_SEC = 5
_GIT_TIMEOUT_ENV = "UPDATE_DOTFILES_GIT_TIMEOUT_SEC"
_LOG_STAGE_TOTAL = 5
_CODEX_AUTO_RESTART_ENV = "DOTFILES_CODEX_DAEMON_AUTO_RESTART"
# 公式READMEが定める停止猶予`shutdownGraceSeconds`の上限300秒に、強制終了と応答の余裕を加える。
_CODEX_DAEMON_STOP_TIMEOUT_SEC = 360
_CODEX_DAEMON_COMMAND_TIMEOUT_SEC = 120
_CODEX_DAEMON_START_COMMAND = "codex app-server daemon start"
_CODEX_DAEMON_STAGE_TITLE = "Codex管理daemonの一時停止と再起動"

logger = logging.getLogger(__name__)
_current_run_id: str | None = None
_persistent_log_ready = False
_current_stage_title: str | None = None
_last_stderr_tail: str | None = None
_continued_failures: list[_ContinuedFailure] = []
_update_index: pathlib.Path | None = None


@dataclasses.dataclass(frozen=True)
class _ContinuedFailure:
    """失敗しても後続の段を続けた段と、その段の次の操作。"""

    title: str
    action: str
    detail: str | None


def _continue_after_failure(title: str, action: str, detail: str | None = None) -> None:
    """後続の段の前提を壊さない段の失敗を記録し、呼び出し側が後続の段を続けられるようにする。"""
    logger.error("stage失敗のまま続行: %s", title)
    _continued_failures.append(_ContinuedFailure(title, action, detail))


@contextlib.contextmanager
def _configure_persistent_log(run_id: str) -> Generator[logging.Handler | None]:
    """更新診断用のログを開き、終了時に登録を外して閉じる。構成不能時はNoneを渡す。"""
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
        print(
            f"永続ログを開始できませんでした: {_LOG_PATH}: {error}。"
            f"{_LOG_PATH.parent}の書込権限と空き容量を確認してupdate-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        _persistent_log_ready = False
        yield None
        return
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(handler.close)
        cleanup.callback(logger.removeHandler, handler)
        handler.setFormatter(logging.Formatter(f"%(asctime)s run={run_id} %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _persistent_log_ready = True
        yield handler


def _finish(returncode: int) -> int:
    """実行終了を記録し、失敗時は次の操作と診断ログの位置・表示コマンドを1回だけ案内する。

    同期結果の構造化記録も本関数だけが確定させる。全ての終了経路で本関数を実行するため、
    記録の欠落と、成功した段を失敗として残す書き分けの誤りを避けられる。
    """
    if _continued_failures:
        returncode = _report_continued_failures(returncode)
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
    if returncode != 0:
        # post-applyは`update-dotfiles`経由の実行では失敗後の案内を出さないため、次の操作はここで1回だけ示す。
        print("次の操作: 失敗理由を確認し、update-dotfilesを再実行してください。", file=sys.stderr)
        if _persistent_log_ready:
            print(f"永続ログ: {_LOG_PATH}", file=sys.stderr)
            print("失敗の詳細は update-dotfiles logs で直近1回の実行ログを表示して確認できる。", file=sys.stderr)
    return returncode


def _report_continued_failures(returncode: int) -> int:
    """続行した段の失敗の一覧を標準エラーへ表示し、同期記録へ残す内容を整える。

    続行した失敗だけで終えた場合は終了コード1にし、記録する段と標準エラーの末尾を続行した失敗の一覧から作成する。
    後続の前提になる段が失敗した場合は、その段の記録を保ち、一覧は標準エラーと永続ログへ残す。
    """
    global _current_stage_title, _last_stderr_tail  # noqa: PLW0603
    lines = ["失敗しても後続の段を続けた段:"]
    for failure in _continued_failures:
        lines.append(f"- {failure.title}: {failure.action}")
    print("\n".join(lines), file=sys.stderr)
    logger.error("続行した失敗: %s", " / ".join(failure.title for failure in _continued_failures))
    if returncode == 0:
        details = [f"{failure.title}: {failure.detail}" if failure.detail else failure.title for failure in _continued_failures]
        _current_stage_title = " / ".join(failure.title for failure in _continued_failures)
        _last_stderr_tail = sync_report.truncate_tail("\n".join(details))
        return 1
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


def _git_env() -> dict[str, str]:
    """更新区間のGitと退避子だけへ代替indexを渡す。"""
    env = _child_env()
    if _update_index is not None:
        env["GIT_INDEX_FILE"] = str(_update_index)
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

    `capture=False`の段は、子へ両ストリームを継承し、書き込み順と出力先を保つ。
    `capture=True`時は標準出力を文字列で返し、標準エラーは段の終了後に親の標準エラーへ転送する。
    """
    global _current_stage_title, _last_stderr_tail  # noqa: PLW0603
    _current_stage_title = title
    _last_stderr_tail = None
    log_step_no = step_no if log_step_no is None else log_step_no
    if show_heading:
        # 子の出力より前に並ぶよう、見出しを子プロセスの起動前に書き込む。
        # 端末以外（サービスのjournal、ファイル）への出力はブロックバッファで、flushしないと見出しが後段の出力より後に並ぶ。
        print(f"=== [{step_no}/{total}] {title} ===", flush=True)
    logger.info("stage開始: %d/%d %s", log_step_no, _LOG_STAGE_TOTAL, title)
    started_at = time.monotonic()
    try:
        if capture:
            result = subprocess.run(
                argv,
                cwd=_DOTFILES_ROOT,
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env=_child_env(),
            )
            returncode, stdout, output_tail = result.returncode, result.stdout, result.stderr
            if result.stderr:
                sys.stderr.write(result.stderr)
        else:
            result = subprocess.run(argv, cwd=_DOTFILES_ROOT, check=False, env=_child_env())
            returncode, output_tail = result.returncode, ""
            stdout = ""
    except OSError as error:
        logger.exception("stage起動失敗: %d/%d %s", log_step_no, _LOG_STAGE_TOTAL, title)
        print(
            f"{title}を開始できませんでした: {error}。"
            "chezmoiとGitがPATH上で起動できることを確認してupdate-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        _last_stderr_tail = sync_report.truncate_tail(str(error))
        return 1, ""
    logger.info(
        "stage終了: %d/%d %s exit=%d duration=%.3f",
        log_step_no,
        _LOG_STAGE_TOTAL,
        title,
        returncode,
        time.monotonic() - started_at,
    )
    if output_tail:
        _last_stderr_tail = sync_report.truncate_tail(output_tail)
    return returncode, stdout


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


def _run_git_pull(
    step_no: int, total: int, *, timeout: int | None = _GIT_TIMEOUT_DEFAULT_SEC, plugin: pathlib.Path | None = None
) -> int:
    """Git更新段を実行し、正常終了時の出力を標準出力へ正規化する。

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
    global _current_stage_title, _last_stderr_tail  # noqa: PLW0603
    title = "git pull" if plugin is None else f"dotfilesプラグイン {plugin.name}: git pull"
    _current_stage_title = title
    _last_stderr_tail = None
    print(f"=== [{step_no}/{total}] {title} ===", flush=True)
    logger.info("stage開始: %d/%d %s", step_no, _LOG_STAGE_TOTAL, title)
    started_at = time.monotonic()
    # Windowsではreader threadがpipeを閉じる。主スレッドでpipeのロックを待つPopen.__exit__を避ける。
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
            ]
            if plugin is None
            else ["git", "-C", str(plugin), "-c", "submodule.recurse=false", "pull", "--rebase"],
            cwd=_DOTFILES_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=_git_env() if plugin is None else _child_env(),
        )
    except OSError as error:
        logger.exception("stage起動失敗: %d/%d %s", step_no, _LOG_STAGE_TOTAL, title)
        print(
            f"{title}を開始できませんでした: {error}。"
            "chezmoi --versionとgit --versionの実行に成功する状態にしてupdate-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        _last_stderr_tail = sync_report.truncate_tail(f"{title}: {error}")
        return 1
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout) if timeout is not None else process.communicate()
        except KeyboardInterrupt:
            _kill_process_tree(process)
            process.wait(timeout=_PROCESS_TREE_WAIT_TIMEOUT_SEC)
            raise
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
                f"{title}が{timeout}秒以内に完了しなかったため、子孫プロセスを終了しました。"
                f"未完了です。通信とSSH認証の問題を解消してupdate-dotfilesを再実行してください。"
                f"待機上限を延ばす場合は{_GIT_TIMEOUT_ENV}へ1200（秒）を設定して再実行してください。"
            )
            print(timeout_message, file=sys.stderr)
            _last_stderr_tail = sync_report.truncate_tail(f"{title}\n{stdout}\n{stderr}\n{timeout_message}")
            logger.error(
                "stage終了: %d/%d %s exit=1 timeout=%s duration=%.3f stdout:\n%s\nstderr:\n%s",
                step_no,
                _LOG_STAGE_TOTAL,
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
            _last_stderr_tail = sync_report.truncate_tail(stderr)
        logger.info("%s stdout:\n%s\nstderr:\n%s", title, stdout, stderr)
        if plugin is not None and process.returncode != 0:
            _last_stderr_tail = sync_report.truncate_tail(f"{stdout}\n{stderr}\n{title}: exit {process.returncode}")
        logger.info(
            "stage終了: %d/%d %s exit=%d duration=%.3f",
            step_no,
            _LOG_STAGE_TOTAL,
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
    argv = ["git", "-C", str(_DOTFILES_ROOT), *arguments]
    with subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=_git_env(),
    ) as process:
        try:
            stdout, stderr = process.communicate()
        except KeyboardInterrupt:
            _kill_process_tree(process)
            stdout, stderr = process.communicate(timeout=_GIT_OUTPUT_RECOVERY_TIMEOUT_SEC)
            logger.warning("Git操作を中断: %s stdout:\n%s\nstderr:\n%s", argv, stdout, stderr)
            raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


# 更新処理が差分を保持せずHEADへ戻すlockfile（作業ツリーのルートからの相対パス）。
_MISE_LOCK = "mise.lock"


def _git_value(*arguments: str, strip: bool = True) -> str | None:
    """Gitの成功した単一値を返し、失敗時は診断を転送する。"""
    result = _git_capture(*arguments)
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        return None
    return result.stdout.strip() if strip else result.stdout


def _git_operation_in_progress() -> str | bool:
    """対象worktreeの操作状態と、index準備後も存続するGitプロセスを調べる。"""
    for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD", "sequencer"):
        path = _git_value("rev-parse", "--git-path", name)
        if path is None:
            return True
        candidate = pathlib.Path(path)
        if not candidate.is_absolute():
            candidate = _DOTFILES_ROOT / candidate
        if candidate.exists():
            return name
    git_dir = _git_value("rev-parse", "--absolute-git-dir")
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
            env = _child_env()
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
                cwd=_DOTFILES_ROOT,
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
    global _current_stage_title, _last_stderr_tail  # noqa: PLW0603
    _current_stage_title = "Git更新の所有確認"
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
    message = f"{reason}のためGit更新は未完了です。{action}自動更新は次回のタイマー起動で再試行します。"
    _last_stderr_tail = sync_report.truncate_tail(message)
    logger.warning(message)
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
    global _update_index  # noqa: PLW0603
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
        _update_index = alternate
        prepared = True
        yield
    finally:
        _update_index = None
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
    path = _git_value("rev-parse", "--git-path", name)
    if path is None:
        return False
    candidate = pathlib.Path(path)
    if not candidate.is_absolute():
        candidate = _DOTFILES_ROOT / candidate
    return candidate.exists()


def _run_git_change(*arguments: str) -> bool:
    """単一のGit状態変更を実行し、失敗時の診断を転送する。"""
    global _last_stderr_tail  # noqa: PLW0603
    logger.info("Git状態変更開始: git %s", " ".join(arguments))
    result = _git_capture(*arguments)
    logger.info(
        "git %s: exit=%d stdout:\n%s\nstderr:\n%s", " ".join(arguments), result.returncode, result.stdout, result.stderr
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        (sys.stdout if result.returncode == 0 else sys.stderr).write(result.stderr)
    if result.returncode != 0:
        _last_stderr_tail = sync_report.truncate_tail(
            f"git {' '.join(arguments)}: exit={result.returncode}\n{result.stdout}\n{result.stderr}"
        )
    return result.returncode == 0


def _run_worktree_stash(*arguments: str) -> subprocess.CompletedProcess[str] | None:
    """同じcheckoutの退避CLIを有限時間で実行し、子孫と出力の回収にも上限を設ける。"""
    global _current_stage_title, _last_stderr_tail  # noqa: PLW0603
    launcher = _SOURCE_ROOT / "libexec" / "update_dotfiles_stash.py"
    argv = [sys.executable, str(launcher), *arguments]
    title = "worktree-stash " + " ".join(arguments)
    display_title = {
        "save": "未コミット変更の一時保存",
        "drop": "復元済みの一時保存データの回収",
        "protect-old": "過去の退避データの整理",
    }.get(arguments[0], "退避データの保護")
    _current_stage_title = title
    _last_stderr_tail = None
    if arguments[0] != "protect-old":
        print(f"{display_title}を行っています。", flush=True)
    started_at = time.monotonic()
    logger.info("退避操作開始: %s", title)
    # Windowsではreader threadへpipeの解放を任せ、Popen.__exit__による主スレッドのcloseを避ける。
    try:
        process = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
            argv,
            cwd=_DOTFILES_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=_git_env(),
        )
    except OSError as error:
        logger.exception("worktree-stashの起動に失敗: worktree=%s launcher=%s", _DOTFILES_ROOT, launcher)
        print(
            f"未コミット内容の退避操作を開始できませんでした ({_DOTFILES_ROOT}): {error}。"
            f"{launcher}の実在と読取権限を確認し、update-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        _last_stderr_tail = sync_report.truncate_tail(f"{title}: {error}")
        return None
    try:
        try:
            stdout, stderr = process.communicate(timeout=_WORKTREE_STASH_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=_GIT_OUTPUT_RECOVERY_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            outcome = (
                "子孫プロセスを終了しました。"
                if arguments[0] == "protect-old"
                else "子孫プロセスを終了して更新を中止しました。"
            )
            message = (
                f"{display_title}が{_WORKTREE_STASH_TIMEOUT_SEC}秒以内に完了しなかったため、"
                f"{outcome}update-dotfiles logsで停止した操作と退避先を確認してください。"
            )
            _last_stderr_tail = sync_report.truncate_tail(f"{stdout}\n{stderr}\n{message}")
            logger.error(
                "退避操作timeout: %s duration=%.3f stdout=%s stderr=%s", title, time.monotonic() - started_at, stdout, stderr
            )
            if stdout:
                sys.stdout.write(stdout)
            if stderr:
                sys.stderr.write(stderr)
            print(message, file=sys.stderr)
            return None
        except KeyboardInterrupt:
            _kill_process_tree(process)
            process.wait(timeout=_PROCESS_TREE_WAIT_TIMEOUT_SEC)
            raise
    finally:
        # Windowsのreader threadがpipeを保持している場合、主スレッドのcloseも待機し得る。
        if os.name != "nt":
            with contextlib.ExitStack() as cleanup:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        cleanup.callback(stream.close)
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    logger.info(
        "退避操作終了: %s exit=%d duration=%.3f stdout=%s stderr=%s",
        title,
        result.returncode,
        time.monotonic() - started_at,
        stdout,
        stderr,
    )
    if result.returncode != 0:
        _last_stderr_tail = sync_report.truncate_tail(f"{title}: exit {result.returncode}\n{stdout}\n{stderr}")
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return None
    return result


def _save_worktree(label: str) -> str | None:
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
    logger.info("未コミット内容の退避先: %s", ref)
    print("未コミット変更を一時保存しました。更新後に復元します。")
    return ref


def _restore_worktree(ref: str) -> bool:
    """worktree固有refからindexを含む未コミット内容を復元する。"""
    restored = _run_git_change("stash", "apply", "--index", ref)
    if restored:
        print("一時保存していた未コミット変更を復元しました。")
    else:
        _record_git_recovery(
            f"未コミット内容の復元に失敗しました。未コミット内容は{ref}から復旧できます。"
            "git statusで競合を確認し、競合を解消した作業ツリーで"
            f"git stash apply --index {ref}を実行してください。"
        )
    return restored


def _record_git_recovery(message: str) -> None:
    """復元・回復の結果と次の操作を画面、永続ログ、同期結果へ残す。"""
    global _last_stderr_tail  # noqa: PLW0603
    logger.warning(message)
    _last_stderr_tail = sync_report.truncate_tail(f"{_last_stderr_tail or ''}\n{message}".strip())
    print(message, file=sys.stderr)


def _drop_restored_worktree(ref: str) -> bool:
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


def _protect_old_worktree_stashes() -> bool:
    """旧版の未保護退避を1回の子起動で移行し、保護済みの退避は再処理しない。"""
    result = _run_worktree_stash("protect-old")
    if result is None:
        return False
    if result.stdout.strip():
        # 個別refの成功行は永続ログに保持し、画面へは最後の移行結果だけを出力する。
        print(result.stdout.strip().splitlines()[-1], flush=True)
    return True


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
    """最初の変更前に所有を確認し、Git更新区間を標準indexロックで保護する。"""
    operation = _git_operation_in_progress()
    if operation:
        return _skip_git_update("既存のGit操作が進行中", operation)
    value = _git_value("rev-parse", "--git-path", "index")
    if value is None:
        return _skip_git_update("対象indexを解決できない")
    index = pathlib.Path(value)
    if not index.is_absolute():
        index = _DOTFILES_ROOT / index
    try:
        with _git_update_signals(), _locked_git_index(index):
            operation = _git_operation_in_progress()
            if operation:
                return _skip_git_update("ロック取得前に開始したGit操作が進行中", operation)
            return _update_owned_git(step_no, total, timeout=timeout)
    except FileExistsError:
        return _skip_git_update("対象indexの標準ロックが使用中")
    except OSError as error:
        logger.exception("Git更新のindex管理に失敗")
        return _skip_git_update(f"対象indexの管理に失敗: {error}")


def _update_owned_git(step_no: int, total: int, *, timeout: int | None) -> int:
    """所有区間内で更新し、競合時は復旧参照を保持して上流へ合わせる。"""
    if _update_index is None:
        raise RuntimeError("Git更新にはindexの所有が必要です")
    if not _protect_old_worktree_stashes():
        print(
            "過去の退避データを確認できませんでしたが、更新を続けます。旧形式の参照は変更されず残り、今回の退避は新しい参照へ保存されます。",
            file=sys.stderr,
        )
        _continue_after_failure(
            "過去の退避データの整理",
            "git statusとupdate-dotfiles logsで原因を確かめ、解消しない場合はログを添えて報告してください。",
            _last_stderr_tail,
        )
    if not _discard_mise_lock_changes():
        print(
            "mise.lockの差分を破棄できませんでしたが、更新を続けます。破棄できなかった差分は未コミット内容として退避・復元されます。",
            file=sys.stderr,
        )
        _continue_after_failure(
            "mise.lockの差分の破棄",
            "git status --short -- mise.lockで状態を確認し、Gitのエラーと書込権限の問題を解消して"
            "update-dotfilesを再実行してください。",
            _last_stderr_tail,
        )
    branch = _git_value("symbolic-ref", "--quiet", "--short", "HEAD")
    upstream = _git_value("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    original_head = _git_value("rev-parse", "HEAD")
    if not branch or not upstream or not original_head:
        print(
            "現在branch、設定済み上流またはHEADを解決できません。git statusとgit branch -vvで状態を確認し、"
            "branchを選びgit branch --set-upstream-to=<remote>/<branch>で上流を設定して"
            "update-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        return 1
    status = _git_value("status", "--porcelain=v1", "--untracked-files=all")
    if status is None:
        return 1
    untracked_output = _git_value("ls-files", "-z", "--others", "--exclude-standard", strip=False)
    if untracked_output is None:
        return 1
    untracked = tuple(path for path in untracked_output.split("\0") if path)
    suffix = f"{time.time_ns()}-{os.getpid()}"
    stash_ref = _save_worktree(f"update-dotfiles-{suffix}") if status else None
    if status and stash_ref is None:
        return 1

    pull_code = _run_git_pull(step_no, total, timeout=timeout)
    rebase_in_progress = any(_git_path_exists(name) for name in ("rebase-merge", "rebase-apply"))
    if pull_code != 0 and not rebase_in_progress:
        if stash_ref is not None and _restore_worktree(stash_ref):
            _drop_restored_worktree(stash_ref)
        return pull_code
    if pull_code == 0 and (stash_ref is None or _restore_worktree(stash_ref)):
        return 0 if stash_ref is None or _drop_restored_worktree(stash_ref) else 1

    recovery_branch = f"update-dotfiles-recovery-{suffix}"
    if not _run_git_change("branch", recovery_branch, original_head):
        _record_git_recovery(
            "復旧用branchを保存できなかったため、自動回復を中止します。"
            f"git branch {recovery_branch} {original_head}で元HEADを保存し、git statusで競合を確認してください。"
            "解消できない場合は、このログをdotfilesの作者へ報告してください。",
        )
        return 1
    if rebase_in_progress and not _run_git_change("rebase", "--abort"):
        return 1
    if not _run_git_change("reset", "--hard", upstream):
        return 1
    if not _clean_saved_untracked(untracked):
        return 1
    _record_git_recovery(
        f"競合から回復し、{branch}を{upstream}へ合わせました。"
        f"元のcommitは{recovery_branch}、未コミット内容は{stash_ref or 'なし'}から復旧できます。"
        "更新後の内容を使う場合は対応不要です。元のcommitへ戻す場合は"
        f"git switch {recovery_branch}を実行してください。"
        + (f"未コミット内容を戻す場合はgit stash apply --index {stash_ref}を実行してください。" if stash_ref else "")
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
    停止も再起動も要らない。実際の延期の案内は処置の要否を判定するpost-applyへ委ねる。
    """
    if sys.platform != "linux":
        return None
    processes = codex_processes.codex_processes()
    if not any(process.role == "managed-daemon" for process in processes):
        return None
    sessions = tuple(process.label for process in processes if process.role == "session")
    codex = shutil.which("codex", path=_child_env()["PATH"])
    if os.environ.get(_CODEX_AUTO_RESTART_ENV) == "1" or sessions or codex is None:
        return None
    logger.info("Codex管理daemonを一時停止: %s", codex)
    print("Codex管理daemonを一時停止します（更新後に再起動します）。", flush=True)
    stop_error = _run_codex_daemon_command(codex, "stop", _CODEX_DAEMON_STOP_TIMEOUT_SEC)
    if stop_error is not None:
        stop_error = (
            f"Codex管理daemonを停止できませんでした（{stop_error}）。"
            "診断ログ復元または不要pluginの除去が延期された場合は、update-dotfilesを再実行してください。"
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
    parser.add_argument("--mode", choices=dotfiles_mode.MODES, help="導入モードを保存して適用する")
    parser.add_argument("--prune", action="store_true", help="現在のモードで不要なツールとキャッシュを削除する")
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
                f"保存ログを読み取れませんでした: {path}: {error}。"
                "このファイルの読取権限を付与し、文字コードの問題があれば元ファイルを退避してUTF-8で保存し直してから"
                "update-dotfiles logsを再実行してください。",
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
    _continued_failures.clear()
    with _configure_persistent_log(_current_run_id) as log_handler:
        try:
            sync_report.write_start(_current_run_id, sync_report.now_text())
            logger.info("update-dotfiles開始: root=%s", _DOTFILES_ROOT)
            if log_handler is None:
                _continue_after_failure(
                    "永続ログの開始",
                    f"{_LOG_PATH.parent}の書込権限と空き容量を確認してupdate-dotfilesを再実行してください。",
                    "永続ログを開始できない",
                )
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
                    dotfiles_mode.select(args.mode)
                    if args.prune:
                        os.environ["DOTFILES_PRUNE"] = "1"
                    returncode = _update_git_with_recovery(1, total, timeout=git_timeout)
                    if returncode != 0:
                        return _finish(returncode)

                    plugins_root = _DOTFILES_ROOT / "plugins"
                    plugins = sorted(plugins_root.iterdir(), key=lambda path: path.name) if plugins_root.exists() else []
                    for plugin in plugins:
                        if plugin.is_dir() and (plugin / ".git").exists():
                            returncode = _run_git_pull(1, total, timeout=git_timeout, plugin=plugin)
                            if returncode != 0:
                                _continue_after_failure(
                                    _current_stage_title or f"dotfilesプラグイン {plugin.name}: git pull",
                                    f"{plugin}で git status と git pull --rebase を実行して原因を確かめてください。",
                                    _last_stderr_tail,
                                )

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
                        _continue_after_failure(
                            "chezmoi status (apply予定のファイル)",
                            "表示専用の段であり、chezmoi applyは続けます。"
                            "chezmoi statusを単独で実行して原因を確かめてください。",
                            _last_stderr_tail,
                        )
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
                        _continue_after_failure(
                            "chezmoi diff (上書き前の差分)",
                            "差分の記録だけの段であり、chezmoi applyは続けます。"
                            "chezmoi diffを単独で実行して原因を確かめてください。",
                            _last_stderr_tail,
                        )

                    daemon_pause = None if dotfiles_mode.is_minimal() and args.prune else _pause_codex_daemon()
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
                        _continue_after_failure(
                            _CODEX_DAEMON_STAGE_TITLE,
                            "Codex管理daemonの状態を確認し、停止していれば codex app-server daemon start を実行してください。",
                            "\n".join(daemon_errors),
                        )
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
            _current_run_id = None
            _persistent_log_ready = False
            _current_stage_title = None
            _last_stderr_tail = None
            _continued_failures.clear()


if __name__ == "__main__":
    for output_stream in (sys.stdout, sys.stderr):
        if isinstance(output_stream, io.TextIOWrapper):
            output_stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
