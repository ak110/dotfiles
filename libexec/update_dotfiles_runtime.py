"""update_dotfilesの更新状態・環境とログ。"""

# pylint: disable=global-statement

import contextlib
import dataclasses
import logging
import logging.handlers
import os
import pathlib
import sys
from collections.abc import Generator

import platformdirs

from pytools._internal import sync_report

SOURCE_ROOT = pathlib.Path(__file__).resolve().parent.parent

DOTFILES_ROOT = SOURCE_ROOT

LOCK_PATH = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "locks" / "update-dotfiles.lock"

LOG_PATH = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "update-dotfiles.log"

_LOG_MAX_BYTES = 2 * 1024 * 1024

LOG_BACKUP_COUNT = 3

_RUN_ID_ENV = "UPDATE_DOTFILES_RUN_ID"

LOCK_TIMEOUT_SEC = 600.0

GIT_TIMEOUT_DEFAULT_SEC = 600

GIT_OUTPUT_RECOVERY_TIMEOUT_SEC = 30

WORKTREE_STASH_TIMEOUT_SEC = 120

PROCESS_TREE_WAIT_TIMEOUT_SEC = 5

GIT_TIMEOUT_ENV = "UPDATE_DOTFILES_GIT_TIMEOUT_SEC"

LOG_STAGE_TOTAL = 5

# 公式READMEが定める停止猶予`shutdownGraceSeconds`の上限300秒に、強制終了と応答の余裕を加える。
CODEX_DAEMON_STOP_TIMEOUT_SEC = 360

CODEX_DAEMON_COMMAND_TIMEOUT_SEC = 120

CODEX_DAEMON_START_COMMAND = "codex app-server daemon start"

CODEX_DAEMON_STAGE_TITLE = "Codex管理daemonの一時停止と再起動"

logger = logging.getLogger(__name__)

current_run_id: str | None = None

persistent_log_ready = False

current_stage_title: str | None = None

last_stderr_tail: str | None = None

continued_failures: list[_ContinuedFailure] = []

update_index: pathlib.Path | None = None


@dataclasses.dataclass(frozen=True)
class _ContinuedFailure:
    """失敗しても後続の段を続けた段と、その段の次の操作。"""

    title: str
    action: str
    detail: str | None


def continue_after_failure(title: str, action: str, detail: str | None = None) -> None:
    """後続の段の前提を壊さない段の失敗を記録し、呼び出し側が後続の段を続けられるようにする。"""
    logger.error("stage失敗のまま続行: %s", title)
    continued_failures.append(_ContinuedFailure(title, action, detail))


@contextlib.contextmanager
def configure_persistent_log(run_id: str) -> Generator[logging.Handler | None]:
    """更新診断用のログを開き、終了時に登録を外して閉じる。構成不能時はNoneを渡す。"""
    global persistent_log_ready  # noqa: PLW0603
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as error:
        print(
            f"永続ログを開始できませんでした: {LOG_PATH}: {error}。"
            f"{LOG_PATH.parent}の書込権限と空き容量を確認してupdate-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        persistent_log_ready = False
        yield None
        return
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(handler.close)
        cleanup.callback(logger.removeHandler, handler)
        handler.setFormatter(logging.Formatter(f"%(asctime)s run={run_id} %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        persistent_log_ready = True
        yield handler


def finish(returncode: int) -> int:
    """実行終了を記録し、失敗時は次の操作と診断ログの位置・表示コマンドを1回だけ案内する。

    同期結果の構造化記録も本関数だけが確定させる。全ての終了経路で本関数を実行するため、
    記録の欠落と、成功した段を失敗として残す書き分けの誤りを避けられる。
    """
    if continued_failures:
        returncode = _report_continued_failures(returncode)
    logger.info("update-dotfiles終了: exit=%d", returncode)
    if current_run_id is not None:
        sync_report.write_finish(
            current_run_id,
            status="succeeded" if returncode == 0 else "failed",
            exit_code=returncode,
            failed_stage=None if returncode == 0 else current_stage_title,
            stderr_tail=None if returncode == 0 else last_stderr_tail,
            finished_at=sync_report.now_text(),
        )
    if returncode != 0:
        # post-applyは`update-dotfiles`経由の実行では失敗後の案内を出さないため、次の操作はここで1回だけ示す。
        print("次の操作: 失敗理由を確認し、update-dotfilesを再実行してください。", file=sys.stderr)
        if persistent_log_ready:
            print(f"永続ログ: {LOG_PATH}", file=sys.stderr)
            print("失敗の詳細は update-dotfiles logs で直近1回の実行ログを表示して確認できる。", file=sys.stderr)
    return returncode


def _report_continued_failures(returncode: int) -> int:
    """続行した段の失敗の一覧を標準エラーへ表示し、同期記録へ残す内容を整える。

    続行した失敗だけで終えた場合は終了コード1にし、記録する段と標準エラーの末尾を続行した失敗の一覧から作成する。
    後続の前提になる段が失敗した場合は、その段の記録を保ち、一覧は標準エラーと永続ログへ残す。
    """
    global current_stage_title, last_stderr_tail  # noqa: PLW0603
    lines = ["失敗しても後続の段を続けた段:"]
    for failure in continued_failures:
        lines.append(f"- {failure.title}: {failure.action}")
    print("\n".join(lines), file=sys.stderr)
    logger.error("続行した失敗: %s", " / ".join(failure.title for failure in continued_failures))
    if returncode == 0:
        details = [f"{failure.title}: {failure.detail}" if failure.detail else failure.title for failure in continued_failures]
        current_stage_title = " / ".join(failure.title for failure in continued_failures)
        last_stderr_tail = sync_report.truncate_tail("\n".join(details))
        return 1
    return returncode


def child_env() -> dict[str, str]:
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
    if current_run_id is not None:
        env[_RUN_ID_ENV] = current_run_id
    user_bin = str(pathlib.Path.home() / ".local" / "bin")
    current_path = env.get("PATH", "")
    path_entries = current_path.split(os.pathsep) if current_path else []
    if user_bin not in path_entries:
        path_entries.append(user_bin)
    env["PATH"] = os.pathsep.join(path_entries)
    return env


def git_env() -> dict[str, str]:
    """更新区間のGitと退避子だけへ代替indexを渡す。"""
    env = child_env()
    if update_index is not None:
        env["GIT_INDEX_FILE"] = str(update_index)
    return env


# 更新処理が差分を保持せずHEADへ戻すlockfile（作業ツリーのルートからの相対パス）。
MISE_LOCK = "mise.lock"
