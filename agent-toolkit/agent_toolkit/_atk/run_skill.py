"""`atk run-skill`: 定期実行から任意のスキルを自律モードの最上位セッションとして1回実行する。

cron、systemd timer、Windowsのタスクスケジューラーなど端末を持たない起動元が1行で呼ぶコマンドとする。
`claude`コマンドを直接書く場合に必要になる非対話起動、モデル選択、利用上限の待機、多重起動の防止、時間上限、
ログ保存、自律モードの印付けおよび終了コードを本コマンドが吸収する。

モデル候補の解決、可用性判定と利用上限の待機は`atk wi process-loop`と同じ`orchestrator`の関数を呼ぶ。
子セッションには`atk wi process-loop`用の環境印を渡さない。その印は対話型のCLI本体を停止させる終了工程を
Stopの条件にするhookを有効にし、自ら最終応答で終わる非対話セッションを時間上限まで終われなくするためである。
報告用UWIはセッション内の`agent-toolkit:completion-report`が作成し、本コマンドは後処理で報告を作成しない。
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
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

# ヘルプや他の処理で使わない依存は、使用時まで読み込まない。
lazy import filelock

from agent_toolkit._atk import orchestrator as _orchestrator
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.run_skill_progress import Progress
from agent_toolkit._atk.wi import add as _wi_add
from agent_toolkit._atk.wi.repo import resolve_local_worktree
from agent_toolkit._common import antigravity_cli as _antigravity
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._common import claude_usage_limit as _claude_usage_limit
from agent_toolkit._common import codex_usage_limit as _codex_usage_limit
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import private_notes as _private_notes
from agent_toolkit._common import process_tree as _process_tree
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._git import command as _git_command

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

子セッションが起動した子孫が出力のパイプを保持したまま残ると、出力の終端が届かず読み取りが終わらない。
待ちに上限を置き、`--timeout`の外で待ち続けないようにする。終了した子セッションの出力はパイプへ書き込み済みであり、
上限はそれを読み終える余裕として置く。
"""

_TITLE = "atk run-skill"
_USAGE_LIMIT_RESUME_PROMPT = _automated_prompt.wrap(
    "利用上限で中断したため解除まで待ちました。最初の未完了工程から作業を続けてください。"
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
    parser.add_argument(
        "--timeout",
        metavar="SECONDS",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            f"セッションの起動からの時間上限の秒数。省略時は{DEFAULT_TIMEOUT_SECONDS}秒。"
            "可用性判定と利用上限の解除待ちの時間は含めない。"
        ),
    )
    parser.add_argument("--args", metavar="TEXT", default=None, help="スキルへ渡す任意の文字列。")
    parser.add_argument(
        "skill",
        metavar="SKILL",
        help="実行するスキル名（`agent-toolkit:process-wi`のような修飾名、またはプロジェクトのスキル名）。",
    )


def dispatch(args: argparse.Namespace) -> int:
    """スキルを1回実行し、終了コードを返す。"""
    if args.timeout <= 0:
        _outcome.report_failure(
            f"--timeoutは正の秒数で指定する（指定値: {args.timeout}）",
            next_action=f"--timeoutを省略して{DEFAULT_TIMEOUT_SECONDS}秒とするか、正の秒数を指定する",
        )
        return 2
    candidates = _orchestrator.resolve_specs(rerun_action="`atk run-skill`を再実行する")
    repo_root = _resolve_repo_root(resolve_local_worktree(args.target_repo))
    log_dir = _state_paths.state_dir() / _LOG_DIRNAME
    log_dir.mkdir(parents=True, exist_ok=True)
    _remove_expired_logs(log_dir, now=time.time())
    log_path = _new_log_path(log_dir, args.skill)
    with log_path.open("w", encoding="utf-8") as log:
        progress = Progress(log, sys.stderr)
        progress.message(f"ログ: {log_path.resolve()}")
        _write_header(log, repo_root=repo_root, args=args)
        lock = filelock.FileLock(str(_lock_path(repo_root, args.skill)))
        with contextlib.ExitStack() as resources:
            try:
                resources.enter_context(lock.acquire(timeout=0))
            except filelock.Timeout:
                _log_line(log, "終了状態: 同じ対象リポジトリと同じスキルの実行が進行中のため起動しなかった")
                _outcome.report_failure(
                    f"同じ対象リポジトリと同じスキルの実行が進行中のため起動しなかった（ログ: {log_path}）",
                    next_action="進行中の実行の終了を待つ。次回の定期実行で改めて起動されるため、操作は不要",
                )
                return CONCURRENT_EXIT_CODE
            with _console_title.console_title(_TITLE):
                return _run_locked(
                    args, repo_root=repo_root, candidates=candidates, log=log, log_path=log_path, progress=progress
                )


def build_goal(skill: str, skill_args: str | None) -> str:
    """子セッションへ渡す目的文の本体を返す。"""
    goal = (
        f"スキル`{skill}`を完遂し、`agent-toolkit:completion-report`の報告用UWIを保存してください。"
        "作業の前に`agent-toolkit:user-confirmation-and-report`の`references/answer-transitions.md`"
        "「`atk run-skill`の過去の実行のUWI」を適用してください。"
    )
    if skill_args is not None:
        goal += f"\nスキルへ渡す引数: {skill_args}"
    return goal


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
    env = _orchestrator.child_env(
        drop=(
            _orchestrator.RESTART_SPEC_ENV,
            _orchestrator.PROCESS_LOOP_SESSION_ENV,
            _orchestrator.PROCESS_LOOP_SESSION_ID_ENV,
        )
    )
    run_id = uuid.uuid4().hex
    env["AGENT_TOOLKIT_RUN_SKILL_ID"] = run_id
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
    )
    try:
        orchestrator, model, effort = _orchestrator.select_available(candidates, env, repo_root, context)
    except _orchestrator.NoAvailableCandidateError as error:
        _log_line(log, f"終了状態: 全てのモデル候補の可用性判定に失敗した（{error.orchestrator}: {error.detail}）")
        _outcome.report_failure(
            f"全てのモデル候補の可用性判定に失敗したため起動しなかった（ログ: {log_path}）",
            next_action=_PATH_ACTION,
        )
        return error.returncode or 1
    candidate = f"{orchestrator}:{model}/{effort}"
    _log_line(log, f"採用した候補: {candidate}")
    goal = _automated_prompt.wrap(
        build_goal(args.skill, args.args), source=_automated_prompt.SOURCE_RUN_SKILL, kind=_automated_prompt.KIND_GOAL
    )
    session_id: str | None = None
    if orchestrator == "claude":
        session_id = str(uuid.uuid4())
        progress.message(f"Claudeのセッション識別子: {session_id}")
        argv = [
            "claude",
            "-p",
            "--session-id",
            session_id,
            "--permission-mode",
            "auto",
            "--permission-prompts",
            "none",
            "--model",
            model,
            "--effort",
            effort,
            f"/goal {goal}",
        ]
        argv[-1:-1] = ["--output-format", "stream-json", "--verbose"]
    elif orchestrator == "agy":
        argv = _antigravity.build_command(goal, model, effort, None, timeout=args.timeout)
    else:
        # Codexの非対話起動は`/goal`を目標として扱わず通常の入力として残すため、目的文だけを渡す。
        argv = ["codex", "exec", "--model", model, "-c", f"model_reasoning_effort={effort}", goal]
        argv.insert(-1, "--json")
    log.flush()
    codex_home = _host_homes.codex_home(env)
    remaining = float(args.timeout)
    while True:
        output = _SessionOutput()
        started_at = time.monotonic()
        with contextlib.ExitStack() as resources:
            try:
                process = resources.enter_context(
                    _process_tree.process_scope(
                        argv,
                        cwd=repo_root,
                        env=_orchestrator.session_env(env, orchestrator),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        close_streams=False,
                    )
                )
            except OSError as error:
                _log_line(log, f"終了状態: {orchestrator}を起動できなかった（{error}）")
                _outcome.report_failure(
                    f"{orchestrator}を起動できなかった: {error}（ログ: {log_path}）", next_action=_PATH_ACTION
                )
                return 1
            code = _wait_session(
                process,
                orchestrator=orchestrator,
                timeout=remaining,
                log=log,
                log_path=log_path,
                progress=progress,
                output=output,
                codex_home=codex_home,
                run_id=run_id,
                skill=args.skill,
            )
        remaining = max(0.0, remaining - (time.monotonic() - started_at))
        if code == TIMEOUT_EXIT_CODE or output.usage_limit is None:
            return code
        usage_limit = output.usage_limit
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

    def receive(self, line: str) -> None:
        """stdoutのイベントを取り込む。"""
        thread_id = _codex_usage_limit.thread_id_from_stream_lines((line,))
        if thread_id is not None:
            self.thread_id = thread_id
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        conversation = _antigravity.event_session_id(event)
        if conversation is not None:
            self.thread_id = conversation
        state = _claude_usage_limit.from_event(event)
        if state is not None:
            self.usage_limit = state if state.is_wait_target else None


@contextlib.contextmanager
def _session_readers(
    process: subprocess.Popen[bytes],
    *,
    orchestrator: str,
    log: typing.TextIO,
    progress: Progress,
    output: _SessionOutput,
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
                _process_tree.log_residual(_process_tree.stop_process_tree(process), context="run-skill")
        finally:
            _drain_output([reader for reader in readers if reader.ident is not None], log, lock)
        raise
    finally:
        # 起動した読取スレッドがパイプを閉じる。未起動の側だけを主スレッドが閉じる。
        with contextlib.ExitStack() as cleanup:
            for index, stream in enumerate(streams):
                if stream is not None and (index >= len(readers) or readers[index].ident is None):
                    cleanup.callback(stream.close)


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
) -> int:
    """子セッションの終了か時間上限を待ち、結果行を出力して終了コードを返す。"""
    with _session_readers(process, orchestrator=orchestrator, log=log, progress=progress, output=output) as (lock, readers):
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            residual = _process_tree.stop_process_tree(process)
            _drain_output(readers, log, lock)
            with lock:
                _log_line(log, f"終了状態: 時間上限（{timeout:g}秒）を超えたため終了させた")
                if residual:
                    _log_line(log, f"終了させられなかったプロセス: {residual}")
            _outcome.report_failure(
                f"セッションが時間上限（{timeout:g}秒）を超えたため終了させた（ログ: {log_path}）",
                next_action="ログで進行状況を確かめ、必要なら`--timeout`を延ばして次回の定期実行を待つ",
            )
            return TIMEOUT_EXIT_CODE
        _drain_output(readers, log, lock)
        with lock:
            _log_line(log, f"終了状態: exit code {returncode}")
            if orchestrator == "codex" and returncode != 0:
                output.usage_limit = _codex_usage_limit.from_thread(output.thread_id, codex_home=codex_home)
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
                        "ログの実行識別子に対応する報告用UWIを確認し、同じ会話を再開してcompletion-reportで保存する。"
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
                "解消してから次回の定期実行を待つ"
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
                f"出力の読み取りを打ち切った: 子セッションの終了から{OUTPUT_DRAIN_SECONDS:g}秒以内に出力の終端が届かなかった"
                "（出力のパイプを保持したまま残る子孫プロセスがある）",
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
    with stream:
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            with lock:
                if log.closed:
                    # 読み取りを打ち切った後に届いた出力は、閉じたログへ書けないため捨てる。
                    return
                log.write(f"[{label}] {line}\n")
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


def _lock_path(repo_root: pathlib.Path, skill: str) -> pathlib.Path:
    """対象リポジトリとスキルの組ごとのロックファイルのパスを、ロックファイルのディレクトリ配下に返す。"""
    digest = hashlib.sha256(f"{repo_root}\0{skill}".encode()).hexdigest()[:16]
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
    for path in log_dir.glob("*.log"):
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
    log.write(f"{text}\n")
    log.flush()
