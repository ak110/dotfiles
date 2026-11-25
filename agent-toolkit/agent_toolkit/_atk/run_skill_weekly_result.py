"""結果UWIを必要とする週次作業の、実行記録と週枠の確定。  pyfltr: allow-comment"""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable
from typing import Any

from agent_toolkit._atk import outcome, run_skill_state, weekly_schedule
from agent_toolkit._common import weekly_work


def _committed(definition: weekly_work.WeeklyWork, slot: str, args: argparse.Namespace) -> bool:
    run_id = getattr(args, "run_id", None)
    if not run_id:
        return False
    try:
        return weekly_schedule.committed_result(definition.mode, slot, run_id=run_id)
    except OSError, subprocess.SubprocessError:
        return False


def result_saved(definition: weekly_work.WeeklyWork, slot: str, args: argparse.Namespace) -> bool:
    if _committed(definition, slot, args):
        return True
    outcome.report_failure(
        f"{definition.title}の結果UWIが保存されていない",
        next_action="診断ログを確認して同じ回を再実行する。自動起動が有効なら次の判定で再試行する",
    )
    return False


def finish(args: argparse.Namespace, slot: str) -> None:
    if run_id := getattr(args, "run_id", None):
        run_skill_state.mark_completed(run_id)
    outcome.report_success(f"週枠 {slot} の結果UWIを確認した")


def resume(
    definition: weekly_work.WeeklyWork,
    args: argparse.Namespace,
    execute: Callable[[argparse.Namespace], int],
    record_failure: Callable[[weekly_work.WeeklyWork, dict[str, Any], str, int], None],
) -> int:
    slot = args.resume_record.weekly_slot
    try:
        work, date = weekly_schedule.parse_slot_id(slot or "")
        if work != definition.mode or date.weekday() != weekly_schedule.SLOT_WEEKDAY:
            raise ValueError
    except ValueError:
        outcome.report_failure(
            "元の実行の週枠が未記録か不正",
            next_action="保存記録とログを確認する。現在の週枠を新規実行するには--resumeを外す",
        )
        return 2
    args.weekly_slot = slot
    if _committed(definition, slot, args):
        finish(args, slot)
        return 0
    code = execute(args)
    stage = (getattr(args, "batch_failure", None) or "").partition(":")[0] or f"execution-{code}"
    if not code and not result_saved(definition, slot, args):
        stage, code = "result-uwi", 1
    # 子の実行中に別の枠へ進んだ場合は、この実行の結果で次の枠の状態を書き戻さない。  # pyfltr: allow-comment
    schedule = weekly_schedule.read_record(definition.mode)
    if schedule.get("slot") == slot:
        if code:
            record_failure(definition, schedule, stage, code)
        else:
            weekly_schedule.write_record(definition.mode, {**schedule, "completed_slot": slot})
    if not code:
        finish(args, slot)
    return code
