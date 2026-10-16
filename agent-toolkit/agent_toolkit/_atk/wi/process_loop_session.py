"""`atk wi process-loop`のプロンプトとオーケストレーターの選択と、1回のセッションの起動。"""

import argparse
import dataclasses
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
from collections.abc import Callable

from agent_toolkit._atk import lane as _lane
from agent_toolkit._atk import orchestrator as _orchestrator
from agent_toolkit._atk.wi import listing as _listing
from agent_toolkit._atk.wi import process_loop_control as _pl_control
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log
from agent_toolkit._atk.wi import process_loop_ownership as _ownership
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi.constants import PROCESS_WI_GOAL_BODY
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._common import codex_usage_limit as _codex_usage_limit
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import next_action as _next_action

# モデル可用性だけを確認し、作業の副作用を生じさせない事前起動の固定プロンプト。
# 受領した委譲先がユーザー自身の発話と区別できるよう、`atk-auto`要素で囲んで渡す。
_AVAILABILITY_PROBE_PROMPT = _automated_prompt.wrap(
    "応答できる場合はOKだけを返してください。",
    source=_automated_prompt.SOURCE_PROCESS_LOOP,
    kind=_automated_prompt.KIND_AVAILABILITY_PROBE,
)


def build_repository_delegate_prompt(
    snapshot: _listing.RepositorySnapshot,
    ownership: _ownership.Ownership,
    *,
    instruction: str = "",
    worktree_name: str | None = None,
) -> str:
    """対象cwdの通常delegateへ、取得済み候補と所有・処理上限を渡す。"""
    data = {
        "target_repo": snapshot.repository.repo_id,
        "worktree": str(snapshot.repository.worktree),
        "common_dir": str(snapshot.repository.common_dir),
        "ownership_record": str(ownership.path),
        "ownership_token": ownership.token,
        "lane_session_id": ownership.lane_session_id,
        "lane_limit": snapshot.lane_limit,
        "candidates": snapshot.candidate_records(),
        "readiness": dataclasses.asdict(snapshot.readiness),
        "previous": ownership.previous,
        "worktree_name": worktree_name,
    }
    body = (
        "対象cwdでagent-toolkit:process-wiの選定から計画・実装・独立した実行レビュー・統合・公開・WI終端・回収まで完遂する。"
        "初回取得済み候補は次のJSONを使い、初期一覧を再取得しない。"
        "対象プロジェクトの規範・CI・監査とworktree準備は担当が対象cwdで判断・実施する。"
        "worktree_nameの指定がある場合は、対象リポジトリのworktree作成hookを含む既存の作成手順でworktreeを用意する。"
        "処理所有は親のprocess-loopが取得済みのため、同じoriginの所有を別に取得しない。"
        "レーン作成・再開・終了時回収の全操作にはlane_session_idを--session-idへ明示して使う。"
        "追加指示は同じ起動集合で一度だけ受領して各対象へ中継した本文である。"
        "この担当の対象に関する部分だけを対象cwdで適用する。別対象宛の部分は該当担当へ配送済みである。"
        "previousがある場合は保持した未統合成果を既存の再開手順へ渡す。"
        "結果には正常完了か続行できない理由、確認済み工程・残工程・再開先を返す。\n"
        + json.dumps(data, ensure_ascii=False)
        + ("\n追加指示:\n" + instruction if instruction else "")
    )
    return _automated_prompt.wrap(body, source=_automated_prompt.SOURCE_PROCESS_LOOP, kind="repository-delegate")


def all_repos_resume_session(ownership: _ownership.Ownership, requested: str | None) -> str | None:
    """all-repos担当の再開だけを認め、通常会話との混同を拒否する。"""
    previous = ownership.previous
    recorded = previous.get("session_id") if previous is not None and previous.get("mode") == "all-repos" else None
    if requested is not None and (not requested or requested != recorded):
        raise ValueError("all-reposの--resumeには保持したall-repos担当のsession IDを指定する")
    return recorded if isinstance(recorded, str) else None


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
    """orchestrate用途のモデル設定を候補ごとの(orchestrator, model, effort)として返す。"""
    return _orchestrator.resolve_specs(rerun_action="process-loopを再起動する")


def _record_usage_limit_wait(candidate: str, usage_limit: _orchestrator.UsageLimitState, delay: float) -> None:
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
            "`atk config set orchestrate_engine <engine順>`か`atk config set <engine>_high_tier_model <モデル>`で候補を変える"
        ),
    )
    sys.exit(returncode)


def _run_process_session(
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
    on_session_finished: Callable[[bool], None],
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
    env[_lane.SESSION_ENV] = lane_session_id
    launch_env[_lane.SESSION_ENV] = lane_session_id
    instruction = _process_loop_log.consume_instructions()
    if instruction:
        launch_env[_pl_env.PROCESS_LOOP_INSTRUCTION_ENV] = instruction
    try:
        if orchestrator == "codex":
            result = _run_codex_session(session_argv, launch_env, session_path)
        else:
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
    on_session_finished(normal_exit)
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


def _run_codex_session(argv: list[str], env: dict[str, str], cwd: pathlib.Path) -> subprocess.CompletedProcess[bytes]:
    """所有する子の上限停止を監視し、次の反復へ移れる終了状態を返す。"""
    started_at = time.time()
    rollout: pathlib.Path | None = None
    with subprocess.Popen(argv, env=env, cwd=cwd, creationflags=_pl_env.session_creation_flags("codex")) as process:
        try:
            while True:
                try:
                    return subprocess.CompletedProcess(argv, process.wait(timeout=1))
                except subprocess.TimeoutExpired:
                    if rollout is None:
                        rollout = _codex_usage_limit.find_session_rollout(
                            cwd, started_at, codex_home=_host_homes.codex_home(env)
                        )
                    if rollout is None or _codex_usage_limit.from_rollout(rollout) is None:
                        continue
                    _process_loop_log.append("session_usage_limit", rollout_path=str(rollout))
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    # WindowsのTerminateProcessの値も、この所有済み上限停止だけは次の反復へ進める。
                    return subprocess.CompletedProcess(argv, 0)
        except BaseException:
            process.kill()
            process.wait()
            raise


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
    on_session_finished: Callable[[bool], None] | None = None,
) -> bool:
    """子終端後・自己再起動前に所有を解放し、例外時も一度だけ通知する。"""
    notified = False

    def finished(completed: bool) -> None:
        nonlocal notified
        if notified:
            return
        notified = True
        if on_session_finished is not None:
            on_session_finished(completed)

    try:
        return _run_process_session(
            args,
            session_path,
            session_prompt,
            env,
            orchestrator=orchestrator,
            model=model,
            effort=effort,
            resume_pending=resume_pending,
            dotfiles_root=dotfiles_root,
            on_session_finished=finished,
        )
    finally:
        finished(False)
