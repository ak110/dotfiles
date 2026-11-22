"""`atk run-skill`: 定期実行から任意のスキルを自律モードの最上位セッションとして1回実行する。

cron、systemd timer、Windowsのタスクスケジューラーなど端末を持たない起動元が1行で呼ぶコマンドとする。
`claude`コマンドを直接書く場合に必要になる非対話起動、モデル選択、利用上限の待機、多重起動の防止、時間上限、
ログ保存、自律モードの印付けおよび終了コードを本コマンドが吸収する。

モデル候補の解決、可用性判定と利用上限の待機は`atk wi process-loop`と同じ`orchestrator`の関数を呼ぶ。
子セッションには`atk wi process-loop`用の環境印を渡さない。その印は対話型のCLI本体を停止させる終了工程を
Stopの条件にするhookを有効にし、自ら最終応答で終わる非対話セッションを時間上限まで終われなくするためである。
報告用UWIはセッション内で入口`atk-mode`の完了報告の手順書（`playbooks/report-completion.md`）に従って作成し、
本コマンドは後処理で報告を作成しない。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
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

lazy import filelock

from agent_toolkit._atk import orchestrator as _orchestrator
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk import usage_limit_policy
from agent_toolkit._atk.run_skill_execution import (
    TIMEOUT_EXIT_CODE,
    USAGE_LIMIT_PAUSE_EXIT_CODE,
    _SessionOutput,
    run_locked,
)
from agent_toolkit._atk.run_skill_execution import (
    log_line as _log_line,
)
from agent_toolkit._atk.run_skill_progress import Progress
from agent_toolkit._atk.wi import add as _wi_add
from agent_toolkit._atk.wi.repo import resolve_local_worktree, resolve_repo_id_or_raise
from agent_toolkit._common import claude_usage_limit as _claude_usage_limit
from agent_toolkit._common import codex_usage_limit as _codex_usage_limit
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import diagnostic_process, recorded_process, session_review, skill_modes
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

CONCURRENT_EXIT_CODE = 1
"""同じ対象リポジトリと同じスキルの実行が進行中の場合の終了コード。"""

OUTPUT_DRAIN_SECONDS = 5.0
"""子セッションの終了後に、残りの出力をログへ写し終えるまで待つ上限（秒）。

子セッションが起動した子孫が出力のパイプを保持したまま残ると、出力の終了が届かず読み取りが終わらない。
待ちに上限を置き、`--timeout`の外で待ち続けないようにする。終了した子セッションの出力はパイプへ書き込み済みであり、
上限はそれを読み終える余裕として置く。
"""

_TITLE = "atk run-skill"
_LOG_DIRNAME = "run-skill"


def build_parser(parser: argparse.ArgumentParser) -> None:
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
        help=(
            "スキルへ渡す任意の文字列。先頭語がprocess-wiのsingle-lane/all-reposかreview-sessionのbatch/metaに"
            "完全一致する時はモードに使い、残りを自由記述として保持する。"
        ),
    )
    parser.add_argument("--resume", metavar="RUN_ID", help="保存した実行識別子の会話を、元の入力・報告先と残時間で再開する。")
    parser.add_argument(
        "skill",
        nargs="?",
        metavar="SKILL",
        help=(
            "実行するスキル名（`agent-toolkit:review-session`のような修飾名、またはプロジェクトのスキル名）。"
            f"入口`atk-mode`の作業は作業の修飾名（`{skill_modes.PROCESS_WI}`など）で指定する。"
        ),
    )


def dispatch(args: argparse.Namespace) -> int:
    explicit_arguments = args.args is not None
    if args.skill is not None:
        # 無修飾の作業名（`review-session`など）と入口`atk-mode`への作業の名指しを、どちらも作業の修飾名へそろえる。
        args.skill = skill_modes.qualified(args.skill)
        skill, arguments = skill_modes.resolve_work(args.skill, args.args)
        if skill != args.skill:
            args.skill, args.args = skill, arguments or None
    if identifier := getattr(args, "resume", None):
        try:
            record = _run_state.load(identifier)
            # 保存記録は書き換えず、読んだ作業名を指定名と同じ規則でそろえ、以降の比較と起動単位はこの値で行う。
            saved_skill = skill_modes.qualified(record.input.skill)
            if any(getattr(args, name, None) is not None for name in ("target_repo", "work_dir", "timeout")) or (
                explicit_arguments and (args.args or None) != (record.input.arguments or None)
            ):
                raise ValueError("--resumeでは元の対象・cwd・時間予算・スキル引数を変更できない")
            if (
                args.skill is not None
                and args.skill != saved_skill
                and (explicit_arguments or not _is_entry_of(args.skill, saved_skill))
            ):
                raise ValueError("--resumeのスキルが元の実行と一致しない")
        except (OSError, ValueError) as error:
            _outcome.report_failure(
                str(error), next_action="ログの実行識別子と保存した入力を確認し、--resume <RUN_ID>で再実行する"
            )
            return 2
        if record.status == "completed" and record.input.batch_manifest is None:
            _outcome.report_success(f"既に完了した実行: {record.input.run_id}（ログ: {record.log_path}）")
            return 0
        args.skill, args.args = saved_skill, record.input.arguments
        args.target_repo = record.input.target_repo or str(record.work_dir)
        args.work_dir = str(record.work_dir) if record.input.target_repo is not None else None
        args.timeout, args.run_id = record.remaining, record.input.run_id
        args.batch_manifest, args.resume_record = record.input.batch_manifest, record
        if args.batch_manifest is not None or skill_modes.invocation_scope(args.skill, args.args) == session_review.META_SCOPE:
            from agent_toolkit._atk import run_skill_batch  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

            return run_skill_batch.dispatch(args, _dispatch_once)
        return _dispatch_once(args)
    if args.skill is None:
        _outcome.report_failure("スキル名が必要", next_action="スキル名または--resume <RUN_ID>を指定する")
        return 2
    if getattr(args, "timeout", None) is None:
        args.timeout = DEFAULT_TIMEOUT_SECONDS
    if skill_modes.invocation_scope(args.skill, args.args) in {session_review.BATCH_SCOPE, session_review.META_SCOPE}:
        from agent_toolkit._atk import run_skill_batch  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        return run_skill_batch.dispatch(args, _dispatch_once)
    return _dispatch_once(args)


def _is_entry_of(requested: str, saved: str) -> bool:
    return requested.removeprefix("agent-toolkit:") == skill_modes.ENTRY_SKILL and skill_modes.entry_work(saved) is not None


def _dispatch_once(args: argparse.Namespace) -> int:
    record = getattr(args, "resume_record", None)
    log_dir = _state_paths.state_dir() / _LOG_DIRNAME
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = (
        record.log_path if record is not None else _new_log_path(log_dir, skill_modes.invocation_scope(args.skill, args.args))
    )
    args.log_path = log_path
    with log_path.open("a" if record is not None else "w", encoding="utf-8") as log:
        if manifest_path := getattr(args, "batch_manifest", None):
            manifest = session_review.read(pathlib.Path(manifest_path))
            manifest["log_path"] = str(log_path)
            session_review.write(pathlib.Path(manifest_path), manifest)
        progress = Progress(log, sys.stderr)
        progress.message(f"ログ: {log_path.resolve()}")
        try:
            return _dispatch_with_log(args, record, log, log_path, progress)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            _log_line(log, f"終了状態: 起動または実行に失敗した: {error}")
            if isinstance(error, ActionableError):
                _log_line(log, f"次の操作: {error.next_action}")
            raise


def _log_preflight_failure(args: argparse.Namespace, log: typing.TextIO, stage: str, detail: str, *, next_action: str) -> int:
    message = redact_secrets(detail)
    args.batch_failure = f"run-skill-preflight-{stage}: {message}"
    _log_line(log, f"終了状態: {args.batch_failure}")
    _log_line(log, f"次の操作: {next_action}")
    _outcome.report_failure(message, next_action=next_action)
    return 2


def _dispatch_with_log(
    args: argparse.Namespace,
    record: _run_state.RunRecord | None,
    log: typing.TextIO,
    log_path: pathlib.Path,
    progress: Progress,
) -> int:
    if args.timeout <= 0 and record is None:
        return _log_preflight_failure(
            args,
            log,
            "timeout",
            f"--timeoutは正の秒数で指定する（指定値: {args.timeout}）",
            next_action=f"--timeoutを省略して{DEFAULT_TIMEOUT_SECONDS}秒とするか、正の秒数を指定する",
        )
    try:
        candidates: list[tuple[str, str, str]] = (
            [(record.engine, record.model, record.effort)]
            if record is not None
            else _orchestrator.resolve_specs(rerun_action="`atk run-skill`を再実行する", raise_error=True)
        )
    except ActionableError as error:
        return _log_preflight_failure(args, log, "model-config", str(error), next_action=error.next_action)
    work_dir = getattr(args, "work_dir", None)
    if record is not None:
        repo_root = record.work_dir
        if not repo_root.is_dir():
            return _log_preflight_failure(
                args,
                log,
                "resume-cwd",
                f"再開するcwdが存在しない: {repo_root}",
                next_action="元の作業ツリーを復元して同じ実行を再開する",
            )
    elif work_dir is not None:
        repo_root = pathlib.Path(work_dir).expanduser()
        if not repo_root.is_absolute() or not repo_root.is_dir() or not args.target_repo:
            return _log_preflight_failure(
                args,
                log,
                "work-dir",
                "--work-dirは既存の絶対ディレクトリを--target-repoと併せて指定する",
                next_action="報告先repoと実行cwdを確認して再実行する",
            )
        repo_root = repo_root.resolve()
        args.target_repo = resolve_repo_id_or_raise(args.target_repo)
    else:
        repo_root = _resolve_repo_root(resolve_local_worktree(args.target_repo))
    _write_header(log, repo_root=repo_root, args=args)
    try:
        _remove_expired_logs(_state_paths.state_dir() / _LOG_DIRNAME, now=time.time())
    except (OSError, ValueError) as error:
        return _log_preflight_failure(
            args, log, "log-sweep", str(error), next_action="run-skill/runsの実行記録とログの保存先を確認して再実行する"
        )
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
            next_action = "進行中の実行の終了を確認してから、`atk run-skill`を元の引数で再実行する"
            _log_line(log, f"次の操作: {next_action}")
            _outcome.report_failure(
                f"同じ対象リポジトリと同じスキルの実行が進行中のため起動しなかった（ログ: {log_path}）",
                next_action=next_action,
            )
            return CONCURRENT_EXIT_CODE
        with _console_title.console_title(_TITLE):
            return _run_locked(args, repo_root=repo_root, candidates=candidates, log=log, log_path=log_path, progress=progress)


def _run_locked(
    args: argparse.Namespace,
    *,
    repo_root: pathlib.Path,
    candidates: list[tuple[str, str, str]],
    log: typing.TextIO,
    log_path: pathlib.Path,
    progress: Progress,
) -> int:
    return run_locked(
        args,
        repo_root=repo_root,
        candidates=candidates,
        log=log,
        log_path=log_path,
        progress=progress,
        wait_session=_wait_session,
        clock=time,
    )


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
    scope: str,
    executor: _registry.ProcessIdentity | None,
    allow_connection_switch: bool = False,
) -> int:
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
                    if isinstance(limit, _claude_usage_limit.UsageLimitState) and limit.status == "rejected":
                        wait_or_switch = allow_connection_switch or usage_limit_policy.should_wait(
                            orchestrator, limit.limit_type
                        )
                        residual = _stop_executor(process, executor)
                        if residual:
                            raise ActionableError(
                                f"接続切替前に旧CLIの終了を確認できない: {residual}",
                                next_action="記録したPID世代の旧CLIの終了を確認して同じ実行を再開する",
                            ) from None
                        returncode = process.returncode or 1
                        if not wait_or_switch:
                            output.paused_for_usage_limit = limit
                        break
        except subprocess.TimeoutExpired:
            residual = _stop_executor(process, executor)
            _drain_output(readers, log, lock)
            with lock:
                _log_line(log, f"終了状態: 時間上限（{timeout:g}秒）を超えたため終了させた")
                if residual:
                    _log_line(log, f"終了させられなかったプロセス: {residual}")
            if residual:
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
            if output.paused_for_usage_limit is not None:
                return USAGE_LIMIT_PAUSE_EXIT_CODE
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
                saved = _wi_add.has_synced_run_report(_private_notes.default_private_notes(), run_id=run_id, scope=scope)
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
                    return
                diagnostic = label == "stderr" or diagnostic_process.is_error_event(line)
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
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_skill = re.sub(r"[^A-Za-z0-9._-]+", "_", skill)
    return log_dir / f"{stamp}-{safe_skill}-{uuid.uuid4().hex[:8]}.log"


def _remove_expired_logs(log_dir: pathlib.Path, *, now: float) -> None:
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
    _log_line(log, f"開始時刻: {datetime.datetime.now().astimezone().isoformat(timespec='seconds')}")
    _log_line(log, f"対象リポジトリ: {repo_root}")
    _log_line(log, f"スキル: {args.skill}")
    _log_line(log, f"スキルへ渡す引数: {args.args if args.args is not None else 'なし'}")
    _log_line(log, f"時間上限: {args.timeout}秒")
