"""laneの統合各段が記録と再開に使う、有限終了する外部コマンドの両ストリームと終了状態の保持。

公開コマンドにはしない。一般の検証はハーネスの標準実行で行う。
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import shlex
import subprocess
import sys
import time
from typing import Any

from pyfltr.command import execution_slot

from agent_toolkit._atk import managed_temp, outcome
from agent_toolkit._atk.environment import is_human_terminal
from agent_toolkit._common import process_tree
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.inherited_venv import strip_inherited_venv
from agent_toolkit._git import command as _git_command

_EXIT_TIMEOUT = 124
_EXIT_WRAPPER_FAILURE = 125
# 子プロセスの起動前に作業ツリーの状態を取る`git`の時間上限。観測の前処理で実行を止めないよう短く保つ。
_GIT_STATE_TIMEOUT_SECONDS = 30.0
# 成功記録の再利用判定で値を比べない環境変数。エージェントの会話・委譲・巡回の識別子と引き継ぎ記録の場所、
# ホストとのメッセージング、端末・SSH・D-Busのセッション、シェルの作業ディレクトリと階層、
# miseのセッション差分とshimの起動経路（プロセス番号を含む）は、
# 実行や再開のたびに値が変わり、同じ版・argv・cwdの検証結果を変えない。値まで比較すると再開ごとに同じ全体検証を
# 繰り返すため、有無だけを比較する。有無は通知先の解決など動作を切り替えうるので比較に残す。
# 委譲の種別や`1`で有効化するフラグ（`AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND`、`AGENT_TOOLKIT_DELEGATED_SESSION`、
# `AGENT_TOOLKIT_PROCESS_LOOP_SESSION`など）は値が動作を変えるため、ここへ入れず値を比較する。
# cwdは記録の`cwd`で別に比較する。名前で扱うのはここに挙げた既知の変数だけとし、未知の変数は値を比較する。
_VOLATILE_ENVIRONMENT_NAMES = frozenset(
    {
        "_",
        "OLDPWD",
        "PWD",
        "SHLVL",
        "AGENT_TOOLKIT_HANDOFF_PATH",
        "AGENT_TOOLKIT_OWNER_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_PID",
        "CODEX_SESSION_ID",
        "CODEX_THREAD_ID",
        "DBUS_SESSION_BUS_ADDRESS",
        "SSH_CLIENT",
        "SSH_CONNECTION",
        "SSH_TTY",
        "TERM_SESSION_ID",
        "TMUX_PANE",
        "WINDOWID",
        "XDG_SESSION_ID",
        "__MISE_DIFF",
        "__MISE_SESSION",
        "__MISE_SHIM_FALLBACK_CHAIN",
    }
)


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
    started = time.monotonic()
    timing: dict[str, Any] = {
        "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "finished_at": None,
        "elapsed": None,
        "command_started_at": None,
        "command_finished_at": None,
        "command_elapsed": None,
        "slot_waits": [] if verification else None,
    }
    try:
        directory = managed_temp.create_output_temp("atk-command")
    except (OSError, managed_temp.ManagedTempError) as error:
        failed = _metadata(
            argv=argv,
            cwd=cwd,
            git_head=None,
            git_status=None,
            child_exit_code=None,
            timed_out=False,
            signal_number=None,
            stdout_path=None,
            stderr_path=None,
        )
        failed.pop("_metric_errors")
        failure = f"保存の準備または完了に失敗した: {error}"
        failed.update(record_path=None, execution_conditions=None, wrapper_exit_code=_EXIT_WRAPPER_FAILURE, failure=failure)
        return failed, _EXIT_WRAPPER_FAILURE, failure
    journal = directory / "timing.json"
    waits: dict[tuple[str, int], dict[str, Any]] = {}

    def record_wait(wait: execution_slot.SlotWait) -> None:
        waits[wait.kind, wait.sequence] = wait.to_dict()
        timing["slot_waits"] = list(waits.values())
        atomic_write(journal, json.dumps(timing, ensure_ascii=False) + "\n")

    ownership = (
        execution_slot.coordinated_hold(
            label=shlex.join(argv), on_wait_interval=record_wait, participant=execution_slot.receive_ticket()
        )
        if verification
        else contextlib.nullcontext(None)
    )
    observed: dict[str, Any] | None = None
    try:
        atomic_write(journal, json.dumps(timing, ensure_ascii=False) + "\n")
        try:
            with ownership as coordinator:
                command_started = time.monotonic()
                timing["command_started_at"] = datetime.datetime.now(datetime.UTC).isoformat()
                participant = coordinator.participant() if coordinator is not None else contextlib.nullcontext(None)
                with participant as ticket, execution_slot.bind_tool_ticket(ticket):
                    observed, code, failure = _execute(argv, cwd, timeout, directory, coordinator=coordinator)
                timing["command_finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
                timing["command_elapsed"] = time.monotonic() - command_started
        finally:
            # 取得前の中断でも、完了した待機区間と未到達のコマンドを区別して保持する。
            timing["finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
            timing["elapsed"] = time.monotonic() - started
            atomic_write(journal, json.dumps(timing, ensure_ascii=False) + "\n")
        observed["timing"] = timing
        observed["timing_path"] = str(journal)
        if observed.get("record_path") is not None:
            atomic_write(pathlib.Path(observed["record_path"]), json.dumps(observed, ensure_ascii=False, sort_keys=True) + "\n")
    except KeyboardInterrupt:
        outcome.report_warning(
            f"コマンドが中断された。待機・到達状態の記録: {journal}",
            next_action="記録と保存出力から未実行・未完了の範囲を確認せよ",
        )
        raise
    except OSError as error:
        failed = (
            observed
            if observed is not None
            else _metadata(
                argv=argv,
                cwd=cwd,
                git_head=None,
                git_status=None,
                child_exit_code=None,
                timed_out=False,
                signal_number=None,
                stdout_path=None,
                stderr_path=None,
            )
        )
        failed.pop("_metric_errors", None)
        failed.setdefault("execution_conditions", None)
        failure = f"計測または実行記録を保存できない: {directory}: {error}"
        failed.update(record_path=None, wrapper_exit_code=_EXIT_WRAPPER_FAILURE, failure=failure, timing=timing)
        return failed, _EXIT_WRAPPER_FAILURE, failure
    return observed, code, failure


def execution_conditions(environment: dict[str, str] | None = None) -> dict[str, Any]:
    """子へ渡す正規化後の条件を、値を記録せず項目別に比較する。"""
    environment = dict(os.environ if environment is None else environment)
    strip_inherited_venv(environment)
    # 参加券は起動ごとに発行する協調用の値で、検証対象の入力ではない。
    environment.pop(execution_slot.TICKET_ENV, None)
    # 起動層の復元情報とvenvは子にも渡らない。値を比べないのは`_VOLATILE_ENVIRONMENT_NAMES`だけとする。
    # 比較方法を変えた版では旧版の記録と項目の意味が異なるため、versionを上げて旧記録を形式違いとして扱う。
    for name in _VOLATILE_ENVIRONMENT_NAMES & environment.keys():
        environment[name] = ""
    return {
        "version": 5,
        "python": {
            key: hashlib.sha256(value.encode("utf-8")).hexdigest()
            for key, value in {
                "executable": str(pathlib.Path(sys.executable).resolve()),
                "version": sys.version,
                "platform": sys.platform,
            }.items()
        },
        "environment": {key: hashlib.sha256(value.encode("utf-8")).hexdigest() for key, value in sorted(environment.items())},
    }


def reusable_success(path: pathlib.Path, argv: list[str], cwd: pathlib.Path) -> dict[str, Any] | None:
    """同じcleanな版と実行条件の完了記録だけを返す。"""
    try:
        observed = json.loads(path.read_text(encoding="utf-8"))
        head, status = _git_state(cwd)
        if not isinstance(observed, dict) or head is None or status is None or status:
            return None
        conditions = execution_conditions()
        previous = observed.get("execution_conditions")
        if previous != conditions or not isinstance(previous, dict) or isinstance(previous.get("version"), bool):
            reasons = []
            if not isinstance(previous, dict) or previous.get("version") != conditions["version"]:
                reasons.append("実行条件の項目別内訳がない、または形式が異なる")
            else:
                for section in ("python", "environment"):
                    old, current = previous.get(section), conditions[section]
                    if not isinstance(old, dict):
                        reasons.append(f"{section}: 内訳がない")
                        continue
                    for key in sorted(old.keys() | current.keys()):
                        if key not in old:
                            reasons.append(f"{section}: 追加 {key}")
                        elif key not in current:
                            reasons.append(f"{section}: 削除 {key}")
                        elif old[key] != current[key]:
                            reasons.append(f"{section}: 変更 {key}")
            if not reasons:
                reasons.append("実行条件の項目構成が異なる")
            outcome.report_warning(
                f"保存済み検証を再利用しない: {path}: {'; '.join(reasons)}",
                next_action="変更した条件で必要な検証を実行する（環境値は記録・表示しない）",
            )
            return None
        expected = {
            "argv": argv,
            "cwd": str(cwd),
            "git_head": head,
            "git_status": [],
            "execution_conditions": conditions,
            "child_exit_code": 0,
            "wrapper_exit_code": 0,
            "timed_out": False,
            "signal": None,
            "failure": None,
        }
        different = [key for key, value in expected.items() if key not in observed or observed[key] != value]
        if different:
            outcome.report_warning(
                f"保存済み検証を再利用しない: {path}: 条件が異なる {', '.join(different)}",
                next_action="異なる条件に対応する検証を実行せよ",
            )
            return None
        if observed["timed_out"] is not False or any(
            not isinstance(observed[key], int) or isinstance(observed[key], bool)
            for key in ("child_exit_code", "wrapper_exit_code")
        ):
            return None
        for name in ("stdout", "stderr"):
            output = observed.get(name + "_path")
            if not isinstance(output, str) or not pathlib.Path(output).is_absolute():
                return None
            if _file_metrics(pathlib.Path(output)) != (observed.get(name + "_lines"), observed.get(name + "_bytes")):
                return None
    except OSError, ValueError, UnicodeDecodeError:
        return None
    return observed


def _execute(
    argv: list[str],
    cwd: pathlib.Path,
    timeout: float | None,
    directory: pathlib.Path,
    *,
    coordinator: execution_slot.SlotCoordinator | None = None,
) -> tuple[dict[str, Any], int, str | None]:
    """終了する子プロセスを実行して記録を保存し、表示とは別に結果・終了コード・保存の失敗を返す。"""
    child_env = execution_slot.tool_environ(dict(os.environ))
    strip_inherited_venv(child_env)
    conditions = execution_conditions(child_env)
    git_head, git_status = _git_state(cwd)
    stdout_path: pathlib.Path | None = None
    stderr_path: pathlib.Path | None = None
    child_exit_code: int | None = None
    timed_out = False
    signal_number: int | None = None
    wrapper_exit_code = _EXIT_WRAPPER_FAILURE
    failure: str | None = None
    try:
        stdout_path = (directory / "stdout.bin").resolve()
        stderr_path = (directory / "stderr.bin").resolve()
        with stdout_path.open("xb") as stdout_stream, stderr_path.open("xb") as stderr_stream:
            try:
                # 回収不能でも記録を確定するため、context managerの無期限waitを重ねない。
                process = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
                    argv,
                    cwd=cwd,
                    env=child_env,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    start_new_session=os.name == "posix",
                )
                try:
                    if is_human_terminal():
                        print(f"実行中（両出力を保存中）: {shlex.join(argv)}", file=sys.stderr, flush=True)
                    if coordinator is None:
                        process.wait(timeout=timeout)
                    else:
                        deadline = None if timeout is None else time.monotonic() + timeout
                        while True:
                            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                            try:
                                process.wait(timeout=0.2 if remaining is None else min(0.2, remaining))
                                break
                            except subprocess.TimeoutExpired:
                                if deadline is not None and time.monotonic() >= deadline:
                                    raise
                                paused = time.monotonic()
                                coordinator.checkpoint()
                                # 子の安全な区切りで譲った時間は、子の実処理の時間上限から除く。
                                if deadline is not None:
                                    deadline += time.monotonic() - paused
                except subprocess.TimeoutExpired:
                    timed_out = True
                    if is_human_terminal():
                        print(f"時間上限に到達し、子と子孫を停止中: {shlex.join(argv)}", file=sys.stderr, flush=True)
                    residual = process_tree.stop_process_tree(process)
                    if residual:
                        failure = "子孫プロセスを回収できない: " + "; ".join(residual)
                except BaseException as error:
                    reclaim_error = process_tree.stop_owned_process(process, context="lane command exception")
                    if reclaim_error is not None:
                        raise reclaim_error from error
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
    # 実行中に版や本文が変わった観測を、開始時のHEADの成功として再利用しない。
    metadata["execution_conditions"] = (
        conditions if git_head is not None and git_status == [] and _git_state(cwd) == (git_head, []) else None
    )
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
