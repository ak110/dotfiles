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

実行の開始時と終了時に、同期結果を`pytools/_internal/sync_report.py`が定める構造化ファイルへ記録する。
次に起動するコーディングエージェントが、失敗した段と標準エラーの末尾からAWIの処理を
完遂できるかを判定するための記録であり、失敗の内容を人間の目視に頼らず残す。
`chezmoi init`と`chezmoi apply`の段は、子が標準出力と標準エラーを継承する。
この2段の同期結果には終了コードを残し、post-applyの詳細はpost-apply自身の記録と永続ログで確認する。

Linuxでは`chezmoi apply`の直前に、Codexの管理daemonだけが稼働し、利用セッションが無い場合に限り、
公開CLI`codex app-server daemon stop`で管理daemonを一時停止する。
post-applyのplugin更新と診断ログ復元が、利用セッションの無い管理daemonを理由に延期されないためである。
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
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator

import filelock
import platformdirs
import psutil

_SOURCE_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SOURCE_ROOT))

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
_update_index: pathlib.Path | None = None


def _configure_persistent_log(run_id: str) -> logging.Handler | None:
    """更新診断用のサイズ制限付きログを構成し、構成不能ならNoneを返す。"""
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
            assert process.stdout is not None
            assert process.stderr is not None
            process.stdout.close()
            process.stderr.close()


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


def _git_value(*arguments: str) -> str | None:
    """Gitの成功した単一値を返し、失敗時は診断を転送する。"""
    result = _git_capture(*arguments)
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        return None
    return result.stdout.strip()


def _git_operation_in_progress() -> bool:
    """対象worktreeの操作状態と、index準備後も存続するGitプロセスを調べる。"""
    for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD", "sequencer"):
        path = _git_value("rev-parse", "--git-path", name)
        if path is None:
            return True
        candidate = pathlib.Path(path)
        if not candidate.is_absolute():
            candidate = _DOTFILES_ROOT / candidate
        if candidate.exists():
            return True
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
            result = subprocess.run(
                ["git", *options, "rev-parse", "--absolute-git-dir"],
                cwd=process.cwd(),
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


def _skip_git_update(reason: str) -> int:
    """未完了の理由と再試行契機を全ての診断先へ残す。"""
    global _current_stage_title, _last_stderr_tail  # noqa: PLW0603
    _current_stage_title = "Git更新の所有確認"
    message = (
        f"{reason}のためGit更新は未完了です。進行中のGit操作が終了してからupdate-dotfilesを再実行してください。"
        "自動更新は次回のタイマー起動で再試行します。"
    )
    _last_stderr_tail = sync_report.truncate_tail(message)
    logger.warning(message)
    print(message, file=sys.stderr)
    return 1


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
        descriptor, name = tempfile.mkstemp(prefix="update-dotfiles-index-", dir=index.parent)
        os.close(descriptor)
        alternate = pathlib.Path(name)
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
        if alternate is not None:
            try:
                current = lock.stat()
                if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                    raise RuntimeError(f"Git更新のロック所有者が変わりました: {lock}")
                if prepared and alternate.exists():
                    os.replace(alternate, index)
            finally:
                alternate.unlink(missing_ok=True)
                alternate.with_name(alternate.name + ".lock").unlink(missing_ok=True)
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
    logger.info("Git状態変更開始: git %s", " ".join(arguments))
    result = _git_capture(*arguments)
    logger.info(
        "git %s: exit=%d stdout:\n%s\nstderr:\n%s", " ".join(arguments), result.returncode, result.stdout, result.stderr
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        (sys.stdout if result.returncode == 0 else sys.stderr).write(result.stderr)
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
            message = (
                f"{display_title}が{_WORKTREE_STASH_TIMEOUT_SEC}秒以内に完了しなかったため、"
                "子孫プロセスを終了して更新を中止しました。"
                "update-dotfiles logsで停止した操作と退避先を確認してください。"
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
            assert process.stdout is not None and process.stderr is not None
            process.stdout.close()
            process.stderr.close()
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
    return restored


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
    if _git_operation_in_progress():
        return _skip_git_update("既存のGit操作が進行中")
    value = _git_value("rev-parse", "--git-path", "index")
    if value is None:
        return _skip_git_update("対象indexを解決できない")
    index = pathlib.Path(value)
    if not index.is_absolute():
        index = _DOTFILES_ROOT / index
    try:
        with _locked_git_index(index):
            if _git_operation_in_progress():
                return _skip_git_update("ロック取得前に開始したGit操作が進行中")
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
        return 1
    if not _discard_mise_lock_changes():
        print(
            "mise.lockの差分を破棄できなかったため、更新を中止します。"
            "git status --short -- mise.lockで状態を確認し、Gitのエラーと書込権限の問題を解消して"
            "update-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        return 1
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
        if stash_ref is not None:
            if _restore_worktree(stash_ref):
                _drop_restored_worktree(stash_ref)
            else:
                print(
                    f"未コミット内容は{stash_ref}から復旧できます。git statusで競合を確認し、"
                    f"競合を解消した作業ツリーでgit stash apply --index {stash_ref}を実行してください。",
                    file=sys.stderr,
                )
        return pull_code
    if pull_code == 0 and (stash_ref is None or _restore_worktree(stash_ref)):
        return 0 if stash_ref is None or _drop_restored_worktree(stash_ref) else 1

    recovery_branch = f"update-dotfiles-recovery-{suffix}"
    if not _run_git_change("branch", recovery_branch, original_head):
        print(
            "復旧用branchを保存できなかったため、自動回復を中止します。"
            f"git branch {recovery_branch} {original_head}で元HEADを保存し、git statusで競合を確認してください。"
            "解消できない場合は、このログをdotfilesの作者へ報告してください。",
            file=sys.stderr,
        )
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

                plugins_root = _DOTFILES_ROOT / "plugins"
                plugins = sorted(plugins_root.iterdir(), key=lambda path: path.name) if plugins_root.exists() else []
                for plugin in plugins:
                    if plugin.is_dir() and (plugin / ".git").exists():
                        returncode = _run_git_pull(1, total, timeout=git_timeout, plugin=plugin)
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
