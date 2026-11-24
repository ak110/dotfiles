import argparse
import contextlib
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
from collections.abc import Callable

import psutil

from agent_toolkit._atk import claude_connection as _connection
from agent_toolkit._atk import codex_connection as _codex_connection
from agent_toolkit._atk import config as _config
from agent_toolkit._atk import managed_temp, usage_limit_policy, usage_refresh
from agent_toolkit._atk import orchestrator as _orchestrator
from agent_toolkit._atk.wi import process_loop_control as _control
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_identity as startup_identity
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log
from agent_toolkit._common import antigravity_cli, process_tree, skill_modes
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._common import claude_connection as _claude_settings
from agent_toolkit._common import claude_usage_limit as _claude_usage_limit
from agent_toolkit._common import codex_usage_limit as _codex_usage_limit
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import process_loop_session as _coordinator_identity
from agent_toolkit._common import session_record_paths as _record_paths
from agent_toolkit._common import session_registry as _registry
from agent_toolkit._common.next_action import recover_from_failure

_AVAILABILITY_PROBE_PROMPT = _automated_prompt.wrap(
    "応答できる場合はOKだけを返してください。",
    source=_automated_prompt.SOURCE_PROCESS_LOOP,
    kind=_automated_prompt.KIND_AVAILABILITY_PROBE,
)


class ClaudeUsageLimit(Exception):
    def __init__(self, session_id: str, limit: _claude_usage_limit.UsageLimitState | None = None) -> None:
        super().__init__(session_id)
        self.session_id = session_id
        self.limit = limit


def build_process_loop_prompt() -> str:
    return _automated_prompt.wrap(
        skill_modes.entry_launch("process-loop", free_text=False),
        source=_automated_prompt.SOURCE_PROCESS_LOOP,
        kind=_automated_prompt.KIND_GOAL,
    )


def resolve_orchestrator_specs() -> list[tuple[str, str, str]]:
    return _orchestrator.resolve_specs(rerun_action="process-loopを再起動する")


def build_resume_prompt() -> str:
    return _automated_prompt.wrap(
        "保存済みの要求・認可・元の目的と成果を確認し、最初の未完了工程から続行せよ。"
        "未達の目的を達成扱いに変えず、成功済みの操作を繰り返すな。",
        source=_automated_prompt.SOURCE_PROCESS_LOOP,
        kind="resume",
    )


def _record_usage_limit_wait(candidate: str, usage_limit: _orchestrator.UsageLimitState, delay: float) -> None:
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
) -> tuple[str, str, str, str | None, _connection.Connection | None]:
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
    claude_connection: _connection.Connection | None = None,
) -> tuple[list[str], pathlib.Path | None]:
    env.pop(_pl_env.PROCESS_LOOP_SESSION_ID_ENV, None)

    env[_coordinator_identity.CLAIM_ENV] = uuid.uuid4().hex
    if orchestrator == "claude":
        info = _registry.resolve(args.resume).resume_info if resume_pending and args.resume else None
        if claude_connection is not None:
            connection = claude_connection
        elif info is not None:
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
            argv.append("--permission-mode=auto")
            if prompt:
                argv.append(prompt)
        else:
            session_id = str(uuid.uuid4())
            env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = session_id
            argv.extend(("--session-id", session_id))
            argv.extend(("--permission-mode=auto", "--model", model, "--effort", effort, prompt))
        if identifier := env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV):
            request = _host_homes.claude_config_dir(env) / "agent-toolkit-function-hooks" / f"rate-limit-{identifier}.txt"
            request.unlink(missing_ok=True)
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
                    state=_registry.PublicationState.PENDING_RUNNING,
                    engine="claude",
                    cwd=str(cwd),
                    model=model,
                    effort=effort,
                    turn_seq=info.turn_seq + 1 if info is not None else 1,
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
            raise ValueError("このengineには会話pickerがないため、再開IDを指定する")
        argv.extend(("-i", prompt))
        return argv, None

    argv = ["codex"]
    if resume_pending:
        argv.append("resume")
    # 共有daemonでは起動元の統括識別をhookが継承せず、終了操作も対話CLIを祖先から特定できない。
    argv.append("--no-daemon")
    argv.extend(("--model", model, "-c", f"model_reasoning_effort={effort}"))
    if codex_provider is not None:
        argv.extend(_codex_connection.provider_arguments(codex_provider))
    if resume_pending and args.resume:
        argv.append(args.resume)
        env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = args.resume
    if prompt:
        argv.append(prompt)
    return argv, None


_is_normal_session_exit = _orchestrator.is_normal_session_exit


def _exit_abnormal_session(orchestrator: str, returncode: int, detail: str = "") -> None:
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
    on_residual: Callable[[process_tree.ReclaimError], None] | None = None,
    codex_provider: str | None = None,
    claude_connection: _connection.Connection | None = None,
) -> bool:
    original_env = env.copy()
    while True:
        try:
            return _run_selected_process_session(
                args,
                session_path,
                session_prompt
                + (
                    "\n単回実行: 処理可能な候補が無いか、今回の検収を終えた時点で atk agents-exit-session で終了する。"
                    if getattr(args, "once", False)
                    else ""
                ),
                env,
                orchestrator=orchestrator,
                model=model,
                effort=effort,
                resume_pending=resume_pending,
                on_session_finished=on_session_finished,
                on_session_started=on_session_started,
                on_process_started=on_process_started,
                on_residual=on_residual,
                codex_provider=codex_provider,
                claude_connection=claude_connection,
            )
        except ClaudeUsageLimit as limited:
            if limited.limit is None:
                _process_loop_log.append("session_usage_limit", session_id=limited.session_id)
            else:
                resets_at = limited.limit.resets_at_iso() or ""
                _process_loop_log.append(
                    "session_usage_limit", session_id=limited.session_id, limit_type="seven_day", resets_at=resets_at
                )
                print(
                    f"Claude Codeの週間利用上限（seven_day）。解除予定: {resets_at}。"
                    f"解除後は atk wi process-loop --resume {limited.session_id} で再開できます。",
                    file=sys.stderr,
                )
            _, model, effort, _, claude_connection = select_available_orchestrator(
                [("claude", model, effort)],
                original_env,
                session_path,
                resume_session_id=limited.session_id,
            )
            args = argparse.Namespace(**vars(args))
            args.resume = limited.session_id
            args.resume_request_id = None
            resume_pending = True
            env.clear()
            env.update(original_env)
            session_prompt = _automated_prompt.wrap(
                "利用上限で中断した同じ会話の未完了作業を続行せよ。保存済みの要求と成果を確認し、完了済みの操作は繰り返すな。",
                source=_automated_prompt.SOURCE_PROCESS_LOOP,
                kind="usage-limit-resume",
            )


def _run_selected_process_session(
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
    on_residual: Callable[[process_tree.ReclaimError], None] | None = None,
    codex_provider: str | None,
    claude_connection: _connection.Connection | None,
) -> bool:
    with (
        contextlib.ExitStack() as settings_owner,
        usage_refresh.cli_activity(
            orchestrator, model, effort, claude_connection.kind if claude_connection is not None else codex_provider
        ),
    ):
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
            claude_connection=claude_connection,
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
            on_residual=on_residual,
            codex_provider=codex_provider,
            resume_request_id=getattr(args, "resume_request_id", None),
            claude_connection=claude_connection,
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
    on_residual: Callable[[process_tree.ReclaimError], None] | None = None,
    codex_provider: str | None = None,
    resume_request_id: str | None = None,
    claude_connection: _connection.Connection | None = None,
) -> bool:
    if hook_debug_log is not None:
        print(f"Claude hook診断ログ: {hook_debug_log}")
    _process_loop_log.append("session_start")
    session_started_at = time.monotonic()
    launch_env = _pl_env.session_env(env, orchestrator)
    if (
        on_session_started is not None
        and startup_identity.read(env) is None
        and (identifier := env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV))
    ):
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
            on_residual=on_residual,
            codex_provider=codex_provider,
            resume_request_id=resume_request_id,
            claude_connection=claude_connection,
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
                state=_registry.PublicationState.RESULT_COMPLETED
                if result.returncode == 0
                else _registry.PublicationState.RESULT_INTERRUPTED,
                engine=orchestrator,
                cwd=str(session_path),
                model=model,
                effort=effort,
                model_type="orchestrate",
                turn_seq=info.turn_seq,
                claude_api_env_file=info.claude_api_env_file,
                claude_connection=info.claude_connection,
                claude_role="orchestrator",
                codex_model_provider=info.codex_model_provider,
                codex_subscription_provider=info.codex_subscription_provider,
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
    return normal_exit


def _attach_orchestrator_child(identifier: str, child: _registry.ProcessIdentity) -> None:
    resolution = _registry.resolve(identifier)
    info = resolution.resume_info
    owner = _registry.owner_process()
    if resolution.state is not _registry.Resolution.RUNNING or info is None or owner is None:
        return
    _registry.publish(
        identifier,
        state=_registry.PublicationState.PENDING_RUNNING,
        engine="claude",
        cwd=info.cwd,
        model=info.model,
        effort=info.effort,
        turn_seq=info.turn_seq,
        model_type="orchestrate",
        claude_api_env_file=info.claude_api_env_file,
        claude_connection=info.claude_connection,
        claude_role="orchestrator",
        processes=(owner, child),
    )


def _stop_orchestrator(
    process: subprocess.Popen[bytes],
    *,
    context: str,
    on_residual: Callable[[process_tree.ReclaimError], None] | None,
    known: list[psutil.Process] | None = None,
    pins: process_tree.ProcessIdentityPins | None = None,
) -> None:
    failure = process_tree.stop_owned_process(process, context=context, known=known or (), pins=pins)
    if failure is None:
        return
    process_tree.log_residual(failure.residual, context=context)
    if on_residual is not None:
        on_residual(failure)
    raise failure


def _raise_claude_usage_limit(
    process: subprocess.Popen[bytes],
    env: dict[str, str],
    identifier: str | None,
    on_residual: Callable[[process_tree.ReclaimError], None] | None = None,
    *,
    known: list[psutil.Process],
    pins: process_tree.ProcessIdentityPins,
) -> None:
    if identifier is None:
        return
    request = _host_homes.claude_config_dir(env) / "agent-toolkit-function-hooks" / f"rate-limit-{identifier}.txt"
    try:
        limited = request.read_text(encoding="utf-8") == "requested"
    except FileNotFoundError:
        return
    if not limited:
        return
    _stop_orchestrator(
        process,
        context="process-loop usage-limit switch",
        on_residual=on_residual,
        known=known,
        pins=pins,
    )
    request.unlink(missing_ok=True)
    raise ClaudeUsageLimit(identifier)


def _unwaited_weekly_limit(
    connection: _connection.Connection, env: dict[str, str]
) -> _claude_usage_limit.UsageLimitState | None:
    if connection.kind not in {"subscription", "inherit"} or usage_limit_policy.should_wait("claude", "seven_day"):
        return None
    if connection.kind == "inherit" and any(
        env.get(key) for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
    ):
        return None
    try:
        windows = usage_refresh.refresh_claude(connection, env)
    except OSError, ValueError, subprocess.TimeoutExpired:
        return None
    now = time.time()
    for window in windows:
        if (
            window.window_id in {"weekly_all", "seven_day"}
            and window.scope == "all"
            and window.used_percent is not None
            and window.used_percent >= 100
            and window.resets_at is not None
            and window.resets_at > now
        ):
            return _claude_usage_limit.UsageLimitState("rejected", "seven_day", int(window.resets_at))
    return None


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
    on_residual: Callable[[process_tree.ReclaimError], None] | None = None,
    codex_provider: str | None = None,
    resume_request_id: str | None = None,
    claude_connection: _connection.Connection | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """既存のCLI待機で実行主体を保持し、上限停止と無出力停止を監視する。

    Codexの`codex_provider`は再開時に同じ接続を使うため、セッション記録へ保存する。
    """
    started_at = time.time()
    last_usage_check = time.monotonic()
    rollout: pathlib.Path | None = None
    published: str | None = None
    # 時間上限なし: 長時間の子セッションの実行そのものであり、無出力の監視と中断は呼び出し側の待機ループが扱う。
    with contextlib.ExitStack() as resources, process_tree.ProcessIdentityPins() as pins:
        stream_reader = None
        stream_buffer = b""
        stdout = None
        stream_path: pathlib.Path | None = None
        streamed = (orchestrator == "codex" and "--json" in argv) or (orchestrator == "agy" and "-p" in argv)
        if streamed:
            stream_path = managed_temp.create_output_temp("process-loop-stream") / "stdout.jsonl"
            stdout = resources.enter_context(stream_path.open("wb"))
            stream_reader = resources.enter_context(stream_path.open("rb"))
            print(f"{orchestrator}の実行出力: {stream_path}", flush=True)

        def receive_identity() -> bool:
            nonlocal stream_buffer
            if stream_reader is None:
                return False
            chunk = stream_reader.read(64 * 1024)
            stream_buffer += chunk
            lines = stream_buffer.split(b"\n")
            stream_buffer = lines.pop()
            if not chunk and stream_buffer and process is not None and process.poll() is not None:
                lines.append(stream_buffer)
                stream_buffer = b""
            for raw in lines:
                line = raw.decode("utf-8", errors="replace")
                identifier = _codex_usage_limit.thread_id_from_stream_lines((line,)) if orchestrator == "codex" else None
                if orchestrator == "agy":
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    identifier = antigravity_cli.event_session_id(event) if isinstance(event, dict) else None
                if identifier is not None:
                    previous = env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV)
                    if previous is not None and previous != identifier:
                        raise ValueError("CLIが返した実会話IDが再開対象と一致しない")
                    env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = identifier
            return bool(chunk)

        process: subprocess.Popen[bytes] | None = None
        known: list[psutil.Process] = []
        try:
            with _control.resume_launch(resume_request_id), _process_loop_log.launch_instructions() as instructions:
                if instructions:
                    argv = [*argv[:-1], f"{argv[-1]}\n\n追加指示:\n{instructions}"]
                process = resources.enter_context(
                    subprocess.Popen(
                        argv, env=env, cwd=cwd, stdout=stdout, creationflags=_pl_env.session_creation_flags(orchestrator)
                    )
                )
            known = process_tree.collect_descendants(process.pid, pins=pins, include_root=True)
            identity = _registry.ProcessIdentity.of(process.pid)
            if identity is None:
                raise _next_action.ActionableError(
                    "統括CLIのPIDと起動時刻を取得できない",
                    next_action=recover_from_failure(
                        component="統括CLIの識別",
                        evidence="診断と実行元",
                        first_step="起動するCLIの実体を確認する",
                        retry="CLIを修復し、統括が未起動の場合だけ再試行する",
                    ),
                )
            if on_process_started is not None:
                on_process_started(identity)
            while True:
                for target in process_tree.collect_descendants(process.pid, pins=pins):
                    if target not in known:
                        known.append(target)
                receive_identity()
                captured = startup_identity.read(env)
                if captured is not None and captured.get("session_id"):
                    if captured.get("host") != orchestrator:
                        raise ValueError("SessionStartの実行engineが起動したCLIと一致しない")
                    env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = captured["session_id"]
                if (
                    orchestrator == "codex"
                    and not streamed
                    and rollout is None
                    and (captured is None or captured.get("session_id"))
                ):
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
                identifier = (
                    captured.get("session_id") if captured is not None else env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV)
                )
                if identifier is not None and identifier != published:
                    identity = _registry.ProcessIdentity.of(process.pid)
                    if orchestrator == "claude" and identity is not None and captured is None:
                        _attach_orchestrator_child(identifier, identity)
                    else:
                        _registry.publish(
                            identifier,
                            state=_registry.PublicationState.PENDING_RUNNING,
                            engine=orchestrator,
                            model=model,
                            effort=effort,
                            cwd=captured["cwd"] if captured is not None else str(cwd),
                            model_type="orchestrate",
                            claude_api_env_file=claude_connection.api_env_file if claude_connection is not None else None,
                            claude_connection=claude_connection.kind if claude_connection is not None else None,
                            claude_role="orchestrator" if orchestrator == "claude" else None,
                            codex_model_provider=codex_provider,
                            codex_subscription_provider=_codex_connection.subscription_provider_for(codex_provider),
                            processes=tuple(item for item in (_registry.owner_process(), identity) if item),
                        )
                    if on_session_started is not None:
                        on_session_started(identifier)
                    if captured is not None:
                        startup_identity.acknowledge(env)
                    published = identifier
                try:
                    returncode = process.wait(timeout=1)
                    if streamed and published is None:
                        more = receive_identity()
                        if more or env.get(_pl_env.PROCESS_LOOP_SESSION_ID_ENV) is not None:
                            continue
                        raise ValueError(f"CLIの実会話IDを取得できない。実行出力を確認する: {stream_path}")
                    if orchestrator == "claude":
                        _raise_claude_usage_limit(process, env, identifier, on_residual, known=known, pins=pins)
                    return subprocess.CompletedProcess(argv, returncode)
                except subprocess.TimeoutExpired:
                    if orchestrator == "claude":
                        _raise_claude_usage_limit(process, env, identifier, on_residual, known=known, pins=pins)
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
                    if (
                        orchestrator == "claude"
                        and identifier is not None
                        and claude_connection is not None
                        and time.time() - updated >= _claude_usage_limit.RECHECK_SECONDS
                        and time.monotonic() - last_usage_check >= _claude_usage_limit.RECHECK_SECONDS
                    ):
                        last_usage_check = time.monotonic()
                        limit = _unwaited_weekly_limit(claude_connection, env)
                        if limit is not None and process.poll() is None:
                            _stop_orchestrator(
                                process,
                                context="process-loop weekly usage limit",
                                on_residual=on_residual,
                                known=known,
                                pins=pins,
                            )
                            raise ClaudeUsageLimit(identifier, limit) from None
                    if time.time() - updated > 9600:
                        _process_loop_log.append("session_stalled", session_id=identifier or "")
                        _stop_orchestrator(
                            process,
                            context="process-loop stalled",
                            on_residual=on_residual,
                            known=known,
                            pins=pins,
                        )
                        return subprocess.CompletedProcess(argv, 1)
                    if orchestrator == "codex" and rollout is not None and _codex_usage_limit.from_rollout(rollout) is not None:
                        _process_loop_log.append("session_usage_limit", rollout_path=str(rollout))
                        _stop_orchestrator(
                            process,
                            context="process-loop usage-limit",
                            on_residual=on_residual,
                            known=known,
                            pins=pins,
                        )
                        return subprocess.CompletedProcess(argv, 0)
        except BaseException as error:
            if process is not None and not isinstance(error, (ClaudeUsageLimit, process_tree.ReclaimError)):
                _stop_orchestrator(
                    process,
                    context="process-loop exception",
                    on_residual=on_residual,
                    known=known,
                    pins=pins,
                )
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
    on_residual: Callable[[process_tree.ReclaimError], None] | None = None,
    codex_provider: str | None = None,
    claude_connection: _connection.Connection | None = None,
) -> bool:
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
            on_residual=on_residual,
            codex_provider=codex_provider,
            claude_connection=claude_connection,
        )
    finally:
        finished(False)
