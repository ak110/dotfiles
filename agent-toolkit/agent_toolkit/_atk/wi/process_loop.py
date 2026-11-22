"""巡回を始める対話CLIを起動し、終了結果に応じて再起動する。"""

import argparse
import os
import pathlib
import signal
import time
from types import FrameType

from agent_toolkit._atk import outcome
from agent_toolkit._atk.wi import process_loop_control as control
from agent_toolkit._atk.wi import process_loop_env as env
from agent_toolkit._atk.wi import process_loop_log as log
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._common.console_title import console_title

_MAX_CONSECUTIVE_FAILURES = 3
_RETRY_DELAY_SECONDS = 1.0


class _StopRequested(BaseException):
    """待機中の子を回収してからシグナルによる終了を返す。"""


def _stop(signum: int, _frame: FrameType | None) -> None:
    # 子の回収中に同じシグナルを再度受けても、回収を中断しない。
    for candidate in (signal.SIGINT, signal.SIGTERM):
        signal.signal(candidate, signal.SIG_IGN)
    raise _StopRequested(signum)


def _run_loop(*, once: bool) -> int:
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
    """起動元の作業ディレクトリで対話CLIを実行する。"""
    previous = {key: os.environ.get(key) for key in (env.PROCESS_LOOP_SESSION_ENV, env.PROCESS_LOOP_SESSION_ID_ENV)}
    os.environ[env.PROCESS_LOOP_SESSION_ENV] = "1"
    os.environ.pop(env.PROCESS_LOOP_SESSION_ID_ENV, None)
    try:
        with console_title("atk wi process-loop"):
            previous_signals = {signum: signal.signal(signum, _stop) for signum in (signal.SIGINT, signal.SIGTERM)}
            try:
                return _run_loop(once=args.once)
            except _StopRequested as stop:
                return 128 + int(stop.args[0])
            finally:
                for signum, handler in previous_signals.items():
                    signal.signal(signum, handler)
    finally:
        env.restore_process_loop_env(previous)
