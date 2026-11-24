# pylint: disable=global-statement

import contextlib
import dataclasses
import logging
import logging.handlers
import os
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Generator

import platformdirs
import update_dotfiles_report
from agent_toolkit._common import process_tree
from agent_toolkit._common.diagnostic_redaction import redact_secrets

from pytools._internal import sync_report, update_log

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

persistent_log: update_log.ProcessSafeRotatingFileHandler | None = None

current_stage_title: str | None = None

last_stderr_tail: str | None = None

# 直前の段が待機上限で打ち切られたか。同じ原因の再発が見込まれるため、実行器は再試行しない。
last_step_timed_out: bool = False

continued_failures: list[_ContinuedFailure] = []

update_index: pathlib.Path | None = None


@dataclasses.dataclass(frozen=True)
class _ContinuedFailure:
    """失敗しても後続の段を続けた段と、その段の次の操作。"""

    title: str
    action: str
    detail: str | None


PRUNE_READY_ENV = "DOTFILES_PRUNE_READY_FILE"


def continue_after_failure(title: str, action: str, detail: str | None = None) -> None:
    """後続の段の前提を壊さない段の失敗を記録し、呼び出し側が後続の段を続けられるようにする。"""
    logger.error("stage失敗のまま続行: %s", title)
    continued_failures.append(_ContinuedFailure(title, action, detail))


class _DiagnosticFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


@contextlib.contextmanager
def configure_persistent_log(run_id: str) -> Generator[logging.Handler | None]:
    global persistent_log  # noqa: PLW0603
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = update_log.ProcessSafeRotatingFileHandler(
            LOG_PATH,
            max_bytes=_LOG_MAX_BYTES,
            backup_count=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as error:
        print(f"永続ログを開始できませんでした: {error}", file=sys.stderr)
        yield None
        return
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(handler.close)
        cleanup.callback(logger.removeHandler, handler)
        handler.setFormatter(_DiagnosticFormatter(f"%(asctime)s run={run_id} %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        persistent_log = handler
        try:
            yield handler
        finally:
            persistent_log = None


def finish(returncode: int) -> int:
    """実行終了を記録し、失敗時は次の操作と診断ログの位置・表示コマンドを1回だけ案内する。

    同期結果の構造化記録も本関数だけが確定させる。全ての終了経路で本関数を実行するため、
    記録の欠落と、成功した段を失敗として残す書き分けの誤りを避けられる。
    """
    if current_run_id is not None:
        try:
            for failure in update_dotfiles_report.bootstrap_failures(LOG_PATH.parent):
                action = "次回のupdate-dotfiles起動時に再試行します。"
                if failure.saved and failure.title in {"uvの自己更新", "管理Pythonの更新"}:
                    log = LOG_PATH.with_name(f"uv-{failure.title}-failed.log")
                    if log.is_file():
                        action += f"詳細: {log}"
                continue_after_failure(failure.title, action, failure.detail)
            failures = [(failure.title, failure.detail or "失敗") for failure in continued_failures]
            if returncode not in {0, 130}:
                failures.append((current_stage_title or "更新の開始", last_stderr_tail or f"exit={returncode}"))
            update_dotfiles_report.report(failures, LOG_PATH.with_name("update-failures.json"), DOTFILES_ROOT)
        except update_dotfiles_report.process_tree.ReclaimError:
            logger.exception("自動起票の子孫を回収できませんでした")
            raise
        except Exception as error:  # noqa: BLE001 -- 起票の未知の障害でも更新と結果の記録を妨げない境界。
            logger.exception("更新失敗を自動起票できませんでした")
            continue_after_failure("更新失敗の起票", "update-dotfiles logsで診断を確認してください。", str(error))
    if returncode == 0:
        _mark_body_completed()
    parent_log_saved = _record_parent_diagnostics(returncode)
    if continued_failures:
        returncode = _report_continued_failures(returncode)
        if ack := os.environ.get("UPDATE_DOTFILES_BOOTSTRAP_ACK"):
            try:
                pathlib.Path(ack).touch(exist_ok=False)
            except OSError:
                logger.exception("起動準備の診断表示をランチャーへ通知できませんでした")
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
        stage = f"{current_stage_title}（exit={returncode}）の" if current_stage_title and not last_stderr_tail else ""
        print(f"次の操作: {stage}失敗理由を確認し、update-dotfilesを再実行してください。", file=sys.stderr)
    if parent_log_saved or (current_run_id and update_log.has_step_diagnostic(LOG_PATH, LOG_BACKUP_COUNT, current_run_id)):
        print(f"更新の永続ログ: {LOG_PATH}", file=sys.stderr)
        print("診断の詳細は update-dotfiles logs で直近1回の実行ログを表示して確認できる。", file=sys.stderr)
    return returncode


def _record_parent_diagnostics(returncode: int) -> bool:
    """更新本体が持つ失敗理由を記録する。標準エラーを継承した子の終了値だけでは診断先を案内しない。"""
    details = [f"{failure.title}: {failure.detail or failure.action}" for failure in continued_failures]
    if returncode and last_stderr_tail:
        details.append(last_stderr_tail)
    if not details:
        return False
    prefix = "更新本体の中断" if returncode == 130 else "更新本体の失敗"
    message = f"{prefix}: " + "\n".join(details)
    if persistent_log is None:
        print(redact_secrets(message), file=sys.stderr)
        return False
    logger.error("%s", message)
    return not persistent_log.write_failed


def _mark_body_completed() -> None:
    """続行した失敗だけで終えた場合も、ランチャーがキャッシュ整理と事前構築へ進めるよう印を残す。

    終了コード1は続行した失敗と中断の両方を表すため、ランチャーは終了コードだけでは区別できない。
    Windowsでは常駐が管理Pythonを使う間その更新を毎回見送り、整理とMCPの事前構築が常に省かれていた。
    """
    path = os.environ.get(PRUNE_READY_ENV)
    if not path:
        return
    try:
        pathlib.Path(path).touch()
    except OSError:
        logger.exception("更新後処理の実行可否の印を書けませんでした: %s", path)


def _report_continued_failures(returncode: int) -> int:
    """続行した段の失敗の一覧を標準エラーへ表示し、同期記録へ残す内容を整える。

    続行した失敗だけで終えた場合は終了コード1にし、記録する段と標準エラーの末尾を続行した失敗の一覧から作成する。
    後続の前提になる段が失敗した場合は、その段の記録を保ち、一覧は標準エラーと永続ログへ残す。
    """
    global current_stage_title, last_stderr_tail  # noqa: PLW0603
    lines = ["失敗しても後続の段を続けた段:"]
    for failure in dict.fromkeys(continued_failures):
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
    env = child_env()
    if update_index is not None:
        env["GIT_INDEX_FILE"] = str(update_index)
    return env


class ChezmoiResolveError(RuntimeError):
    pass


_MISE_WHICH_TIMEOUT_SEC = 60

_WINDOWS = sys.platform == "win32"


def chezmoi_executable() -> str:
    """更新の各段がchezmoiを起動する実行ファイルを返す。

    WindowsのCreateProcessは拡張子の無い名前に`.exe`だけを補うため、PATHにmiseのshim（`chezmoi.cmd`）しか無いと
    名前での直接起動がWinError 2になる。cmdを介すと引数の解釈がcmdの規則に変わるため、実体のexeを絶対パスで起動する。
    PATH上のchezmoi.exeを優先し、無ければ`mise which chezmoi`を各段と同じ作業ディレクトリと環境で実行して、
    導入済みの実体を取得する（`child_env`の`MISE_AUTO_INSTALL=0`により追加の導入はしない）。
    POSIXの起動経路は変えず、名前のまま起動する。pyfltr: allow-comment
    """
    if not _WINDOWS:
        return "chezmoi"
    env = child_env()
    search_path = env.get("PATH")
    found = shutil.which("chezmoi.exe", path=search_path)
    if found is not None:
        return found
    mise = shutil.which("mise.exe", path=search_path)
    if mise is None:
        raise ChezmoiResolveError("PATHにchezmoi.exeが無く、miseのshimから実体を取得するmise.exeも見つかりません")
    try:
        result = process_tree.run_with_timeout(
            [mise, "which", "chezmoi"],
            cwd=DOTFILES_ROOT,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_MISE_WHICH_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired, process_tree.ReclaimError) as error:
        raise ChezmoiResolveError(f"`{mise} which chezmoi`を実行できません: {error}") from error
    candidate = result.stdout.strip()
    if result.returncode != 0 or not candidate:
        detail = result.stderr.strip() or "出力なし"
        raise ChezmoiResolveError(f"`{mise} which chezmoi`が実体を返しません（exit={result.returncode}）: {detail}")
    path = pathlib.Path(candidate)
    if not (path.is_absolute() and path.suffix.lower() == ".exe" and path.is_file()):
        raise ChezmoiResolveError(f"`{mise} which chezmoi`の結果が実在するexeの絶対パスではありません: {candidate}")
    return str(path)


MISE_LOCK = "mise.lock"
