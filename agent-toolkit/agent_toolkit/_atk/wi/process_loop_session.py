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
from agent_toolkit._atk import codex_connection as _codex_connection
from agent_toolkit._atk import config as _config
from agent_toolkit._atk import lane as _lane
from agent_toolkit._atk import orchestrator as _orchestrator
from agent_toolkit._atk.wi import listing as _listing
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log
from agent_toolkit._atk.wi import process_loop_ownership as _ownership
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._common import claude_connection as _claude_settings
from agent_toolkit._common import codex_usage_limit as _codex_usage_limit
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import process_tree
from agent_toolkit._common import session_record_paths as _record_paths
from agent_toolkit._common import session_registry as _registry

# モデル可用性だけを確認し、作業の副作用を生じさせない事前起動の固定プロンプト。
# 受領した委譲先がユーザー自身の発話と区別できるよう、`atk-auto`要素で囲んで渡す。
_AVAILABILITY_PROBE_PROMPT = _automated_prompt.wrap(
    "応答できる場合はOKだけを返してください。",
    source=_automated_prompt.SOURCE_PROCESS_LOOP,
    kind=_automated_prompt.KIND_AVAILABILITY_PROBE,
)


def repository_input(
    snapshot: _listing.RepositorySnapshot,
    ownership: _ownership.Ownership,
    *,
    worktree: pathlib.Path,
    reuse_candidates: bool = False,
) -> dict[str, Any]:
    """統括担当へ渡す、凍結した対象別の候補と所有・準備情報を返す。"""
    previous_input: dict[str, Any] = {}
    previous_manifest = (ownership.previous or {}).get("manifest")
    if reuse_candidates and isinstance(previous_manifest, str):
        data = json.loads(pathlib.Path(previous_manifest).read_text(encoding="utf-8"))
        previous_input = next((item for item in data["repositories"] if item["target_repo"] == snapshot.repository.repo_id), {})
    return {
        **previous_input,
        "target_repo": snapshot.repository.repo_id,
        "registered_worktree": str(snapshot.repository.worktree),
        "worktree": str(worktree),
        "common_dir": str(snapshot.repository.common_dir),
        "ownership_record": str(ownership.path),
        "ownership_token": ownership.token,
        "lane_session_id": ownership.lane_session_id,
        "lane_limit": previous_input.get("lane_limit", snapshot.lane_limit),
        "candidate_file": previous_input.get("candidate_file")
        or str(_ownership.save_candidates(ownership, snapshot.candidate_records())),
        "readiness": previous_input.get("readiness", dataclasses.asdict(snapshot.readiness)),
        "previous": ownership.previous,
    }


def build_process_loop_prompt(manifest: pathlib.Path | None = None) -> str:
    """登録全体の処理を依頼し、長い候補本文は配送ファイルで渡す。"""
    body = "agent-toolkit:process-wi-all-reposを完遂する"
    if manifest is not None:
        body += f"。取得済み候補・所有・準備先と結果保存先は {manifest} を読む"
    goal = _automated_prompt.wrap(
        body,
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
) -> tuple[str, str, str, str | None]:
    """候補を先頭から事前に試し、最初に可用な3つ組とCodexのprovider IDを返す。全候補が失敗した場合は異常終了する。

    Codexの統括担当は使用順の先頭から利用可能なproviderを判定し、本起動と再開へ同じproviderを渡す。
    再開では保存したproviderを使い、その種別を使用順から外した場合だけ使用順の先頭の利用可能な候補へ移す。
    """
    info = _registry.resolve(resume_session_id).resume_info if resume_session_id else None
    if info is not None:
        if info.model is None or info.effort is None:
            raise ValueError("統括の再開に必要なモデル設定が無い。セッション登録を確認する")
        candidates = [(info.engine, info.model, info.effort)]
    selected = (
        _connection.Connection(info.claude_connection or _claude_settings.LEGACY_API_KIND, info.claude_api_env_file)
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
        codex_providers=_codex_orchestrator_providers(candidates, env, info),
    )
    try:
        return _orchestrator.select_available_with_provider(candidates, env, cwd, context)
    except _orchestrator.NoAvailableCandidateError as error:
        _exit_abnormal_session(error.orchestrator, error.returncode, error.detail)
        raise AssertionError("到達不能") from error


def _codex_orchestrator_providers(
    candidates: list[tuple[str, str, str]], env: dict[str, str], info: _registry.ResumeInfo | None
) -> tuple[str, ...]:
    """Codexの統括担当で可用性を判定するprovider IDを順に返す。接続を管理しない環境では空を返す。"""
    if not any(engine == "codex" for engine, _, _ in candidates) or not _codex_connection.managed():
        return ()
    saved = info.codex_model_provider if info is not None and info.engine == "codex" else None
    if saved is not None and not _codex_connection.outside_order(
        saved, "orchestrator", subscription_provider=info.codex_subscription_provider if info is not None else None
    ):
        return (saved,)
    entries, diagnostics = _codex_connection.orchestrator_providers(env)
    for diagnostic in diagnostics:
        print(diagnostic, file=sys.stderr)
    if not entries:
        key = _config.connection_order_key("codex", "orchestrator")
        raise ValueError(
            "Codexの統括担当に使える接続がありません。"
            + "".join(f"{message}。" for message in diagnostics)
            + f"`atk config set {key} <種別列>`と各種別のprovider設定を確認する"
        )
    return tuple(provider for _, provider in entries)


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
    codex_provider: str | None = None,
) -> tuple[list[str], pathlib.Path | None]:
    """選択したオーケストレーターの対話セッション用argvを構築する。"""
    env.pop(_pl_env.PROCESS_LOOP_SESSION_ID_ENV, None)
    if orchestrator == "claude":
        info = _registry.resolve(args.resume).resume_info if resume_pending and args.resume else None
        if info is not None:
            connection = _connection.Connection(
                info.claude_connection or (_claude_settings.LEGACY_API_KIND if info.claude_api_env_file else "inherit"),
                info.claude_api_env_file,
            )
        else:
            choices = _connection.candidates("orchestrator", env=env, cli=True)
            for diagnostic in choices.warnings:
                print(diagnostic, file=sys.stderr)
            connection = choices.require_first()
        connection = _connection.apply_current_settings(connection, role="orchestrator", env=env, cli=True)
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
            argv.extend(("--permission-mode=auto", prompt))
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
        if resume_pending and args.resume:
            argv.extend(("--conversation", args.resume))
            env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = args.resume
        elif resume_pending:
            argv.append("--continue")
        # 再開時も今回のmanifest・結果保存先を配送する。/goalはClaude専用。
        argv.extend(("-i", prompt.removeprefix("/goal ")))
        return argv, None

    argv = ["codex"]
    if resume_pending:
        argv.append("resume")
    argv.extend(("--model", model, "-c", f"model_reasoning_effort={effort}"))
    if codex_provider is not None:
        argv.extend(_codex_connection.provider_arguments(codex_provider))
    if resume_pending and args.resume:
        argv.append(args.resume)
        env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = args.resume
    argv.append(prompt.removeprefix("/goal "))
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
    """担当の終了を確定した後に、終了sessionの解放応答を待たず同じ適格判定でレーン資源を回収する。

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
    on_session_finished: Callable[[bool], None],
    on_session_started: Callable[[str], None] | None,
    on_process_started: Callable[[_registry.ProcessIdentity], None] | None,
    codex_provider: str | None = None,
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
            codex_provider=codex_provider,
        )
        for argument in session_argv:
            if isinstance(argument, _claude_settings.SettingsFile):
                settings_owner.enter_context(argument)
        return _run_started_process_session(
            session_path,
            env,
            session_argv=session_argv,
            hook_debug_log=hook_debug_log,
            orchestrator=orchestrator,
            model=model,
            effort=effort,
            on_session_finished=on_session_finished,
            on_session_started=on_session_started,
            on_process_started=on_process_started,
            codex_provider=codex_provider,
        )


def _run_started_process_session(
    session_path: pathlib.Path,
    env: dict[str, str],
    *,
    session_argv: list[str],
    hook_debug_log: pathlib.Path | None,
    orchestrator: str,
    model: str,
    effort: str,
    on_session_finished: Callable[[bool], None],
    on_session_started: Callable[[str], None] | None,
    on_process_started: Callable[[_registry.ProcessIdentity], None] | None,
    codex_provider: str | None = None,
) -> bool:
    """統括CLIを1回実行し、正常終了したかを返す。"""
    if hook_debug_log is not None:
        print(f"Claude hook診断ログ: {hook_debug_log}")
    _process_loop_log.append("session_start")
    session_started_at = time.monotonic()
    # 対象別の指示とlane所有は外側の統括ループが管理する。
    # 追加指示はセッションを実際に起動する反復でだけ消費する。
    # AWIが0件で変更検知を待つ反復はここへ到達しないため、保持したまま次の起動へ残る。
    launch_env = _pl_env.session_env(env, orchestrator)
    if on_session_started is not None and (identifier := env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV)):
        on_session_started(identifier)
    try:
        result = _run_cli_session(
            session_argv,
            launch_env,
            session_path,
            orchestrator=orchestrator,
            model=model,
            effort=effort,
            on_session_started=on_session_started,
            on_process_started=on_process_started,
            codex_provider=codex_provider,
        )
        if identifier := launch_env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV):
            env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = identifier
    except OSError as error:
        _process_loop_log.append("session_launch_failed", error=type(error).__name__, detail=str(error))
        raise
    if identifier := env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV):
        info = _registry.resolve(identifier).resume_info
        if info is not None:
            _registry.publish(
                identifier,
                terminal=True,
                engine=orchestrator,
                cwd=str(session_path),
                model=model,
                effort=effort,
                model_type="orchestrate",
                claude_api_env_file=info.claude_api_env_file,
                claude_connection=info.claude_connection,
                claude_role="orchestrator",
                codex_model_provider=info.codex_model_provider,
                codex_subscription_provider=info.codex_subscription_provider,
                status="completed" if result.returncode == 0 else "interrupted",
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
    # 完遂判定・回収・中断要求・自己再起動は全対象を所有する呼出元が行う。
    return normal_exit


def _attach_orchestrator_child(identifier: str, child: _registry.ProcessIdentity) -> None:
    """起動時に公開したオーケストレーターの記録へ、turnを実行する子プロセスの識別を加えて公開し直す。"""
    resolution = _registry.resolve(identifier)
    info = resolution.resume_info
    owner = _registry.owner_process()
    if resolution.state is not _registry.Resolution.RUNNING or info is None or owner is None:
        return
    _registry.publish(
        identifier,
        terminal=False,
        engine="claude",
        cwd=info.cwd,
        model=info.model,
        effort=info.effort,
        model_type="orchestrate",
        claude_api_env_file=info.claude_api_env_file,
        claude_connection=info.claude_connection,
        claude_role="orchestrator",
        processes=(owner, child),
    )


def _run_cli_session(
    argv: list[str],
    env: dict[str, str],
    cwd: pathlib.Path,
    *,
    orchestrator: str = "codex",
    model: str | None = None,
    effort: str | None = None,
    on_session_started: Callable[[str], None] | None = None,
    on_process_started: Callable[[_registry.ProcessIdentity], None] | None = None,
    codex_provider: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """既存のCLI待機で実行主体を保持し、上限停止と無出力停止を監視する。

    Codexの`codex_provider`は再開時に同じ接続を使うため、セッション記録へ保存する。
    """
    started_at = time.time()
    rollout: pathlib.Path | None = None
    published: str | None = None
    with subprocess.Popen(argv, env=env, cwd=cwd, creationflags=_pl_env.session_creation_flags(orchestrator)) as process:
        try:
            identity = _registry.ProcessIdentity.of(process.pid)
            if identity is None:
                raise RuntimeError("統括CLIのPIDと起動時刻を取得できない")
            if on_process_started is not None:
                on_process_started(identity)
            while True:
                if orchestrator == "codex" and rollout is None:
                    known_session = env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV)
                    if known_session is not None:
                        record = _record_paths.find_session_record(known_session, codex_home=_host_homes.codex_home(env))
                        rollout = record.paths[0] if record and record.paths else None
                    else:
                        rollout = _codex_usage_limit.find_session_rollout(
                            cwd, started_at, codex_home=_host_homes.codex_home(env)
                        )
                    if rollout is not None:
                        env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = _record_paths.codex_session_id(rollout)
                identifier = env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV)
                if identifier is not None and identifier != published:
                    identity = _registry.ProcessIdentity.of(process.pid)
                    if orchestrator == "claude" and identity is not None:
                        _attach_orchestrator_child(identifier, identity)
                    else:
                        _registry.publish(
                            identifier,
                            terminal=False,
                            engine=orchestrator,
                            model=model,
                            effort=effort,
                            cwd=str(cwd),
                            model_type="orchestrate",
                            codex_model_provider=codex_provider,
                            codex_subscription_provider=_codex_connection.subscription_provider_for(codex_provider),
                            processes=tuple(item for item in (_registry.owner_process(), identity) if item),
                        )
                    if on_session_started is not None:
                        on_session_started(identifier)
                    published = identifier
                try:
                    return subprocess.CompletedProcess(argv, process.wait(timeout=1))
                except subprocess.TimeoutExpired:
                    record = (
                        _record_paths.find_session_record(
                            identifier,
                            codex_home=_host_homes.codex_home(env),
                            claude_home=_host_homes.claude_config_dir(env),
                        )
                        if identifier is not None
                        else None
                    )
                    updated = max((path.stat().st_mtime for path in record.paths), default=started_at) if record else started_at
                    if time.time() - updated > 9600:
                        _process_loop_log.append("session_stalled", session_id=identifier or "")
                        process_tree.log_residual(process_tree.stop_process_tree(process), context="process-loop stalled")
                        return subprocess.CompletedProcess(argv, 1)
                    if orchestrator == "codex" and rollout is not None and _codex_usage_limit.from_rollout(rollout) is not None:
                        _process_loop_log.append("session_usage_limit", rollout_path=str(rollout))
                        process_tree.log_residual(process_tree.stop_process_tree(process), context="process-loop usage-limit")
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
    on_session_finished: Callable[[bool], None] | None = None,
    on_session_started: Callable[[str], None] | None = None,
    on_process_started: Callable[[_registry.ProcessIdentity], None] | None = None,
    codex_provider: str | None = None,
) -> bool:
    """統括CLIの終了または例外を、呼出元へ一度だけ通知する。"""
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
            on_session_finished=finished,
            on_session_started=on_session_started,
            on_process_started=on_process_started,
            codex_provider=codex_provider,
        )
    finally:
        finished(False)
