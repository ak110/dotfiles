"""登録済みplugin内Pythonスクリプトを現在のagent-toolkit環境で実行する。"""

from __future__ import annotations

import argparse
import pathlib
import runpy
import sys

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._common import next_action as _next_action

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_PATHS = {
    "agent-doc-changes": pathlib.Path("skills/plan-mode/scripts/list_agent_doc_changes.py"),
    "plan-create": pathlib.Path("skills/plan-mode/scripts/create_plan_files.py"),
    "plan-check": pathlib.Path("skills/plan-mode/scripts/check_plan_file.py"),
    "plan-progress": pathlib.Path("skills/plan-mode/scripts/append_progress_log.py"),
    "pick-wi-check": pathlib.Path("skills/process-wi/scripts/check_selection.py"),
    "completion-report-check": pathlib.Path("skills/completion-report/scripts/check_completion_report.py"),
    "exec-review-evidence-check": pathlib.Path("skills/review-standards/scripts/check_exec_review_evidence.py"),
    "review-contract": pathlib.Path("skills/review-standards/scripts/review_contract.py"),
    "record-stall-detection": pathlib.Path("skills/delegation/scripts/record_stall_detection.py"),
    "session-review-evidence": pathlib.Path("skills/session-review/scripts/session_review_evidence.py"),
    "session-review-prepare": pathlib.Path("skills/session-review/scripts/session_review_prepare.py"),
    "writing-check-dash": pathlib.Path("skills/writing-standards/scripts/check_dash.py"),
}


def build_parser(parser: argparse.ArgumentParser) -> None:
    """run-scriptの引数を登録する。"""
    parser.add_argument(
        "script_name",
        choices=sorted(SCRIPT_PATHS),
        metavar="SCRIPT",
        help=f"実行する登録済みplugin内スクリプトの公開名（{', '.join(sorted(SCRIPT_PATHS))}）。",
    )
    parser.add_argument(
        "script_args",
        nargs="*",
        metavar="ARG",
        help="`--`の後に指定するスクリプトへの引数。",
    )


def registered_script_path(script_name: str) -> pathlib.Path:
    """登録名に対応するplugin内スクリプトの検証済み絶対パスを返す。"""
    try:
        relative = SCRIPT_PATHS[script_name]
    except KeyError as exc:
        raise _next_action.ActionableError(
            f"未登録のscriptです: {script_name}",
            next_action=f"登録済みscriptから選び直す: {', '.join(sorted(SCRIPT_PATHS))}",
        ) from exc
    target = (PLUGIN_ROOT / relative).resolve()
    if not target.is_relative_to(PLUGIN_ROOT) or not target.is_file():
        raise _next_action.ActionableError(
            f"登録済みscriptがplugin root内に存在しません: {script_name}",
            next_action="agent-toolkitの配布物が欠けている。プラグインを更新するか、ユーザーへ報告する",
        )
    return target


def dispatch(args: argparse.Namespace) -> int:
    """登録済みスクリプトへ引数と終了コードを透過する。"""
    target = registered_script_path(args.script_name)
    script_args = list(args.script_args)
    if script_args[:1] == ["--"]:
        script_args.pop(0)
    previous_argv = sys.argv
    previous_path = sys.path
    previous_path_contents = list(sys.path)
    try:
        sys.argv = [str(target), *script_args]
        sys.path.insert(0, str(target.parent))
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        _outcome.report_failure(
            f"{args.script_name}が失敗した: {exc.code}",
            next_action=(
                f"表示された理由を解消して再実行する。受理形式は`atk run-script {args.script_name} -- --help`で確かめる"
            ),
        )
        return 1
    finally:
        sys.argv = previous_argv
        previous_path[:] = previous_path_contents
        sys.path = previous_path
    return 0
