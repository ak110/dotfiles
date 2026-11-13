"""`atk run-skill`: 定期実行から任意のスキルを自律モードの最上位セッションとして1回実行する。

cron、systemd timer、Windowsのタスクスケジューラーなど端末を持たない起動元が1行で呼ぶコマンドとする。
`claude`コマンドを直接書く場合に必要になる非対話起動、モデル選択、利用上限の待機、多重起動の防止、時間上限、
ログ保存、自律モードの印付けおよび終了コードを本コマンドが吸収する。

モデル候補の解決、可用性判定と利用上限の待機は`atk wi process-loop`と同じ`orchestrator`の関数を呼ぶ。
子セッションには`atk wi process-loop`用の環境印を渡さない。その印は対話型のCLI本体を停止させる終了工程を
Stopの条件にするhookを有効にし、自ら最終応答で終わる非対話セッションを時間上限まで終われなくするためである。
報告用UWIはセッション内の`agent-toolkit:report-completion`が作成し、本コマンドは後処理で報告を作成しない。
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import functools
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import threading
import time
import typing
import uuid

# ヘルプや他の処理で使わない依存は、使用時まで読み込まない。
lazy import filelock

from agent_toolkit._atk import claude_connection as _connection
from agent_toolkit._atk import orchestrator as _orchestrator
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk import usage_limit_policy
from agent_toolkit._atk.run_skill_progress import Progress
from agent_toolkit._atk.wi import add as _wi_add
from agent_toolkit._atk.wi.repo import resolve_local_worktree, resolve_repo_id_or_raise
from agent_toolkit._common import antigravity_cli as _antigravity
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._common import claude_connection as _claude_settings
from agent_toolkit._common import claude_usage_limit as _claude_usage_limit
from agent_toolkit._common import codex_usage_limit as _codex_usage_limit
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import diagnostic_process, mcp_environment, recorded_process, session_review, skill_modes
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import private_notes as _private_notes
from agent_toolkit._common import process_tree as _process_tree
from agent_toolkit._common import session_registry as _registry
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.diagnostic_redaction import redact_secrets
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import command as _git_command
lazy from agent_toolkit._atk import run_skill_state as _run_state

DEFAULT_TIMEOUT_SECONDS = 21600
"""セッションの時間上限の省略時の値（6時間）。数日に1回の定期実行で1回のスキル実行が収まる長さとする。"""

LOG_RETENTION_SECONDS = 30 * 24 * 60 * 60
"""ログを残す期間（30日）。

数日に1回の起動で同じ組の直近約10回分の失敗を調べられる長さとする。
各実行の開始時にこの期間を超えたログを削除し、ログの数は起動間隔と期間で決まる件数に収まる。
"""

TIMEOUT_EXIT_CODE = 124
"""時間上限を超えた場合の終了コード。`timeout`コマンドと同じ値とする。"""

CONCURRENT_EXIT_CODE = 1
"""同じ対象リポジトリと同じスキルの実行が進行中の場合の終了コード。"""

OUTPUT_DRAIN_SECONDS = 5.0
"""子セッションの終了後に、残りの出力をログへ写し終えるまで待つ上限（秒）。

子セッションが起動した子孫が出力のパイプを保持したまま残ると、出力の終了が届かず読み取りが終わらない。
待ちに上限を置き、`--timeout`の外で待ち続けないようにする。終了した子セッションの出力はパイプへ書き込み済みであり、
上限はそれを読み終える余裕として置く。
"""

_TITLE = "atk run-skill"
_USAGE_LIMIT_RESUME_PROMPT = _automated_prompt.wrap(
    "中断前の要求・作業契約と成果を確認し、同じ実行の最初の未完了工程から作業を続けてください。"
    "完了済みの操作はその結果を引き継いでください。",
    source=_automated_prompt.SOURCE_RUN_SKILL,
    kind="usage-limit-resume",
)
_LOG_DIRNAME = "run-skill"
_PATH_ACTION = (
    "定期実行の環境変数`PATH`へ`atk`・`claude`・`codex`の実行ファイルの場所を加える（crontabでは`PATH=...`の行を置く）。"
    "PATHに問題が無い場合は`atk config set orchestrate_engine <engine順>`か"
    "`atk config set <engine>_execute_model <モデル>`で候補を変える"
)


def build_parser(parser: argparse.ArgumentParser) -> None:
    """`atk run-skill`の引数を登録する。"""
    parser.add_argument(
        "--target-repo",
        metavar="REPO",
        default=None,
        help="対象リポジトリの作業ツリーのパス。省略時は現在の作業リポジトリとする。セッションはその作業ツリーのrootで起動する。",
    )
    parser.add_argument("--work-dir", metavar="PATH", help="実行cwd。指定時は--target-repoに報告先repo URLも受理する。")
    parser.add_argument(
        "--timeout",
        metavar="SECONDS",
        type=int,
        default=None,
        help=(
            f"セッションの起動からの時間上限の秒数。省略時は{DEFAULT_TIMEOUT_SECONDS}秒。"
            "可用性判定と利用上限の解除待ちの時間は含めない。"
        ),
    )
    parser.add_argument(
        "--args",
        metavar="TEXT",
        default=None,
        help="スキルへ渡す任意の文字列。process-wiは先頭語single-lane/all-repos、review-sessionはbatchの完全一致でモードを選び、残りを自由記述として保持する。",
    )
    parser.add_argument("--resume", metavar="RUN_ID", help="保存した実行識別子の会話を、元の入力・報告先と残時間で再開する。")
    parser.add_argument(
        "skill",
        nargs="?",
        metavar="SKILL",
        help="実行するスキル名（`agent-toolkit:process-wi`のような修飾名、またはプロジェクトのスキル名）。",
    )


def dispatch(args: argparse.Namespace) -> int:
    """スキルを1回実行し、終了コードを返す。"""
    if identifier := getattr(args, "resume", None):
        try:
            record = _run_state.load(identifier)
            if any(getattr(args, name, None) is not None for name in ("target_repo", "work_dir", "timeout", "args")):
                raise ValueError("--resumeでは元の対象・cwd・時間予算・スキル引数を変更できない")
            if args.skill is not None and args.skill != record.input.skill:
                raise ValueError("--resumeのスキルが元の実行と一致しない")
        except (OSError, ValueError) as error:
            _outcome.report_failure(
                str(error), next_action="ログの実行識別子と保存した入力を確認し、--resume <RUN_ID>で再実行する"
            )
            return 2
        if record.status == "completed" and record.input.batch_manifest is None:
            _outcome.report_success(f"既に完了した実行: {record.input.run_id}（ログ: {record.log_path}）")
            return 0
        args.skill, args.args = record.input.skill, record.input.arguments
        args.target_repo = record.input.target_repo or str(record.work_dir)
        args.work_dir = str(record.work_dir) if record.input.target_repo is not None else None
        args.timeout, args.run_id = record.remaining, record.input.run_id
        args.batch_manifest, args.resume_record = record.input.batch_manifest, record
        if args.batch_manifest is not None:
            from agent_toolkit._atk import run_skill_batch  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

            return run_skill_batch.dispatch(args, _dispatch_once)
        return _dispatch_once(args)
    if args.skill is None:
        _outcome.report_failure("スキル名が必要", next_action="スキル名または--resume <RUN_ID>を指定する")
        return 2
    if getattr(args, "timeout", None) is None:
        args.timeout = DEFAULT_TIMEOUT_SECONDS
    if skill_modes.invocation_scope(args.skill, args.args) == session_review.BATCH_SCOPE:
        from agent_toolkit._atk import run_skill_batch  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        return run_skill_batch.dispatch(args, _dispatch_once)
    return _dispatch_once(args)


def _dispatch_once(args: argparse.Namespace) -> int:
    """batchと通常起動で共有する既存の1回分を実行する。"""
    record = getattr(args, "resume_record", None)
    if args.timeout <= 0 and record is None:
        _outcome.report_failure(
            f"--timeoutは正の秒数で指定する（指定値: {args.timeout}）",
            next_action=f"--timeoutを省略して{DEFAULT_TIMEOUT_SECONDS}秒とするか、正の秒数を指定する",
        )
        return 2
    candidates = (
        [(record.engine, record.model, record.effort)]
        if record is not None
        else _orchestrator.resolve_specs(rerun_action="`atk run-skill`を再実行する")
    )
    work_dir = getattr(args, "work_dir", None)
    if record is not None:
        repo_root = record.work_dir
        if not repo_root.is_dir():
            _outcome.report_failure(
                f"再開するcwdが存在しない: {repo_root}", next_action="元の作業ツリーを復元して同じ実行を再開する"
            )
            return 2
    elif work_dir is not None:
        repo_root = pathlib.Path(work_dir).expanduser()
        if not repo_root.is_absolute() or not repo_root.is_dir() or not args.target_repo:
            _outcome.report_failure(
                "--work-dirは既存の絶対ディレクトリを--target-repoと併せて指定する",
                next_action="報告先repoと実行cwdを確認して再実行する",
            )
            return 2
        repo_root = repo_root.resolve()
        args.target_repo = resolve_repo_id_or_raise(args.target_repo)
    else:
        repo_root = _resolve_repo_root(resolve_local_worktree(args.target_repo))
    log_dir = _state_paths.state_dir() / _LOG_DIRNAME
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        _remove_expired_logs(log_dir, now=time.time())
    except (OSError, ValueError) as error:
        _outcome.report_failure(str(error), next_action="run-skill/runsの実行記録とログの保存先を確認して再実行する")
        return 2
    log_path = (
        record.log_path if record is not None else _new_log_path(log_dir, skill_modes.invocation_scope(args.skill, args.args))
    )
    args.log_path = log_path
    if manifest_path := getattr(args, "batch_manifest", None):
        manifest = session_review.read(pathlib.Path(manifest_path))
        manifest["log_path"] = str(log_path)
        session_review.write(pathlib.Path(manifest_path), manifest)
    with log_path.open("a" if record is not None else "w", encoding="utf-8") as log:
        progress = Progress(log, sys.stderr)
        progress.message(f"ログ: {log_path.resolve()}")
        _write_header(log, repo_root=repo_root, args=args)
        lock = filelock.FileLock(
            str(
                _lock_path(
                    repo_root,
                    skill_modes.invocation_scope(args.skill, args.args),
                    target_repo=args.target_repo if getattr(args, "work_dir", None) is not None else None,
                )
            )
        )
        with contextlib.ExitStack() as resources:
            try:
                resources.enter_context(lock.acquire(timeout=getattr(args, "lock_timeout", 0)))
            except filelock.Timeout:
                args.run_skill_concurrent = True
                _log_line(log, "終了状態: 同じ対象リポジトリと同じスキルの実行が進行中のため起動しなかった")
                _outcome.report_failure(
                    f"同じ対象リポジトリと同じスキルの実行が進行中のため起動しなかった（ログ: {log_path}）",
                    next_action="進行中の実行の終了を確認してから、`atk run-skill`を元の引数で再実行する",
                )
                return CONCURRENT_EXIT_CODE
            with _console_title.console_title(_TITLE):
                return _run_locked(
                    args, repo_root=repo_root, candidates=candidates, log=log, log_path=log_path, progress=progress
                )


def build_goal(skill: str, skill_args: str | None) -> str:
    """起動したセッション自身が担当するスキルとモードの工程の完遂を目的にする。

    工程・終了条件を目的文へ加えるとスキルと定義が二重化し、各ターンの目標評価へ
    再掲される。起動データと終了契約の参照先はSessionStartで渡す。
    目標の評価は目的文の字義で達成を判定するため、主語をセッション自身に限り、
    子の終了後に起動元が行う検収と確定を達成範囲から外すことを短く示す。
    """
    scope = skill_modes.invocation_scope(skill, skill_args)
    return f"このセッションが担当するスキル`{scope}`の工程を完遂する（起動元が終了後に行う検収と確定は含まない）"


def _run_locked(
    args: argparse.Namespace,
    *,
    repo_root: pathlib.Path,
    candidates: list[tuple[str, str, str]],
    log: typing.TextIO,
    log_path: pathlib.Path,
    progress: Progress,
) -> int:
    """ロックを取得した状態で候補を選び、子セッションを実行する。"""
    record = _run_state.load(args.run_id) if getattr(args, "resume_record", None) is not None else None
    remaining = float(args.timeout)
    if record is not None:
        if record.status == "completed":
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
            min(record.remaining, max(0.0, record.deadline - time.time())) if record.deadline is not None else record.remaining
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
    if getattr(args, "work_dir", None) is not None:
        env[session_review.TARGET_ENV] = args.target_repo
    if manifest := getattr(args, "batch_manifest", None):
        env[session_review.BATCH_ENV] = manifest
    _log_line(log, f"実行識別子: {run_id}")

    def record_wait(candidate: str, usage_limit: _orchestrator.UsageLimitState, delay: float) -> None:
        _log_line(log, f"利用上限の解除待ち: {candidate} {usage_limit.describe()}（{int(delay)}秒）")

    def notify(message: str, next_action: str | None) -> None:
        # 標準出力は成功行1行だけとし、cronが標準出力を捨てても失敗時だけ通知が届くよう、判定の経過はログへ書く。
        _log_line(log, message if next_action is None else f"{message}（次の操作: {next_action}）")

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
    try:
        orchestrator, model, effort, _, connection = _orchestrator.select_available_with_provider(
            candidates, env, repo_root, context
        )
    except _orchestrator.NoAvailableCandidateError as error:
        args.batch_failure = f"availability-{error.orchestrator}-{error.returncode}: {error.detail}"
        _log_line(log, f"終了状態: 全てのモデル候補の可用性判定に失敗した（{error.orchestrator}: {error.detail}）")
        _outcome.report_failure(
            f"全てのモデル候補の可用性判定に失敗したため起動しなかった（ログ: {log_path}）",
            next_action=_PATH_ACTION,
        )
        return error.returncode or 1
    candidate = f"{orchestrator}:{model}/{effort}"
    _log_line(log, f"採用した候補: {candidate}")
    goal = _automated_prompt.wrap(
        build_goal(args.skill, args.args),
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
        # 非対話CLIは最終応答で終了するため、子の結果を同じターンで回収させる。
        # このenvは利用上限解除後の同一会話の再開でも使う。
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
            _USAGE_LIMIT_RESUME_PROMPT if record is not None else f"/goal {goal}",
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
        # Codexの非対話起動は`/goal`を目標として扱わず通常の入力として残すため、目的文だけを渡す。
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
            }
        )
    )
    while True:
        output = _SessionOutput()

        def completed(current: _SessionOutput = output) -> bool:
            return current.exit_code == 0 and current.usage_limit is None and not getattr(args, "batch_manifest", None)

        with (
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
                    # 時間上限なし: 長時間の子セッションの実行そのものであり、上限と無出力の監視は監督の仕組みが扱う。
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
                _log_line(log, f"終了状態: {orchestrator}を起動できなかった（{error}）")
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
            scope = skill_modes.invocation_scope(args.skill, args.args)
            allow_connection_switch = (
                orchestrator == "claude" and len(_connection.candidates("orchestrator", env=env, cli=True).connections) > 1
            )
            # ここからは_session_readersが所有する。読取中のcloseはEOF待ちの上限を壊すため解除する。
            unread_streams.pop_all()
            output.exit_code = _wait_session(
                process,
                orchestrator=orchestrator,
                timeout=remaining,
                log=log,
                log_path=log_path,
                progress=progress,
                output=output,
                codex_home=codex_home,
                run_id=run_id,
                skill=scope,
                executor=executor,
                allow_connection_switch=allow_connection_switch,
            )
        remaining = writer.record.remaining
        code = output.exit_code
        assert code is not None
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
            delay = usage_limit.delay_seconds(time.time())
            record_wait(candidate, usage_limit, delay)
            time.sleep(delay)
        if orchestrator == "claude":
            # 初回の明示IDを再開にも使い、同じ会話の履歴を引き継ぐ。
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
    """表示設定から独立して、継続に要る構造化出力だけを保持する。"""

    thread_id: str | None = None
    usage_limit: _orchestrator.UsageLimitState | None = None
    on_thread_id: typing.Callable[[str], None] | None = None
    exit_code: int | None = None

    def receive(self, line: str) -> None:
        """stdoutのイベントを取り込む。"""
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
        conversation = _antigravity.event_session_id(event)
        if conversation is not None and conversation != self.thread_id:
            self.thread_id = conversation
            if self.on_thread_id is not None:
                self.on_thread_id(conversation)
        state = _claude_usage_limit.from_event(event)
        if state is not None:
            self.usage_limit = state if state.status == "rejected" else None


@contextlib.contextmanager
def _session_readers(
    process: subprocess.Popen[bytes],
    *,
    orchestrator: str,
    log: typing.TextIO,
    progress: Progress,
    output: _SessionOutput,
    executor: _registry.ProcessIdentity | None,
) -> typing.Generator[tuple[threading.Lock, list[threading.Thread]]]:
    """読取スレッドを起動し、準備失敗と中断でも子とパイプを回収する。"""
    lock = threading.Lock()
    readers: list[threading.Thread] = []
    streams = (process.stdout, process.stderr)
    try:
        assert process.stdout is not None and process.stderr is not None
        readers = [
            threading.Thread(
                target=_copy_stream, args=(process.stdout, "stdout", log, lock, progress, orchestrator, output), daemon=True
            ),
            threading.Thread(target=_copy_stream, args=(process.stderr, "stderr", log, lock), daemon=True),
        ]
        for reader in readers:
            reader.start()
        yield lock, readers
    except BaseException:
        try:
            if process.poll() is None:
                _process_tree.log_residual(_stop_executor(process, executor), context="run-skill")
        finally:
            _drain_output([reader for reader in readers if reader.ident is not None], log, lock)
        raise
    finally:
        # 起動した読取スレッドがパイプを閉じる。未起動の側だけを主スレッドが閉じる。
        with contextlib.ExitStack() as cleanup:
            for index, stream in enumerate(streams):
                if stream is not None and (index >= len(readers) or readers[index].ident is None):
                    cleanup.callback(stream.close)


def _stop_executor(process: subprocess.Popen[bytes], executor: _registry.ProcessIdentity | None) -> list[str]:
    """子セッションを子孫ごと停止し、回収できなかった所有対象を同じ実行の再開時の停止へ引き継ぐ。

    子が終了すると残った子孫は子から辿れず、再開時の`stop_recorded_executor`は記録が無ければ停止済みと判定する。
    回収待ちとして記録し、再開前に同じ対象の回収を再試行させる。
    """
    failure = _process_tree.stop_owned_process(process, context="run-skill")
    if failure is None:
        return []
    if executor is not None:
        recorded_process.retain_unreclaimed(executor, failure)
    return failure.residual


def _wait_session(
    process: subprocess.Popen[bytes],
    *,
    orchestrator: str,
    timeout: float,
    log: typing.TextIO,
    log_path: pathlib.Path,
    progress: Progress,
    output: _SessionOutput,
    codex_home: pathlib.Path,
    run_id: str,
    skill: str,
    executor: _registry.ProcessIdentity | None,
    allow_connection_switch: bool = False,
) -> int:
    """子セッションの終了か時間上限を待ち、結果行を出力して終了コードを返す。"""
    with _session_readers(process, orchestrator=orchestrator, log=log, progress=progress, output=output, executor=executor) as (
        lock,
        readers,
    ):
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                try:
                    returncode = process.wait(timeout=min(1.0, remaining))
                    break
                except subprocess.TimeoutExpired:
                    with lock:
                        limit = output.usage_limit
                        limited = (
                            isinstance(limit, _claude_usage_limit.UsageLimitState)
                            and limit.status == "rejected"
                            and (allow_connection_switch or usage_limit_policy.should_wait(orchestrator, limit.limit_type))
                        )
                    if limited:
                        residual = _stop_executor(process, executor)
                        if residual:
                            raise ActionableError(
                                f"接続切替前に旧CLIの終了を確認できない: {residual}",
                                next_action="記録したPID世代の旧CLIの終了を確認して同じ実行を再開する",
                            ) from None
                        returncode = process.returncode or 1
                        break
        except subprocess.TimeoutExpired:
            residual = _stop_executor(process, executor)
            _drain_output(readers, log, lock)
            with lock:
                _log_line(log, f"終了状態: 時間上限（{timeout:g}秒）を超えたため終了させた")
                if residual:
                    _log_line(log, f"終了させられなかったプロセス: {residual}")
            if residual:
                # 停止を確認できない時間上限は、次回の実行が同じ資源を奪い合わないよう通常の時間上限と分けて伝える。
                _outcome.report_failure(
                    f"セッションが時間上限（{timeout:g}秒）を超え、子プロセスの停止を確認できない: {residual}"
                    f"（ログ: {log_path}）",
                    next_action=(
                        "表示されたPIDと起動コマンドを確かめ、不要なら終了させる。"
                        f"`atk run-skill --resume {run_id}`は再開前に同じ対象の回収を再試行し、回収を確認できるまで再開しない"
                    ),
                )
                return 1
            _outcome.report_failure(
                f"セッションが時間上限（{timeout:g}秒）を超えたため終了させた（ログ: {log_path}）",
                next_action=(
                    "ログで進行状況を確かめ、必要なら`--timeout`を延ばして、"
                    "`atk run-skill`を元の対象・スキル・スキル引数で再実行する"
                ),
            )
            return TIMEOUT_EXIT_CODE
        _drain_output(readers, log, lock)
        with lock:
            _log_line(log, f"終了状態: exit code {returncode}")
            if orchestrator == "codex" and returncode != 0:
                output.usage_limit = _codex_usage_limit.from_thread(output.thread_id, codex_home=codex_home)
            if (
                output.usage_limit is not None
                and not allow_connection_switch
                and not usage_limit_policy.should_wait(orchestrator, output.usage_limit.limit_type)
            ):
                output.usage_limit = None
            if output.usage_limit is not None:
                return returncode
        if _orchestrator.is_normal_session_exit(orchestrator, returncode):
            try:
                saved = _wi_add.has_synced_run_report(_private_notes.default_private_notes(), run_id=run_id, skill=skill)
            except (OSError, subprocess.SubprocessError) as error:
                saved = False
                _log_line(log, f"報告の保存確認に失敗した: {error}")
            if not saved:
                _outcome.report_failure(
                    f"報告用UWIの保存が同期契約へ到達していない（ログ: {log_path}）",
                    next_action=(
                        "ログの実行識別子に対応する報告用UWIを確認し、同じ会話を再開してreport-completionで保存する。"
                        "保存済みの場合はWI保存先のcommit・pushを完了してから次回の実行を待つ"
                    ),
                )
                return 1
            _outcome.report_success(f"スキルを実行した（ログ: {log_path}）")
            return 0
        exit_code = returncode if returncode > 0 else 128 - returncode
        _outcome.report_failure(
            f"{orchestrator}のセッションがexit code {returncode}で異常終了した（ログ: {log_path}）",
            next_action=(
                "ログとセッション記録（Claude Codeは`~/.claude/projects`配下、Codexは`~/.codex/sessions`配下）で原因を確かめ、"
                "解消してから`atk run-skill`を元の引数で再実行する"
            ),
        )
        return exit_code


def _drain_output(readers: list[threading.Thread], log: typing.TextIO, lock: threading.Lock) -> None:
    """子セッションの終了後、残りの出力の読み取りを上限付きで待つ。"""
    deadline = time.monotonic() + OUTPUT_DRAIN_SECONDS
    for reader in readers:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    if any(reader.is_alive() for reader in readers):
        with lock:
            _log_line(
                log,
                f"出力の読み取りを打ち切った: 子セッションの終了から{OUTPUT_DRAIN_SECONDS:g}秒以内にEOFが届かなかった"
                "（出力のパイプを保持したまま残る子孫プロセスがある）。"
                "次の操作: 当該セッションの記録で子孫の実行識別子を確認し、終了状態を回収してから再実行する",
            )


def _copy_stream(
    stream: typing.IO[bytes],
    label: str,
    log: typing.TextIO,
    lock: threading.Lock,
    progress: Progress | None = None,
    orchestrator: str = "",
    output: _SessionOutput | None = None,
) -> None:
    """子の出力を行ごとにログへ写し、読み取りを終えた側がstreamを閉じる。

    主スレッドからcloseすると読み取り中のロックを待ち、出力待機の上限を超えるため行わない。
    """
    private_key = False
    with stream:
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            with lock:
                if log.closed:
                    # 読み取りを打ち切った後に届いた出力は、閉じたログへ書けないため捨てる。
                    return
                diagnostic = label == "stderr" or _error_event(line)
                saved = line
                if diagnostic:
                    saved, private_key = diagnostic_process.redact_stream_line(line, private_key)
                log.write(f"[{label}] {saved}\n")
                log.flush()
                if output is not None:
                    output.receive(line)
                if progress is not None:
                    progress.receive(orchestrator, line)


def _resolve_repo_root(path: pathlib.Path) -> pathlib.Path:
    """指定パスが属する作業ツリーのrootを返す。Git管理外ならパスをそのまま使う。"""
    result = _git_command.run(["-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        _outcome.report_failure(
            f"対象リポジトリの作業ツリーを解決できない: {path}（git: {result.stderr.strip()}）",
            next_action="`--target-repo`へGitの作業ツリーのパスを指定する",
        )
        sys.exit(2)
    return pathlib.Path(result.stdout.strip()).resolve()


def _lock_path(repo_root: pathlib.Path, skill: str, *, target_repo: str | None = None) -> pathlib.Path:
    """対象リポジトリとスキルの組ごとのロックファイルのパスを、ロックファイルのディレクトリ配下に返す。"""
    from agent_toolkit._git import remote  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    target = target_repo or remote.resolve_repo_identifier(str(repo_root)) or str(repo_root)
    digest = hashlib.sha256(f"{target}\0{skill}".encode()).hexdigest()[:16]
    lock_dir = _state_paths.lock_dir() / _LOG_DIRNAME
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{digest}.lock"


def _new_log_path(log_dir: pathlib.Path, skill: str) -> pathlib.Path:
    """実行ごとに重複しないログファイルのパスを返す。"""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_skill = re.sub(r"[^A-Za-z0-9._-]+", "_", skill)
    return log_dir / f"{stamp}-{safe_skill}-{uuid.uuid4().hex[:8]}.log"


def _remove_expired_logs(log_dir: pathlib.Path, *, now: float) -> None:
    """保持期間を超えたログを削除する。削除できないログは次回の実行で再び試す。"""
    protected = session_review.protected_logs() | _run_state.sweep_records(cutoff=now - LOG_RETENTION_SECONDS)
    for manifest_path in (_state_paths.state_dir() / "wi-submit" / "runs").glob("*/run.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("status") != "completed"
            or not manifest.get("report_synced")
            or manifest["updated_at"] >= now - LOG_RETENTION_SECONDS
        ):
            protected.update(pathlib.Path(path) for path in manifest.get("run_skill_logs", []))
    for path in log_dir.glob("*.log"):
        if path in protected:
            continue
        try:
            if now - path.stat().st_mtime > LOG_RETENTION_SECONDS:
                path.unlink()
        except OSError:
            continue


def _write_header(log: typing.TextIO, *, repo_root: pathlib.Path, args: argparse.Namespace) -> None:
    """実行の条件をログの先頭へ書く。"""
    _log_line(log, f"開始時刻: {datetime.datetime.now().astimezone().isoformat(timespec='seconds')}")
    _log_line(log, f"対象リポジトリ: {repo_root}")
    _log_line(log, f"スキル: {args.skill}")
    _log_line(log, f"スキルへ渡す引数: {args.args if args.args is not None else 'なし'}")
    _log_line(log, f"時間上限: {args.timeout}秒")


def _log_line(log: typing.TextIO, text: str) -> None:
    """ログへ1行書いて直ちに反映する。"""
    log.write(f"{redact_secrets(text)}\n")
    log.flush()


def _error_event(line: str) -> bool:
    try:
        event = json.loads(line)
    except ValueError:
        return False
    return isinstance(event, dict) and (
        event.get("is_error") is True
        or event.get("type") in {"error", "turn.failed"}
        or isinstance(event.get("error"), (dict, str))
    )
