"""有限終了する外部コマンドの両ストリームと終了状態を保持する。"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
from typing import Any

from agent_toolkit._atk import managed_temp, outcome
from agent_toolkit._git import command as _git_command

_EXIT_TIMEOUT = 124
_EXIT_WRAPPER_FAILURE = 125
# 子プロセスの起動前に作業ツリーの状態を取る`git`の時間上限。観測の前処理で実行を止めないよう短く保つ。
_GIT_STATE_TIMEOUT_SECONDS = 30.0


def _positive_seconds(value: str) -> float:
    """正の秒数を返す。"""
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("正の秒数を指定してください") from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("正の秒数を指定してください")
    return seconds


def _absolute_directory(value: str) -> pathlib.Path:
    """実在する絶対ディレクトリを返す。"""
    path = pathlib.Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("--cwdは絶対パスで指定してください")
    resolved = path.resolve()
    if not resolved.is_dir():
        raise argparse.ArgumentTypeError(f"--cwdが実在するディレクトリではありません: {path}")
    return resolved


def build_parser(parser: argparse.ArgumentParser) -> None:
    """`atk run-command`の引数を登録する。"""
    parser.add_argument("--cwd", type=_absolute_directory, metavar="DIR", help="子プロセスの絶対作業ディレクトリ。")
    parser.add_argument("--timeout", type=_positive_seconds, metavar="SECONDS", help="正の実行上限秒数。")
    parser.add_argument("command_argv", nargs=argparse.REMAINDER, metavar="COMMAND", help="`--`以後の実行argv。")


def _file_metrics(path: pathlib.Path) -> tuple[int, int]:
    """ファイルの行数とバイト数を返す。"""
    with path.open("rb") as stream:
        lines = sum(1 for _line in stream)
    return lines, path.stat().st_size


def _git_state(cwd: pathlib.Path) -> tuple[str | None, list[str] | None]:
    """起動直前の作業ツリーのHEADと、未commit・未追跡の状態を返す。

    実行結果を後から別の版へ適用できるかを判定するには、実行した対象の版が要る。
    HEADだけでは未commitの差分や未追跡の入力を実行した結果を区別できないため、`git status`の行も残す。
    Git作業ツリーの外では両方を`None`とする。取得に失敗した場合も子プロセスの実行は続け、警告を標準エラーへ書く。
    """
    try:
        inside = _git_command.run(
            ["rev-parse", "--is-inside-work-tree"],
            cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_STATE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        outcome.report_warning(
            f"作業ツリーの状態を取得できない: {error}",
            next_action="対応不要（コマンドは実行し、`git_head`と`git_status`を`null`で保存した）",
        )
        return None, None
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None, None
    head = _git_command.optional_output(["rev-parse", "--verify", "HEAD^{commit}"], cwd, timeout=_GIT_STATE_TIMEOUT_SECONDS)
    try:
        status = _git_command.run(
            ["status", "--porcelain=v1", "--untracked-files=all"],
            cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_STATE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        status = None
    status_lines = status.stdout.splitlines() if status is not None and status.returncode == 0 else None
    if head is None or status_lines is None:
        outcome.report_warning(
            "作業ツリーのHEADまたは`git status`を取得できない",
            next_action="対応不要（コマンドは実行し、取得できなかった項目を`null`で保存した）。"
            "観測の版を根拠に使う場合は、同じcwdで`git rev-parse HEAD`と`git status`を確かめて記録する",
        )
    return head, status_lines


def _metadata(
    *,
    argv: list[str],
    cwd: pathlib.Path,
    git_head: str | None,
    git_status: list[str] | None,
    child_exit_code: int | None,
    timed_out: bool,
    signal_number: int | None,
    stdout_path: pathlib.Path | None,
    stderr_path: pathlib.Path | None,
) -> dict[str, Any]:
    """公開JSONを構築する。"""
    stdout_lines, stdout_bytes = _file_metrics(stdout_path) if stdout_path is not None else (0, 0)
    stderr_lines, stderr_bytes = _file_metrics(stderr_path) if stderr_path is not None else (0, 0)
    return {
        "argv": argv,
        "cwd": str(cwd),
        "git_head": git_head,
        "git_status": git_status,
        "child_exit_code": child_exit_code,
        "timed_out": timed_out,
        "signal": signal_number,
        "stdout_path": str(stdout_path) if stdout_path is not None else None,
        "stderr_path": str(stderr_path) if stderr_path is not None else None,
        "stdout_lines": stdout_lines,
        "stderr_lines": stderr_lines,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
    }


def dispatch(args: argparse.Namespace) -> int:
    """外部コマンドを実行し、保存結果のJSONと実際の終了状態を返す。"""
    argv = list(args.command_argv)
    if not argv or argv[0] != "--" or len(argv) == 1:
        outcome.report_failure(
            "`--`以後に実行するCOMMANDがありません", next_action="`atk run-command -- COMMAND [ARG...]`で再実行する"
        )
        return _EXIT_WRAPPER_FAILURE
    argv.pop(0)

    cwd = args.cwd if args.cwd is not None else pathlib.Path.cwd().resolve()
    git_head, git_status = _git_state(cwd)
    stdout_path: pathlib.Path | None = None
    stderr_path: pathlib.Path | None = None
    child_exit_code: int | None = None
    timed_out = False
    signal_number: int | None = None
    wrapper_exit_code = _EXIT_WRAPPER_FAILURE
    failure: str | None = None
    directory: pathlib.Path | None = None
    try:
        directory = managed_temp.create_managed_temp("atk-command")
        stdout_path = (directory / "stdout.bin").resolve()
        stderr_path = (directory / "stderr.bin").resolve()
        with stdout_path.open("xb") as stdout_stream, stderr_path.open("xb") as stderr_stream:
            try:
                with subprocess.Popen(  # noqa: S603
                    argv, cwd=cwd, stdout=stdout_stream, stderr=stderr_stream
                ) as process:
                    try:
                        process.wait(timeout=args.timeout)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        process.kill()
                        process.wait()
                    child_exit_code = process.returncode
            except (OSError, ValueError) as error:
                failure = f"子プロセスを開始できない: {error}"
        if timed_out:
            wrapper_exit_code = _EXIT_TIMEOUT
        elif child_exit_code is not None:
            if child_exit_code < 0:
                signal_number = -child_exit_code
                wrapper_exit_code = 128 + signal_number
            else:
                wrapper_exit_code = child_exit_code
    except (OSError, managed_temp.ManagedTempError) as error:
        failure = f"保存の準備または完了に失敗した: {error}"

    metadata = _metadata(
        argv=argv,
        cwd=cwd,
        git_head=git_head,
        git_status=git_status,
        child_exit_code=child_exit_code,
        timed_out=timed_out,
        signal_number=signal_number,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    metadata["record_path"] = None
    if directory is not None:
        record_path = (directory / "record.json").resolve()
        metadata["record_path"] = str(record_path)
        try:
            with record_path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as error:
            metadata["record_path"] = None
            wrapper_exit_code = _EXIT_WRAPPER_FAILURE
            failure = f"実行結果JSONを保存できない: {error}"
    print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    if wrapper_exit_code == 0:
        outcome.report_success("外部コマンドが終了した", outcome.ResultKind.VALUE_OUTPUT)
    else:
        detail = failure or f"外部コマンドが終了コード{wrapper_exit_code}で終了した"
        paths = f"stdout={stdout_path}, stderr={stderr_path}"
        outcome.report_failure(detail, next_action=f"保存先を診断する: {paths}")
    return wrapper_exit_code
