"""`atk wi process-loop`のプロンプトとオーケストレーターの選択と、1回のセッションの起動。"""

import argparse
import os
import pathlib
import subprocess
import sys
import time
import uuid

from agent_toolkit._atk import lane as _lane
from agent_toolkit._atk import orchestrator as _orchestrator
from agent_toolkit._atk.wi import process_loop_control as _pl_control
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi.constants import PROCESS_WI_GOAL_BODY
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._common import claude_usage_limit as _claude_usage_limit
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import next_action as _next_action

# モデル可用性だけを確認し、作業の副作用を生じさせない事前起動の固定プロンプト。
# 受領した委譲先がユーザー自身の発話と区別できるよう、`atk-auto`要素で囲んで渡す。
_AVAILABILITY_PROBE_PROMPT = _automated_prompt.wrap(
    "応答できる場合はOKだけを返してください。",
    source=_automated_prompt.SOURCE_PROCESS_LOOP,
    kind=_automated_prompt.KIND_AVAILABILITY_PROBE,
)


def build_process_loop_prompt() -> str:
    """AWI処理の完遂を依頼する最小の目的文を構築する。

    目的文はスキルの完遂だけを求める。処理対象、処理範囲、終了手順、実行基盤の障害対応および
    再開条件は`agent-toolkit:process-wi`とその参照先が定める。目的文へ重ねて書くと、
    スキル側の規範と目的文の記述が二重管理になり、目的文の記述がユーザー指示として扱われて
    スキル側の規範より優先される。

    目的文は`/goal`条件としてオーケストレーターへ渡り、ターンを終えるたびにセッション記録の全体を
    入力とする評価の対象となる。条件が長いほど各評価の入力が増える。この関数へ記述を足す
    変更は行わない。過去に作業ディレクトリ、対象リポジトリおよび終了手順の指示が順に加わり、
    そのたびに短縮を求める指摘を受領した経緯がある。

    処理対象は`run_process_session`が子セッションの作業ディレクトリの引数として渡すことで伝わる。
    `atk wi`の各サブコマンドは`--target-repo`を省略した場合に作業ディレクトリから対象
    リポジトリを解決するため、目的文へ処理対象を書く必要はない。

    目的文は`atk-auto`要素で囲み、受領した子セッションがユーザー自身の発話と区別できる形にする。
    標識は`/goal`の引数の位置へ置く。ホストは1行目の先頭にあるスラッシュコマンドだけを
    コマンドとして解釈するため、本文全体を囲むとコマンドとして成立しない。
    """
    goal = _automated_prompt.wrap(
        PROCESS_WI_GOAL_BODY,
        source=_automated_prompt.SOURCE_PROCESS_LOOP,
        kind=_automated_prompt.KIND_GOAL,
    )
    return f"/goal {goal}"


def resolve_orchestrator_specs() -> list[tuple[str, str, str]]:
    """orchestrate_model設定を候補ごとの(orchestrator, model, effort)として返す。"""
    return _orchestrator.resolve_specs(rerun_action="process-loopを再起動する")


def _record_usage_limit_wait(candidate: str, usage_limit: _claude_usage_limit.UsageLimitState, delay: float) -> None:
    """利用上限の解除待ちをprocess-loopのログへ記録する。"""
    _process_loop_log.append(
        "usage_limit_wait",
        candidate=candidate,
        limit_type=usage_limit.limit_type or "",
        resets_at=usage_limit.resets_at_iso() or "",
        delay_seconds=int(delay),
    )


def select_available_orchestrator(
    candidates: list[tuple[str, str, str]], env: dict[str, str], cwd: pathlib.Path
) -> tuple[str, str, str]:
    """候補を先頭から事前に試し、最初に可用な3つ組を返す。全候補が失敗した場合は異常終了する。"""
    context = _orchestrator.ProbeContext(
        title="atk wi process-loop", prompt=_AVAILABILITY_PROBE_PROMPT, record_wait=_record_usage_limit_wait
    )
    try:
        return _orchestrator.select_available(candidates, env, cwd, context)
    except _orchestrator.NoAvailableCandidateError as error:
        _exit_abnormal_session(error.orchestrator, error.returncode, error.detail)
        raise AssertionError("到達不能") from error


def _build_session_argv(
    args: argparse.Namespace,
    prompt: str,
    env: dict[str, str],
    *,
    orchestrator: str,
    model: str,
    effort: str,
    resume_pending: bool,
) -> tuple[list[str], pathlib.Path | None]:
    """選択したオーケストレーターの対話セッション用argvを構築する。"""
    env.pop(_pl_env.PROCESS_LOOP_SESSION_ID_ENV, None)
    if orchestrator == "claude":
        hook_debug_log = _pl_env.create_hook_debug_log(env)
        argv = [
            "claude",
            "--debug=hooks",
            "--debug-file",
            str(hook_debug_log),
            "--settings",
            _pl_env.dialog_timeout_settings(),
        ]
        if resume_pending:
            argv.extend(("--model", model, "--effort", effort))
            argv.append("--resume" if not args.resume else f"--resume={args.resume}")
            if args.resume:
                env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = args.resume
        else:
            session_id = str(uuid.uuid4())
            env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = session_id
            argv.extend(("--session-id", session_id))
            argv.extend(("--permission-mode=auto", "--model", model, "--effort", effort, prompt))
        return argv, hook_debug_log

    argv = ["codex"]
    if resume_pending:
        argv.append("resume")
    argv.extend(("--model", model, "-c", f"model_reasoning_effort={effort}"))
    if resume_pending:
        if args.resume:
            argv.append(args.resume)
    else:
        argv.append(prompt)
    return argv, None


_is_normal_session_exit = _orchestrator.is_normal_session_exit


def _exit_abnormal_session(orchestrator: str, returncode: int, detail: str = "") -> None:
    """既存のセッション異常終了メッセージを出力し、同じ終了コードで終了する。"""
    suffix = f" 原因: {detail}" if detail else ""
    _next_action.report(
        f"{orchestrator}がexit code {returncode}で異常終了しました。{suffix}",
        next_action=(
            "オーケストレーターのセッション記録（Claude Codeは`~/.claude/projects`配下、Codexは`~/.codex/sessions`配下）"
            "で原因を確認し、解消してからprocess-loopを再起動する。モデル候補の問題なら"
            "`atk config set orchestrate_model <候補列>`で候補を変える"
        ),
    )
    sys.exit(returncode)


def run_process_session(
    args: argparse.Namespace,
    session_path: pathlib.Path,
    session_prompt: str,
    env: dict[str, str],
    *,
    orchestrator: str,
    model: str,
    effort: str,
    resume_pending: bool,
    dotfiles_root: pathlib.Path | None,
) -> bool:
    """子セッションを1回実行し、process-loopを終了すべきかを返す。

    中断要求の判定は`_pl_update.restart_process_loop`の呼び出しより前に置く。同関数は呼び出し元へ
    戻らないため、後段へ置いた判定は`--no-update`を省略して起動した場合には実行されない。
    """
    session_argv, hook_debug_log = _build_session_argv(
        args,
        session_prompt,
        env,
        orchestrator=orchestrator,
        model=model,
        effort=effort,
        resume_pending=resume_pending,
    )
    if hook_debug_log is not None:
        print(f"Claude hook診断ログ: {hook_debug_log}")
    _process_loop_log.append("session_start")
    session_started_at = time.monotonic()
    # 追加指示はセッションを実際に起動する反復でだけ消費する。
    # AWIが0件で変更検知を待つ反復はここへ到達しないため、保持したまま次の起動へ残る。
    launch_env = _pl_env.session_env(env, orchestrator)
    lane_session_id = str(uuid.uuid4())
    launch_env[_lane.SESSION_ENV] = lane_session_id
    instruction = _process_loop_log.consume_instructions()
    if instruction:
        launch_env[_pl_env.PROCESS_LOOP_INSTRUCTION_ENV] = instruction
    try:
        result = subprocess.run(
            session_argv,
            check=False,
            env=launch_env,
            cwd=session_path,
            creationflags=_pl_env.session_creation_flags(orchestrator),
        )
    except OSError as error:
        _process_loop_log.append("session_launch_failed", error=type(error).__name__, detail=str(error))
        raise
    cleanup = _lane.cleanup_session(lane_session_id)
    _process_loop_log.append("lane_cleanup", **cleanup)
    if cleanup["retained"]:
        _next_action.report(
            f"子セッション終了後に未回収のレーン資源を保持した: {cleanup.get('result_path')}",
            next_action=f"残存理由を解消し、atk lane delete --session-id {lane_session_id}を実行する",
        )
    _pl_env.reset_console()
    _console_title.set_console_title("atk wi process-loop")
    _process_loop_log.append(
        "session_end",
        elapsed_sec=round(time.monotonic() - session_started_at, 3),
        returncode=result.returncode,
    )
    normal_exit = _is_normal_session_exit(orchestrator, result.returncode, platform=os.name)
    _process_loop_log.append("session_classified", normal=normal_exit, returncode=result.returncode)
    if not normal_exit:
        _exit_abnormal_session(orchestrator, result.returncode)
    if _pl_control.consume_process_loop_abort():
        _process_loop_log.append("loop_exit", reason="abort")
        return True
    if args.no_update:
        _process_loop_log.append("loop_continue", reason="no_update")
        return False
    print("process-loopを再起動します。")
    _pl_update.restart_process_loop(
        sys.argv,
        dotfiles_root,
        resume_consumed=True,
        mise_refreshed=False,
        dotfiles_updated=False,
    )
    return False
