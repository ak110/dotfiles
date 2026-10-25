"""laneの統合各段が記録と再開に使う、有限終了する外部コマンドの両ストリームと終了状態の保持。

公開コマンドにはしない。一般の検証はハーネスの標準実行で行う。
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import shlex
import subprocess
import sys
from typing import Any

from pyfltr.command import execution_slot

from agent_toolkit._atk import managed_temp, outcome
from agent_toolkit._atk.environment import is_human_terminal
from agent_toolkit._common import process_tree
from agent_toolkit._common.inherited_venv import strip_inherited_venv
from agent_toolkit._git import command as _git_command

_EXIT_TIMEOUT = 124
_EXIT_WRAPPER_FAILURE = 125
# 子プロセスの起動前に作業ツリーの状態を取る`git`の時間上限。観測の前処理で実行を止めないよう短く保つ。
_GIT_STATE_TIMEOUT_SECONDS = 30.0


def _file_metrics(path: pathlib.Path) -> tuple[int, int]:
    """ファイルの行数とバイト数を返す。"""
    with path.open("rb") as stream:
        lines = sum(1 for _line in stream)
    return lines, path.stat().st_size


def _git_state(cwd: pathlib.Path) -> tuple[str | None, list[str] | None]:
    """起動直前の作業ツリーのHEADと、未commit・未追跡の状態を返す。

    HEADはcommit、`git status`は変更ファイルと変更状態を示す。
    未commit・未追跡の本文は保持しないため、その内容の同定には取得時の本文など別の根拠が要る。
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
            next_action="観測の版を根拠に使う場合は、同じcwdで`git rev-parse HEAD`と`git status`を確かめて記録する",
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
    except OSError, subprocess.SubprocessError:
        status = None
    status_lines = status.stdout.splitlines() if status is not None and status.returncode == 0 else None
    if head is None or status_lines is None:
        outcome.report_warning(
            "作業ツリーのHEADまたは`git status`を取得できない",
            next_action="観測の版を根拠に使う場合は、同じcwdで`git rev-parse HEAD`と`git status`を確かめて記録する",
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
    """保存する完全な実行記録を構築する。"""
    result: dict[str, Any] = {
        "argv": argv,
        "cwd": str(cwd),
        "git_head": git_head,
        "git_status": git_status,
        "child_exit_code": child_exit_code,
        "timed_out": timed_out,
        "signal": signal_number,
        "stdout_path": str(stdout_path) if stdout_path is not None else None,
        "stderr_path": str(stderr_path) if stderr_path is not None else None,
    }
    metric_errors = []
    for name, path in (("stdout", stdout_path), ("stderr", stderr_path)):
        lines: int | None
        size: int | None
        try:
            lines, size = _file_metrics(path) if path is not None else (0, 0)
        except OSError as error:
            lines, size = None, None
            metric_errors.append(f"{name}の属性を取得できない: {error}")
        result[name + "_lines"], result[name + "_bytes"] = lines, size
    result["_metric_errors"] = metric_errors
    return result


def execute(
    argv: list[str],
    cwd: pathlib.Path,
    timeout: float | None,
    *,
    verification: bool = False,
) -> tuple[dict[str, Any], int, str | None]:
    """検証用途では共有実行枠を取得し、子の終了と記録保存まで所有する。"""
    ownership = execution_slot.hold(label=shlex.join(argv)) if verification else contextlib.nullcontext()
    with ownership:
        return _execute(argv, cwd, timeout)


def _execute(
    argv: list[str],
    cwd: pathlib.Path,
    timeout: float | None,
) -> tuple[dict[str, Any], int, str | None]:
    """終了する子プロセスを実行して記録を保存し、表示とは別に結果・終了コード・保存の失敗を返す。"""
    child_env = dict(os.environ)
    strip_inherited_venv(child_env)
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
        directory = managed_temp.create_output_temp("atk-command")
        stdout_path = (directory / "stdout.bin").resolve()
        stderr_path = (directory / "stderr.bin").resolve()
        with stdout_path.open("xb") as stdout_stream, stderr_path.open("xb") as stderr_stream:
            try:
                # 回収不能でも記録を確定するため、context managerの無期限waitを重ねない。
                process = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
                    argv, cwd=cwd, env=child_env, stdout=stdout_stream, stderr=stderr_stream
                )
                try:
                    if is_human_terminal():
                        print(f"実行中（両出力を保存中）: {shlex.join(argv)}", file=sys.stderr, flush=True)
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    if is_human_terminal():
                        print(f"時間上限に到達し、子と子孫を停止中: {shlex.join(argv)}", file=sys.stderr, flush=True)
                    residual = process_tree.stop_process_tree(process)
                    if residual:
                        failure = "子孫プロセスを回収できない: " + "; ".join(residual)
                except BaseException:
                    process_tree.log_residual(process_tree.stop_process_tree(process), context="lane command exception")
                    raise
                child_exit_code = process.returncode
            except (OSError, ValueError) as error:
                failure = f"子の実行に失敗した: {error}"
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
    metric_errors = metadata.pop("_metric_errors")
    if metric_errors:
        wrapper_exit_code = _EXIT_WRAPPER_FAILURE
        failure = "\n".join([*([failure] if failure else []), *metric_errors])
    metadata["record_path"] = None
    metadata["wrapper_exit_code"] = wrapper_exit_code
    metadata["failure"] = failure
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
            metadata["wrapper_exit_code"] = wrapper_exit_code
            metadata["failure"] = failure
    return metadata, wrapper_exit_code, failure
