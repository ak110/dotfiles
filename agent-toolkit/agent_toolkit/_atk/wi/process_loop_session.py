"""`atk wi process-loop`のプロンプトとオーケストレーターの選択と、1回のセッションの起動。"""

import argparse
import contextlib
import dataclasses
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

from agent_toolkit._atk import claude_connection as _connection
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
from agent_toolkit._common import claude_connection as _claude_settings
from agent_toolkit._common import codex_usage_limit as _codex_usage_limit
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import process_tree
from agent_toolkit._common import session_registry as _registry

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
        "candidate_file": str(_ownership.save_candidates(ownership, snapshot.candidate_records())),
        "readiness": dataclasses.asdict(snapshot.readiness),
        "previous": ownership.previous,
        "worktree_name": worktree_name,
    }
    body = (
        "対象cwdでagent-toolkit:process-wiを完遂し、次のJSONを受渡し値として使う。\n"
        + json.dumps(_without_nulls(data), ensure_ascii=False)
        + ("\n追加指示:\n" + instruction if instruction else "")
    )
    return _automated_prompt.wrap(body, source=_automated_prompt.SOURCE_PROCESS_LOOP, kind="repository-delegate")


def _without_nulls(value: object) -> object:
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_nulls(item) for item in value]
    return value


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
    入力とする評価の対象となる。条件が長いほど各評価の入力が増える。この関数へ記述を追加する
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
    candidates: list[tuple[str, str, str]],
    env: dict[str, str],
    cwd: pathlib.Path,
    *,
    resume_session_id: str | None = None,
) -> tuple[str, str, str]:
    """候補を先頭から事前に試し、最初に可用な3つ組を返す。全候補が失敗した場合は異常終了する。"""
    info = _registry.resolve(resume_session_id).resume_info if resume_session_id else None
    selected = (
        _connection.Connection(info.claude_connection or "api", info.claude_api_env_file)
        if info is not None and (info.claude_api_env_file or info.claude_connection)
        else None
    )
    if info is not None and selected is None:
        selected = _connection.Connection("inherit")
    context = _orchestrator.ProbeContext(
        title="atk wi process-loop",
        prompt=_AVAILABILITY_PROBE_PROMPT,
        record_wait=_record_usage_limit_wait,
        claude_connection=selected,
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
    cwd: pathlib.Path,
) -> tuple[list[str], pathlib.Path | None]:
    """選択したオーケストレーターの対話セッション用argvを構築する。"""
    env.pop(_pl_env.PROCESS_LOOP_SESSION_ID_ENV, None)
    if orchestrator == "claude":
        info = _registry.resolve(args.resume).resume_info if resume_pending and args.resume else None
        if info is not None:
            connection = _connection.Connection(
                info.claude_connection or ("api" if info.claude_api_env_file else "inherit"), info.claude_api_env_file
            )
        else:
            choices = _connection.candidates("orchestrator", env=env)
            for diagnostic in choices.warnings:
                print(diagnostic, file=sys.stderr)
            connection = choices.require_first()
        connection = _connection.with_current_isolation(connection, role="orchestrator", env=env)
        prepared = _connection.connection_environment(env, connection)
        # 対話起動は未承認の環境APIキーで確認画面を表示するため、helperから取得する。
        if connection.kind != "inherit":
            prepared.pop("ANTHROPIC_API_KEY", None)
        env.clear()
        env.update(prepared)
        overrides = _connection.connection_overrides(connection)
        if connection.kind != "inherit":
            overrides.pop("ANTHROPIC_API_KEY", None)
        hook_debug_log = _pl_env.create_hook_debug_log(env)
        argv = [
            "claude",
            "--debug=hooks",
            "--debug-file",
            str(hook_debug_log),
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
        settings = _claude_settings.merge_settings(
            _pl_env.dialog_timeout_settings(),
            overrides,
            None,
            force_file=bool(connection.api_env_file),
            key_helper_command=_claude_settings.api_key_helper(connection.api_env_file) if connection.api_env_file else None,
            isolate_auth=connection.kind != "inherit",
            isolation_keys=_connection.isolation_keys(connection),
            cwd=cwd,
        )
        with contextlib.ExitStack() as settings_owner:
            if isinstance(settings, _claude_settings.SettingsFile):
                settings_owner.enter_context(settings)
            if identifier := env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV):
                _registry.publish(
                    identifier,
                    terminal=False,
                    engine="claude",
                    cwd=str(cwd),
                    model=model,
                    effort=effort,
                    model_type="orchestrate",
                    claude_api_env_file=connection.api_env_file,
                    claude_connection=connection.kind,
                    claude_role="orchestrator",
                    processes=tuple(value for value in (_registry.owner_process(),) if value is not None),
                )
            argv[4:4] = ["--settings", settings]
            result = argv, hook_debug_log
            settings_owner.pop_all()
            return result

    if orchestrator == "agy":
        argv = ["agy", "--model", model, "--effort", effort, "--mode", "auto"]
        if resume_pending:
            if args.resume:
                argv.extend(("--conversation", args.resume))
                env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = args.resume
            else:
                argv.append("--continue")
        else:
            # /goalはClaude専用のため、境界付き目的文を対話起動へ渡す。
            argv.extend(("-i", prompt.removeprefix("/goal ")))
        return argv, None

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
            "`atk config set orchestrate_engine <engine順>`か`atk config set <engine>_execute_model <モデル>`で候補を変える"
        ),
    )
    sys.exit(returncode)


def cleanup_lane_session(lane_session_id: str, *, target_repo: str | None = None) -> dict[str, Any]:
    """担当の終了を確定した後に、終端sessionの解放応答を待たず同じ適格判定でレーン資源を回収する。

    単一起動の子終了後と、all-reposで担当の所有を解放した後の両方でこの処理を実行する。
    結果はprocess-loopログの`lane_cleanup`へ残し、保持した資源と回収の失敗には同じ所有sessionで
    再回収する操作を示す。回収の失敗は戻り値へ記録して送出せず、呼び出し側の残りの終了処理
    （他担当の終了と回収、再起動判断）を続けさせる。
    """
    next_action = f"残存理由を解消し、atk lane delete --session-id {lane_session_id}を実行する"
    scope = "" if target_repo is None else f"{target_repo}: "
    fields: dict[str, Any] = {} if target_repo is None else {"target_repo": target_repo}
    try:
        cleanup = _lane.cleanup_session(lane_session_id, wait_release=False)
    except Exception as error:
        # 境界層で全例外を捕捉する。回収できなかった資源は登録とともに残り、同じ操作で再回収できる。
        _process_loop_log.append("lane_cleanup", **fields, lane_session_id=lane_session_id, error=str(error))
        _next_action.report(f"{scope}担当の終了後にレーン資源を回収できなかった: {error}", next_action=next_action)
        return {"lane_session_id": lane_session_id, "error": str(error)}
    # 回収結果のsession_idはレーン所有sessionを指すため、担当sessionと区別できる名前で記録する。
    result = {key: value for key, value in cleanup.items() if key != "session_id"}
    _process_loop_log.append("lane_cleanup", **fields, lane_session_id=lane_session_id, **result)
    if cleanup["retained"]:
        _next_action.report(
            f"{scope}担当の終了後に未回収のレーン資源を保持した: {cleanup.get('result_path')}",
            next_action=next_action,
        )
    return {"lane_session_id": lane_session_id, **result}


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
    """起動引数が作成した設定の寿命を、準備・実行・終了処理の全体へ結び付ける。"""
    with contextlib.ExitStack() as settings_owner:
        session_argv, hook_debug_log = _build_session_argv(
            args,
            session_prompt,
            env,
            orchestrator=orchestrator,
            model=model,
            effort=effort,
            resume_pending=resume_pending,
            cwd=session_path,
        )
        for argument in session_argv:
            if isinstance(argument, _claude_settings.SettingsFile):
                settings_owner.enter_context(argument)
        return _run_started_process_session(
            args,
            session_path,
            env,
            session_argv=session_argv,
            hook_debug_log=hook_debug_log,
            orchestrator=orchestrator,
            model=model,
            effort=effort,
            dotfiles_root=dotfiles_root,
            on_session_finished=on_session_finished,
        )


def _run_started_process_session(
    args: argparse.Namespace,
    session_path: pathlib.Path,
    env: dict[str, str],
    *,
    session_argv: list[str],
    hook_debug_log: pathlib.Path | None,
    orchestrator: str,
    model: str,
    effort: str,
    dotfiles_root: pathlib.Path | None,
    on_session_finished: Callable[[bool], None],
) -> bool:
    """子セッションを1回実行し、process-loopを終了すべきかを返す。

    中断要求の判定は`_pl_update.restart_process_loop`の呼び出しより前に置く。同関数は呼び出し元へ
    戻らないため、後段へ置いた判定は`--no-update`を省略して起動した場合には実行されない。
    """
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
    if orchestrator == "claude" and (identifier := env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV)):
        info = _registry.resolve(identifier).resume_info
        if info is not None:
            _registry.publish(
                identifier,
                terminal=True,
                engine="claude",
                cwd=str(session_path),
                model=model,
                effort=effort,
                model_type="orchestrate",
                claude_api_env_file=info.claude_api_env_file,
                claude_connection=info.claude_connection,
                claude_role="orchestrator",
                status="completed" if result.returncode == 0 else "interrupted",
            )
    cleanup_lane_session(lane_session_id)
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
                    process_tree.log_residual(process_tree.stop_process_tree(process), context="process-loop usage-limit")
                    # WindowsのTerminateProcessの値も、この所有済み上限停止だけは次の反復へ進める。
                    return subprocess.CompletedProcess(argv, 0)
        except BaseException:
            process_tree.log_residual(process_tree.stop_process_tree(process), context="process-loop exception")
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
