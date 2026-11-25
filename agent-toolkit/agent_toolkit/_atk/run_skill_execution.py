from __future__ import annotations

import argparse
import contextlib
import dataclasses
import functools
import json
import pathlib
import subprocess
import typing
import uuid

from agent_toolkit._atk import claude_connection as _connection
from agent_toolkit._atk import orchestrator as _orchestrator
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk import usage_limit_policy, usage_refresh
from agent_toolkit._atk.run_skill_progress import Progress
from agent_toolkit._common import antigravity_cli as _antigravity
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._common import claude_connection as _claude_settings
from agent_toolkit._common import claude_usage_limit as _claude_usage_limit
from agent_toolkit._common import codex_usage_limit as _codex_usage_limit
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import mcp_environment, recorded_process, session_review, skill_modes, weekly_work
from agent_toolkit._common import process_tree as _process_tree
from agent_toolkit._common import session_registry as _registry
from agent_toolkit._common.diagnostic_redaction import redact_secrets
from agent_toolkit._common.next_action import ActionableError
lazy from agent_toolkit._atk import run_skill_state as _run_state

TIMEOUT_EXIT_CODE = 124
USAGE_LIMIT_PAUSE_EXIT_CODE = 86
"""時間上限を超えた場合の終了コード。`timeout`コマンドと同じ値とする。"""

_TITLE = "atk run-skill"
_USAGE_LIMIT_RESUME_PROMPT = _automated_prompt.wrap(
    "中断前の要求・作業契約と成果を確認し、同じ実行の最初の未完了工程から作業を続けてください。"
    "完了済みの操作はその結果を引き継いでください。",
    source=_automated_prompt.SOURCE_RUN_SKILL,
    kind="usage-limit-resume",
)
_PATH_ACTION = (
    "定期実行の環境変数`PATH`へ`atk`・`claude`・`codex`の実行ファイルの場所を加える（crontabでは`PATH=...`の行を置く）。"
    "PATHに問題が無い場合は`atk config set orchestrate_engine <engine順>`か"
    "`atk config set <engine>_execute_model <モデル>`で候補を変える"
)


def build_goal(skill: str, skill_args: str | None, *, launch: str | None = None) -> str:
    """起動したセッション自身が担当するスキルとモードの工程の完遂と、その始め方を目的にする。

    工程・終了条件を目的文へ加えるとスキルと定義が二重化するため、起動データと終了契約の参照先はSessionStartで渡す。
    完了はStop hookが報告用UWIの保存で判定するため、主語をセッション自身に限り、
    子の終了後に起動元が行う検収と確定を担当範囲から外すことを短く示す。

    始め方は全てのホストで目的文が示す（SessionStartは起動を差し込まない）。
    入口`atk-mode`の作業の名前（`skill_modes.ENTRY_WORKS`。修飾名と接頭辞の無い名前）は、入口を作業と引数で起動させ、
    それ以外はそのスキルの起動を書く。入口の起動は`dispatch`が作業の名前へ読み替えてから渡す。
    `launch`は、`skill`が保存上の作業名で同名のスキルを持たない起動（`atk wi submit`）が、作業の始め方を1文で渡す。
    起動単位の名前（ロック、ログ、報告用UWIの`scope`）は`skill`のまま保ち、スキルの実在に依存させない。
    """
    scope = skill_modes.invocation_scope(skill, skill_args)
    if launch is not None:
        return f"このセッションが担当する作業`{scope}`の工程を完遂する（起動元が終了後に行う検収と確定は含まない）。{launch}"
    if (name := skill_modes.entry_work(skill)) is not None:
        work = skill_modes.invocation_scope(name, skill_args).removeprefix("agent-toolkit:")
        start = skill_modes.entry_launch(work, free_text=True)
    else:
        start = f"スキル`{skill}`を起動して始める（起動入力の`free_text`が空でなければ引数として渡す）。"
    return f"このセッションが担当するスキル`{scope}`の工程を完遂する（起動元が終了後に行う検収と確定は含まない）。{start}"


def run_locked(
    args: argparse.Namespace,
    *,
    repo_root: pathlib.Path,
    candidates: list[tuple[str, str, str]],
    log: typing.TextIO,
    log_path: pathlib.Path,
    progress: Progress,
    wait_session: typing.Callable[..., int],
    clock: typing.Any,
) -> int:
    """ロックを取得した状態で候補を選び、子セッションを実行する。"""
    record = _run_state.load(args.run_id) if getattr(args, "resume_record", None) is not None else None
    remaining = float(args.timeout)
    scope = skill_modes.invocation_scope(args.skill, args.args)
    requires_weekly_result = scope in weekly_work.REQUIRED_RESULT_SCOPES
    if record is not None:
        if record.status == "completed" and not requires_weekly_result:
            _outcome.report_success(f"既に完了した実行: {record.input.run_id}（ログ: {record.log_path}）")
            return 0
        if record.session_id is None:
            _outcome.report_failure("元の会話IDが未記録", next_action=f"ログ{record.log_path}で起動の成否を確認する")
            return 2
        if record.executor is not None:
            failure = recorded_process.stop_recorded_executor(record.executor)
            if failure is not None:
                _outcome.report_failure(failure, next_action="記録した旧CLIの終了を確認して同じ実行を再開する")
                return 1
        remaining = (
            min(record.remaining, max(0.0, record.deadline - clock.time())) if record.deadline is not None else record.remaining
        )
        _run_state.RecordWriter(record).finish(remaining, completed=False)
        if remaining <= 0:
            _outcome.report_failure(
                "元の実行の時間予算が残っていない", next_action=f"ログ{record.log_path}で未完了作業を確認する"
            )
            return TIMEOUT_EXIT_CODE
    env = _orchestrator.child_env(
        drop=(
            _orchestrator.RESTART_SPEC_ENV,
            _orchestrator.PROCESS_LOOP_SESSION_ENV,
            _orchestrator.PROCESS_LOOP_SESSION_ID_ENV,
        )
    )
    run_id = getattr(args, "run_id", None) or uuid.uuid4().hex
    env["AGENT_TOOLKIT_RUN_SKILL_ID"] = run_id
    if requires_weekly_result:
        args.run_id = run_id
    if getattr(args, "work_dir", None) is not None:
        env[session_review.TARGET_ENV] = args.target_repo
    if manifest := getattr(args, "batch_manifest", None):
        env[session_review.BATCH_ENV] = manifest
    env.pop(session_review.WEEKLY_SLOT_ENV, None)
    slot = getattr(args, "weekly_slot", None)
    if slot:
        env[session_review.WEEKLY_SLOT_ENV] = slot
    log_line(log, f"実行識別子: {run_id}")

    def record_wait(candidate: str, usage_limit: _orchestrator.UsageLimitState, delay: float) -> None:
        log_line(log, f"利用上限の解除待ち: {candidate} {usage_limit.describe()}（{int(delay)}秒）")

    def notify(message: str, next_action: str | None) -> None:
        # 標準出力は成功行1行だけとし、cronが標準出力を捨てても失敗時だけ通知が届くよう、判定の経過はログへ書く。
        log_line(log, message if next_action is None else f"{message}（次の操作: {next_action}）")

    context = _orchestrator.ProbeContext(
        title=_TITLE,
        prompt=_automated_prompt.wrap(
            "応答できる場合はOKだけを返してください。",
            source=_automated_prompt.SOURCE_RUN_SKILL,
            kind=_automated_prompt.KIND_AVAILABILITY_PROBE,
        ),
        record_wait=record_wait,
        notify=notify,
        claude_connection=record.connection if record is not None else None,
    )
    if record is not None and record.engine == "claude" and getattr(args, "resume_usage_limited", False):
        orchestrator, model, effort, provider, connection = record.engine, record.model, record.effort, None, record.connection
    else:
        try:
            orchestrator, model, effort, provider, connection = _orchestrator.select_available_with_provider(
                candidates, env, repo_root, context
            )
        except _orchestrator.NoAvailableCandidateError as error:
            args.batch_failure = f"availability-{error.orchestrator}-{error.returncode}: {error.detail}"
            log_line(log, f"終了状態: 全てのモデル候補の可用性判定に失敗した（{error.orchestrator}: {error.detail}）")
            _outcome.report_failure(
                f"全てのモデル候補の可用性判定に失敗したため起動しなかった（ログ: {log_path}）",
                next_action=_PATH_ACTION,
            )
            return error.returncode or 1
    candidate = f"{orchestrator}:{model}/{effort}"
    log_line(log, f"採用した候補: {candidate}")
    goal = _automated_prompt.wrap(
        build_goal(args.skill, args.args, launch=getattr(args, "launch", None)),
        source=_automated_prompt.SOURCE_RUN_SKILL,
        kind=_automated_prompt.KIND_GOAL,
    )
    mode, free_text = skill_modes.split_mode(args.skill, args.args)
    env[mcp_environment.SESSION_START_INPUT_ENV] = json.dumps(
        {
            "source": "run-skill",
            "skill": args.skill,
            "mode": mode,
            "arguments": args.args,
            "free_text": free_text,
            "accepted_scopes": skill_modes.accepted_scopes(args.skill, args.args),
            "run_id": run_id,
            "target_repo": args.target_repo if getattr(args, "work_dir", None) is not None else None,
            "batch_manifest": getattr(args, "batch_manifest", None),
        },
        ensure_ascii=False,
    )
    if record is not None:
        env[mcp_environment.SESSION_START_INPUT_ENV] = record.input.model_dump_json()
    session_id: str | None = record.session_id if record is not None else None
    if orchestrator == "claude":
        if getattr(args, "batch_manifest", None):
            env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
        session_id = session_id or str(uuid.uuid4())
        progress.message(f"Claudeのセッション識別子: {session_id}")
        argv = [
            "claude",
            "-p",
            "--resume" if record is not None else "--session-id",
            session_id,
            "--permission-mode",
            "auto",
            "--permission-prompts",
            "none",
            "--model",
            model,
            "--effort",
            effort,
            _USAGE_LIMIT_RESUME_PROMPT if record is not None else goal,
        ]
        argv[-1:-1] = ["--output-format", "stream-json", "--verbose"]
    elif orchestrator == "agy":
        argv = _antigravity.build_command(
            _USAGE_LIMIT_RESUME_PROMPT if record is not None else goal,
            model,
            effort,
            session_id,
            timeout=max(1, int(remaining)),
        )
    else:
        argv = [
            "codex",
            "exec",
            *(["resume"] if record is not None else []),
            "--model",
            model,
            "-c",
            f"model_reasoning_effort={effort}",
            "--json",
            *([session_id] if session_id is not None else []),
            _USAGE_LIMIT_RESUME_PROMPT if record is not None else goal,
        ]
    log.flush()
    codex_home = _host_homes.codex_home(env)
    writer = _run_state.RecordWriter(
        _run_state.RunRecord.model_validate(
            {
                "input": _run_state.StartupInput.model_validate_json(env[mcp_environment.SESSION_START_INPUT_ENV]),
                "work_dir": repo_root,
                "log_path": log_path,
                "engine": orchestrator,
                "model": model,
                "effort": effort,
                "session_id": session_id,
                "connection": connection,
                "remaining": remaining,
                "weekly_slot": slot,
            }
        )
    )
    while True:
        output = _SessionOutput()

        def completed(current: _SessionOutput = output) -> bool:
            return (
                current.exit_code == 0
                and current.usage_limit is None
                and not getattr(args, "batch_manifest", None)
                and not requires_weekly_result
            )

        with (
            usage_refresh.cli_activity(orchestrator, model, effort, connection.kind if connection is not None else provider),
            writer.running(
                remaining,
                connection,
                completed=completed,
            ),
            contextlib.ExitStack() as resources,
        ):
            launch_env = _orchestrator.session_env(env, orchestrator)
            launch_argv = argv
            if orchestrator == "claude" and connection is not None:
                launch_env = _connection.connection_environment(launch_env, connection)
                settings = _claude_settings.merge_settings(
                    None,
                    _connection.connection_overrides(connection),
                    None,
                    force_file=bool(launch_env.get("ANTHROPIC_API_KEY") or launch_env.get("ANTHROPIC_AUTH_TOKEN")),
                    isolate_auth=connection.kind != "inherit",
                    isolation_keys=_connection.isolation_keys(connection),
                    cwd=repo_root,
                )
                if isinstance(settings, _claude_settings.SettingsFile):
                    resources.enter_context(settings)
                launch_argv = [*argv[:-1], "--settings", settings, argv[-1]]
            try:
                process = resources.enter_context(
                    _process_tree.process_scope(
                        launch_argv,
                        cwd=repo_root,
                        env=launch_env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        close_streams=False,
                    )
                )
            except OSError as error:
                args.batch_failure = f"spawn-{orchestrator}: {error}"
                log_line(log, f"終了状態: {orchestrator}を起動できなかった（{error}）")
                _outcome.report_failure(
                    f"{orchestrator}を起動できなかった: {error}（ログ: {log_path}）", next_action=_PATH_ACTION
                )
                return 1
            unread_streams = resources.enter_context(contextlib.ExitStack())
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    unread_streams.callback(stream.close)
            executor = _registry.ProcessIdentity.of(process.pid)
            if executor is None and process.poll() is None:
                raise ActionableError(
                    "起動したCLIのPID世代を保存できない", next_action="プロセス情報の参照権限を確認して同じ実行を再開する"
                )
            writer.attach(executor)
            output.on_thread_id = functools.partial(writer.set_session_id, executor=executor)
            if getattr(args, "batch_manifest", None):
                from agent_toolkit._atk import run_skill_batch  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

                run_skill_batch.record_executor(args, process.pid)
            allow_connection_switch = (
                orchestrator == "claude" and len(_connection.candidates("orchestrator", env=env, cli=True).connections) > 1
            )

            unread_streams.pop_all()
            output.exit_code = wait_session(
                process,
                orchestrator=orchestrator,
                timeout=remaining,
                log=log,
                log_path=log_path,
                progress=progress,
                output=output,
                codex_home=codex_home,
                run_id=run_id,
                scope=scope,
                executor=executor,
                allow_connection_switch=allow_connection_switch,
            )
        remaining = writer.record.remaining
        code = output.exit_code
        assert code is not None
        limit = output.paused_for_usage_limit
        if (
            limit is None
            and getattr(args, "resume_usage_limited", False)
            and output.usage_limit is not None
            and not usage_limit_policy.should_wait(orchestrator, output.usage_limit.limit_type)
        ):
            limit = output.usage_limit
        if limit is not None:
            notice = f"Claude Codeの利用上限（{limit.limit_type}）を検出したが、元の実行の時間予算が残っていない"
            action = f"ログ{log_path}で未完了の作業を確認する"
            if remaining > 0:
                now = clock.time()
                args.batch_usage_resume_at = now + limit.delay_seconds(now)
                action = f"`atk run-skill --resume {run_id}`で再開する"
                notice = limit.describe()
            log_line(log, f"終了状態: {notice}")
            log_line(log, f"次の操作: {action}")
            _outcome.report_failure(notice, next_action=action)
            return USAGE_LIMIT_PAUSE_EXIT_CODE if remaining > 0 else TIMEOUT_EXIT_CODE
        if code == TIMEOUT_EXIT_CODE or output.usage_limit is None:
            return code
        if remaining <= 0:
            _outcome.report_failure("元の実行の時間予算が残っていない", next_action=f"ログ{log_path}で未完了作業を確認する")
            return TIMEOUT_EXIT_CODE
        usage_limit = output.usage_limit
        alternatives = (
            tuple(
                item
                for item in _connection.candidates("orchestrator", env=env, cli=True).connections
                if connection is None or item.kind != connection.kind
            )
            if orchestrator == "claude"
            else ()
        )
        if alternatives:
            try:
                _, _, _, _, connection = _orchestrator.select_available_with_provider(
                    [(orchestrator, model, effort)],
                    env,
                    repo_root,
                    dataclasses.replace(context, claude_connection=connection),
                )
            except _orchestrator.NoAvailableCandidateError as error:
                _outcome.report_failure(error.detail, next_action=f"接続設定を確認して--resume {run_id}で再開する")
                return error.returncode or 1
        else:
            delay = usage_limit.delay_seconds(clock.time())
            record_wait(candidate, usage_limit, delay)
            clock.sleep(delay)
        if orchestrator == "claude":
            assert session_id is not None
            argv = [
                "claude",
                "-p",
                "--resume",
                session_id,
                "--permission-mode",
                "auto",
                "--permission-prompts",
                "none",
                "--model",
                model,
                "--effort",
                effort,
                "--output-format",
                "stream-json",
                "--verbose",
                _USAGE_LIMIT_RESUME_PROMPT,
            ]
        elif orchestrator == "agy":
            assert output.thread_id is not None
            argv = _antigravity.build_command(
                _USAGE_LIMIT_RESUME_PROMPT, model, effort, output.thread_id, timeout=max(1, int(remaining))
            )
        else:
            assert output.thread_id is not None
            argv = [
                "codex",
                "exec",
                "resume",
                "--model",
                model,
                "-c",
                f"model_reasoning_effort={effort}",
                "--json",
                output.thread_id,
                _USAGE_LIMIT_RESUME_PROMPT,
            ]


@dataclasses.dataclass
class _SessionOutput:
    thread_id: str | None = None
    usage_limit: _orchestrator.UsageLimitState | None = None
    on_thread_id: typing.Callable[[str], None] | None = None
    exit_code: int | None = None
    paused_for_usage_limit: _claude_usage_limit.UsageLimitState | None = None

    def receive(self, line: str) -> None:
        thread_id = _codex_usage_limit.thread_id_from_stream_lines((line,))
        if thread_id is not None and thread_id != self.thread_id:
            self.thread_id = thread_id
            if self.on_thread_id is not None:
                self.on_thread_id(thread_id)
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        if usage_refresh.enabled():
            usage_refresh.record_claude_event(event.get("rate_limit_info"))
            if isinstance(report := event.get("usage_report"), dict):
                usage_refresh.record_claude_report(report)
        conversation = _antigravity.event_session_id(event)
        if conversation is not None and conversation != self.thread_id:
            self.thread_id = conversation
            if self.on_thread_id is not None:
                self.on_thread_id(conversation)
        state = _claude_usage_limit.from_event(event)
        if state is not None:
            self.usage_limit = state if state.status == "rejected" else None


def log_line(log: typing.TextIO, text: str) -> None:
    log.write(f"{redact_secrets(text)}\n")
    log.flush()
