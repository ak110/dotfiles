import argparse
import os
import pathlib
import signal
import time
from types import FrameType

from agent_toolkit._atk import config_shared_migration, outcome
from agent_toolkit._atk.wi import process_loop_control as control
from agent_toolkit._atk.wi import process_loop_env as env
from agent_toolkit._atk.wi import process_loop_log as log
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._common import process_tree
from agent_toolkit._common.console_title import console_title

_MAX_CONSECUTIVE_FAILURES = 3
_RETRY_DELAY_SECONDS = 1.0


class _StopRequested(BaseException):
    """待機中の子を回収してからシグナルによる終了を返す。"""


def _stop(signum: int, _frame: FrameType | None) -> None:

    for candidate in (signal.SIGINT, signal.SIGTERM):
        signal.signal(candidate, signal.SIG_IGN)
    raise _StopRequested(signum)


def _run_loop(*, once: bool, no_alerts: bool, alert_interval: int) -> int:
    cwd = pathlib.Path.cwd()
    failures = 0
    while not control.consume_process_loop_abort():
        request = control.read_resume_request()
        if request is not None and request.mode == "auto":
            raise ValueError("旧方式のauto再開予約が残っている。resume-cancelで取り消し、必要なら会話IDを指定する")
        resume_id = request.session_id if request is not None else None
        launch = argparse.Namespace(
            once=once,
            resume=resume_id,
            resume_request_id=request.request_id if request is not None else None,
        )
        child_env = env.child_env()
        child_env[env.NO_ALERTS_ENV] = "1" if no_alerts else "0"
        child_env[env.ALERT_INTERVAL_ENV] = str(alert_interval)
        try:
            engine, model, effort, provider, connection = session.select_available_orchestrator(
                session.resolve_orchestrator_specs(), child_env, cwd, resume_session_id=resume_id
            )
            prompt = session.build_resume_prompt() if request is not None else session.build_process_loop_prompt()
            normal = session.run_process_session(
                launch,
                cwd,
                prompt,
                child_env,
                orchestrator=engine,
                model=model,
                effort=effort,
                resume_pending=request is not None,
                codex_provider=provider,
                claude_connection=connection,
            )
        except control.ResumeChanged:
            continue
        except OSError as error:
            log.append("session_launch_failed", error=type(error).__name__, detail=str(error))
            normal = False
        if control.consume_process_loop_abort():
            return 0
        if normal:
            if once:
                return 0
            failures = 0
            continue
        failures += 1
        if once or failures >= _MAX_CONSECUTIVE_FAILURES:
            outcome.report_failure(
                f"巡回セッションが異常終了した（連続{failures}回）",
                next_action=f"診断ログ `{log.log_path()}` を確認し、原因を解消してからprocess-loopを再実行する",
            )
            return 1
        time.sleep(_RETRY_DELAY_SECONDS)
    return 0


def cmd_process_loop(args: argparse.Namespace) -> int:
    config_shared_migration.migrate_at_startup()
    previous = {key: os.environ.get(key) for key in (env.PROCESS_LOOP_SESSION_ENV, env.PROCESS_LOOP_SESSION_ID_ENV)}
    os.environ[env.PROCESS_LOOP_SESSION_ENV] = "1"
    os.environ.pop(env.PROCESS_LOOP_SESSION_ID_ENV, None)
    try:
        with console_title("atk wi process-loop"):
            previous_signals = {signum: signal.signal(signum, _stop) for signum in (signal.SIGINT, signal.SIGTERM)}
            try:
                return _run_loop(
                    once=args.once,
                    no_alerts=getattr(args, "no_alerts", False),
                    alert_interval=getattr(args, "alert_interval", env.DEFAULT_ALERT_INTERVAL),
                )
            except _StopRequested as stop:
                return 128 + int(stop.args[0])
            except process_tree.ReclaimError as error:
                outcome.report_failure(
                    f"統括CLIの子孫を回収できない（{error.context}）: {error.residual}",
                    next_action="表示されたPIDと起動コマンドを調べ、残った子孫の終了を確かめてから同じ会話を再開する",
                )
                return 1
            finally:
                for signum, handler in previous_signals.items():
                    signal.signal(signum, handler)
    finally:
        env.restore_process_loop_env(previous)
