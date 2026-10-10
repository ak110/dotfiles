"""有限終了する外部コマンドの両ストリームと終了状態を保持する。"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import subprocess
from typing import Any

from agent_toolkit._atk import managed_temp, outcome
from agent_toolkit._common.inherited_venv import strip_inherited_venv
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
    parser.add_argument(
        "--record",
        type=pathlib.Path,
        action="append",
        metavar="PATH",
        help="保存済みrecord.jsonを読み、子を起動しない。反復可。",
    )
    parser.add_argument(
        "--records-file", type=pathlib.Path, metavar="PATH", help="plan-verifyが保存したrecords配列を持つJSON。"
    )
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


def execute(argv: list[str], cwd: pathlib.Path, timeout: float | None) -> tuple[dict[str, Any], int, str | None]:
    """有限の子実行と記録保存を行い、表示から独立した結果・終了コード・保存失敗を返す。"""
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
        directory = managed_temp.create_managed_temp("atk-command")
        stdout_path = (directory / "stdout.bin").resolve()
        stderr_path = (directory / "stderr.bin").resolve()
        with stdout_path.open("xb") as stdout_stream, stderr_path.open("xb") as stderr_stream:
            try:
                with subprocess.Popen(  # noqa: S603
                    argv, cwd=cwd, env=child_env, stdout=stdout_stream, stderr=stderr_stream
                ) as process:
                    try:
                        process.wait(timeout=timeout)
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


def read_records(paths: list[pathlib.Path], bundle: pathlib.Path | None = None) -> tuple[dict[str, Any], int]:
    """終了済み実行をまとめて読み、旧記録の未記録値と取得失敗を区別して返す。"""
    missing_records = []
    if bundle is not None:
        if not bundle.is_absolute():
            raise ValueError("--records-fileは絶対パスで指定する")
        document = json.loads(bundle.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("records"), list):
            raise ValueError("保存結果はrecordsにrecord_pathの文字列配列を持つJSON objectを指定する")
        if any(not isinstance(value, str) for value in document["records"]):
            raise ValueError("recordsは保存JSONの絶対パスの文字列配列にする")
        results = document.get("results", [])
        if not isinstance(results, list) or any(not isinstance(row, dict) or "record_path" not in row for row in results):
            raise ValueError("resultsはrecord_pathを持つ実行結果の配列にする")
        if (
            "results" in document
            and [row["record_path"] for row in results if row["record_path"] is not None] != document["records"]
        ):
            raise ValueError("resultsの保存先とrecordsの順序が一致しない")
        paths = [*(pathlib.Path(value) for value in document["records"]), *paths]
        missing_records = [row for row in results if row["record_path"] is None]
    if not paths and bundle is None:
        raise ValueError("--recordか--records-fileへ保存済み実行を指定する")
    entries = []
    errors = [
        {
            "record_path": None,
            "order": row.get("order"),
            "error": row.get("failure"),
            "record": row.get("record"),
            "next_action": "子の結果と両出力を保持し、保存失敗を解消して記録を回復する",
        }
        for row in missing_records
    ]
    for path in dict.fromkeys(paths):
        try:
            if not path.is_absolute():
                raise ValueError("実行記録は絶対パスで指定する")
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("実行記録はJSON objectである必要がある")
            fields = {
                "argv",
                "cwd",
                "git_head",
                "git_status",
                "child_exit_code",
                "timed_out",
                "signal",
                "stdout_path",
                "stderr_path",
            }
            if not fields <= record.keys() or not isinstance(record["argv"], list) or not isinstance(record["cwd"], str):
                raise ValueError("実行条件と子の終了状態・保存先が不足している")
            if any(not isinstance(value, str) for value in record["argv"]) or not pathlib.Path(record["cwd"]).is_absolute():
                raise ValueError("実行argvまたはcwdの型・形式が不正である")
            if not isinstance(record["timed_out"], bool):
                raise ValueError("timed_outは真偽値である必要がある")
            if record["git_head"] is not None and (
                not isinstance(record["git_head"], str)
                or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", record["git_head"]) is None
            ):
                raise ValueError("git_headは取得時の完全OIDかnullである必要がある")
            if record["git_status"] is not None and (
                not isinstance(record["git_status"], list) or any(not isinstance(value, str) for value in record["git_status"])
            ):
                raise ValueError("git_statusは文字列配列かnullである必要がある")
            for field in ("child_exit_code", "signal", "wrapper_exit_code"):
                value = record.get(field)
                if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                    raise ValueError(f"{field}は整数かnullである必要がある")
            if record.get("failure") is not None and not isinstance(record["failure"], str):
                raise ValueError("failureは文字列かnullである必要がある")
            missing = [field for field in ("wrapper_exit_code", "failure") if field not in record]
            displayed: dict[str, Any] = dict(record, requested_record=str(path), unrecorded=missing)
            for field in missing:
                displayed[field] = None
            for stream in ("stdout", "stderr"):
                source = record[stream + "_path"]
                if source is not None and (not isinstance(source, str) or not pathlib.Path(source).is_absolute()):
                    raise ValueError(f"{stream}_pathは絶対パスかnullで指定する")
                displayed[stream] = pathlib.Path(source).read_bytes().decode("utf-8", errors="replace") if source else None
            entries.append(displayed)
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            errors.append(
                {
                    "record_path": str(path),
                    "error": str(error),
                    "next_action": "保存先とJSON・両出力を確認して同じ読取を再実行する",
                }
            )
    return {"records": entries, "errors": errors}, _EXIT_WRAPPER_FAILURE if errors else 0


def dispatch(args: argparse.Namespace) -> int:
    """外部コマンドを実行し、保存結果のJSONと実際の終了状態を返す。"""
    argv = list(args.command_argv)
    records = getattr(args, "record", None) or []
    bundle = getattr(args, "records_file", None)
    if records or bundle:
        if argv or args.cwd is not None or args.timeout is not None:
            outcome.report_failure(
                "保存済み読取と子の実行指定は混在できない", next_action="--record・--records-fileだけで再実行する"
            )
            return _EXIT_WRAPPER_FAILURE
        try:
            result, code = read_records(records, bundle)
        except (OSError, UnicodeError, ValueError) as error:
            outcome.report_failure(str(error), next_action="保存結果とパスを確認し同じ読取を再実行する")
            return _EXIT_WRAPPER_FAILURE
        print(json.dumps(result, ensure_ascii=False))
        if code:
            outcome.report_failure("一部の保存済み実行を読めない", next_action="errorsの対象を解消して同じ読取を再実行する")
        return code
    if not argv or argv[0] != "--" or len(argv) == 1:
        outcome.report_failure(
            "`--`以後に実行するCOMMANDがありません", next_action="`atk run-command -- COMMAND [ARG...]`で再実行する"
        )
        return _EXIT_WRAPPER_FAILURE
    cwd = args.cwd if args.cwd is not None else pathlib.Path.cwd().resolve()
    metadata, wrapper_exit_code, failure = execute(argv[1:], cwd, args.timeout)
    print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    if wrapper_exit_code == 0:
        outcome.report_success("外部コマンドが終了した", outcome.ResultKind.VALUE_OUTPUT)
    else:
        detail = failure or f"外部コマンドが終了コード{wrapper_exit_code}で終了した"
        paths = f"stdout={metadata['stdout_path']}, stderr={metadata['stderr_path']}"
        outcome.report_failure(detail, next_action=f"保存先を診断する: {paths}")
    return wrapper_exit_code
