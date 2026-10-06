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
import datetime
import hashlib
import pathlib
import re
import subprocess
import sys
import threading
import time
import typing
import uuid

import filelock
import psutil

from agent_toolkit._atk import config as _config
from agent_toolkit._atk import orchestrator as _orchestrator
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi.repo import _resolve_local_worktree
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._common import claude_usage_limit as _claude_usage_limit
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import process_tree as _process_tree

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
_LOG_DIRNAME = "run-skill"
_LOCK_DIRNAME = "locks"
_PATH_ACTION = (
    "定期実行の環境変数`PATH`へ`atk`・`claude`・`codex`の実行ファイルの場所を加える（crontabでは`PATH=...`の行を置く）。"
    "PATHに問題が無い場合は`atk config set orchestrate_model <候補列>`で候補を変える"
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


def run(args: argparse.Namespace) -> int:
    """スキルを1回実行し、終了コードを返す。"""
    if args.timeout <= 0:
        _outcome.report_failure(
            f"--timeoutは正の秒数で指定する（指定値: {args.timeout}）",
            next_action=f"--timeoutを省略して{DEFAULT_TIMEOUT_SECONDS}秒とするか、正の秒数を指定する",
        )
        return 2
    candidates = _orchestrator.resolve_specs(rerun_action="`atk run-skill`を再実行する")
    repo_root = _resolve_repo_root(_resolve_local_worktree(args.target_repo))
    log_dir = _config.state_dir() / _LOG_DIRNAME
    log_dir.mkdir(parents=True, exist_ok=True)
    _remove_expired_logs(log_dir, now=time.time())
    log_path = _new_log_path(log_dir, args.skill)
    with log_path.open("w", encoding="utf-8") as log:
        _write_header(log, repo_root=repo_root, args=args)
        lock = filelock.FileLock(str(_lock_path(log_dir, repo_root, args.skill)))
        try:
            lock.acquire(timeout=0)
        except filelock.Timeout:
            _log_line(log, "終了状態: 同じ対象リポジトリと同じスキルの実行が進行中のため起動しなかった")
            _outcome.report_failure(
                f"同じ対象リポジトリと同じスキルの実行が進行中のため起動しなかった（ログ: {log_path}）",
                next_action="進行中の実行の終了を待つ。次回の定期実行で改めて起動されるため、操作は不要",
            )
            return CONCURRENT_EXIT_CODE
        try:
            with _console_title.console_title(_TITLE):
                return _run_locked(args, repo_root=repo_root, candidates=candidates, log=log, log_path=log_path)
        finally:
            lock.release()


def build_goal(skill: str, skill_args: str | None) -> str:
    """子セッションへ渡す目的文の本体を返す。"""
    goal = (
        f"スキル`{skill}`を完遂し、`agent-toolkit:completion-report`の報告用UWIを保存してください。"
        "作業の前に`agent-toolkit:user-confirmation-and-report`「`atk run-skill`の過去の実行のUWI」を適用してください。"
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
) -> int:
    """ロックを取得した状態で候補を選び、子セッションを実行する。"""
    env = _orchestrator.child_env(
        drop=(
            _orchestrator.RESTART_SPEC_ENV,
            _orchestrator.PROCESS_LOOP_SESSION_ENV,
            _orchestrator.PROCESS_LOOP_SESSION_ID_ENV,
        )
    )

    def record_wait(candidate: str, usage_limit: _claude_usage_limit.UsageLimitState, delay: float) -> None:
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
    if orchestrator == "claude":
        session_id = str(uuid.uuid4())
        _log_line(log, f"Claudeのセッション識別子: {session_id}")
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
    else:
        # Codexの非対話起動は`/goal`を目標として扱わず通常の入力として残すため、目的文だけを渡す。
        argv = ["codex", "exec", "--model", model, "-c", f"model_reasoning_effort={effort}", goal]
    log.flush()
    try:
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            argv,
            cwd=repo_root,
            env=_orchestrator.session_env(env, orchestrator),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        _log_line(log, f"終了状態: {orchestrator}を起動できなかった（{error}）")
        _outcome.report_failure(f"{orchestrator}を起動できなかった: {error}（ログ: {log_path}）", next_action=_PATH_ACTION)
        return 1
    return _wait_session(process, orchestrator=orchestrator, timeout=args.timeout, log=log, log_path=log_path)


def _wait_session(
    process: subprocess.Popen[bytes],
    *,
    orchestrator: str,
    timeout: int,
    log: typing.TextIO,
    log_path: pathlib.Path,
) -> int:
    """子セッションの終了か時間上限を待ち、結果行を出力して終了コードを返す。"""
    lock = threading.Lock()
    assert process.stdout is not None and process.stderr is not None
    readers = [
        threading.Thread(target=_copy_stream, args=(process.stdout, "stdout", log, lock), daemon=True),
        threading.Thread(target=_copy_stream, args=(process.stderr, "stderr", log, lock), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # 終了を要求する前に子孫を列挙する。子が先に終わると子孫の親が変わり、PIDから辿れなくなる。
        descendants = _process_tree.collect_descendants(process.pid)
        try:
            targets = [psutil.Process(process.pid), *descendants]
        except psutil.NoSuchProcess:
            targets = descendants
        residual = _process_tree.reclaim_descendants(targets)
        process.wait()
        _drain_output(readers, log, lock)
        with lock:
            _log_line(log, f"終了状態: 時間上限（{timeout}秒）を超えたため終了させた")
            if residual:
                _log_line(log, f"終了させられなかったプロセス: {residual}")
        _outcome.report_failure(
            f"セッションが時間上限（{timeout}秒）を超えたため終了させた（ログ: {log_path}）",
            next_action="ログで進行状況を確かめ、必要なら`--timeout`を延ばして次回の定期実行を待つ",
        )
        return TIMEOUT_EXIT_CODE
    _drain_output(readers, log, lock)
    with lock:
        _log_line(log, f"終了状態: exit code {returncode}")
    if _orchestrator.is_normal_session_exit(orchestrator, returncode):
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


def _copy_stream(stream: typing.IO[bytes], label: str, log: typing.TextIO, lock: threading.Lock) -> None:
    """子セッションの出力を行ごとにログへ写す。標準出力と標準エラーを行頭の名前で区別する。"""
    for raw in iter(stream.readline, b""):
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        with lock:
            if log.closed:
                # 読み取りを打ち切った後に届いた出力は、閉じたログへ書けないため捨てる。
                return
            log.write(f"[{label}] {line}\n")
            log.flush()


def _resolve_repo_root(path: pathlib.Path) -> pathlib.Path:
    """指定パスが属する作業ツリーのrootを返す。Git管理外ならパスをそのまま使う。"""
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        _outcome.report_failure(
            f"対象リポジトリの作業ツリーを解決できない: {path}（git: {result.stderr.strip()}）",
            next_action="`--target-repo`へGitの作業ツリーのパスを指定する",
        )
        sys.exit(2)
    return pathlib.Path(result.stdout.strip()).resolve()


def _lock_path(log_dir: pathlib.Path, repo_root: pathlib.Path, skill: str) -> pathlib.Path:
    """対象リポジトリとスキルの組ごとのロックファイルのパスを返す。"""
    digest = hashlib.sha256(f"{repo_root}\0{skill}".encode()).hexdigest()[:16]
    lock_dir = log_dir / _LOCK_DIRNAME
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
