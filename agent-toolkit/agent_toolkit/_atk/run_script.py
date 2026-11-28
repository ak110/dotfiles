from __future__ import annotations

import argparse
import pathlib
import runpy
import sys

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.next_action import recover_from_failure

PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_PATHS = {
    "wait-ci": pathlib.Path("agent_toolkit/wait_ci.py"),
    "cost-metrics": pathlib.Path("skills/atk-mode/scripts/cost_metrics.py"),
    "improvement-intake": pathlib.Path("skills/atk-mode/scripts/improvement_intake.py"),
    "check-exec-review-origin": pathlib.Path("skills/atk-mode/scripts/check_exec_review_origin.py"),
    "review-adoption": pathlib.Path("skills/atk-mode/scripts/review_adoption.py"),
    "review-second-round": pathlib.Path("skills/atk-mode/scripts/review_second_round.py"),
    "delegation-prompt": pathlib.Path("skills/atk-mode/scripts/delegation_prompt.py"),
    "history-compare": pathlib.Path("skills/atk-mode/scripts/compare_history.py"),
    "commit-with-timeout": pathlib.Path("skills/atk-mode/scripts/run_commit.py"),
    "agent-doc-changes": pathlib.Path("skills/atk-mode/scripts/list_agent_doc_changes.py"),
    "plan-create": pathlib.Path("skills/atk-mode/scripts/create_plan_files.py"),
    "plan-check": pathlib.Path("skills/atk-mode/scripts/check_plan_file.py"),
    "plan-progress": pathlib.Path("skills/atk-mode/scripts/append_progress_log.py"),
    "plan-commits": pathlib.Path("skills/atk-mode/scripts/get_plan_commits.py"),
    "plan-rewrite": pathlib.Path("skills/atk-mode/scripts/rewrite_plan_commits.py"),
    "pick-wi-check": pathlib.Path("skills/atk-mode/scripts/check_selection.py"),
    "termination-evidence": pathlib.Path("agent_toolkit/_hooks/termination_evidence.py"),
    "multi-turn-trials": pathlib.Path("skills/atk-mode/scripts/multi_turn_trials.py"),
    "record-stall-detection": pathlib.Path("skills/atk-mode/scripts/record_stall_detection.py"),
    "wait-compaction-context": pathlib.Path("skills/atk-mode/scripts/wait_compaction_context.py"),
    "session-review-evidence": pathlib.Path("skills/atk-mode/scripts/session_review_evidence.py"),
    "warning-count": pathlib.Path("skills/atk-mode/scripts/session_evidence_warn.py"),
    "continuation-count": pathlib.Path("skills/atk-mode/scripts/session_evidence_continuations.py"),
    "session-review-prepare": pathlib.Path("skills/atk-mode/scripts/session_review_prepare.py"),
    "session-review-state": pathlib.Path("skills/atk-mode/scripts/session_review_state.py"),
    "research-sources": pathlib.Path("skills/atk-mode/scripts/research_sources.py"),
    "writing-check-dash": pathlib.Path("agent_toolkit/_atk/check_dash.py"),
    "wi-quote-check": pathlib.Path("skills/atk-mode/scripts/wi_quote_check.py"),
}


def build_parser(parser: argparse.ArgumentParser) -> None:
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
            next_action=recover_from_failure(
                component="配布物の欠落",
                evidence="診断に示されたパス",
                first_step="要求されたスクリプトが導入済みの配布物にあるか確かめる",
                retry="配布物を修復し、スクリプトが未実行である場合だけ再試行する",
            ),
        )
    return target


def dispatch(args: argparse.Namespace) -> int:
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
