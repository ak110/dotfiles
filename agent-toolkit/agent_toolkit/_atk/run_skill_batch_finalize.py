"""分析済みのbatchを報告の保存状態から確定する。  pyfltr: allow-comment"""

import pathlib
from typing import Any

import filelock

from agent_toolkit._atk import outcome, run_skill_state
from agent_toolkit._atk.wi import add
from agent_toolkit._common import session_review


def finish_deferred(manifest: dict[str, Any], path: pathlib.Path, error: filelock.Timeout) -> int:
    outcome.report_failure(
        f"振り返りの確定を保留: {error}（実行記録: {path}）",
        next_action=f"ロックの解放後に atk run-skill --resume {manifest['run_id']} で同じ実行の確定を続ける",
    )
    return 1


def complete_batch(manifest: dict[str, Any], path: pathlib.Path) -> int:
    session_review.BatchWriter(path).complete(report_committed=True)
    run_skill_state.mark_completed(manifest["run_id"])
    if log_path := manifest.get("log_path"):
        outcome.report_success(f"スキルを実行した（ログ: {log_path}）")
    return 0


def resume_finished_analysis(manifest: dict[str, Any], path: pathlib.Path) -> int | None:
    if not manifest.get("analysis_succeeded"):
        return None
    try:
        if not add.has_committed_run_report(
            session_review.resolve_report_notes(manifest.get("report_notes")),
            run_id=manifest["run_id"],
            scope=manifest.get("report_scope", session_review.LEGACY_BATCH_SCOPE),
        ):
            return None
        return complete_batch(manifest, path)
    except filelock.Timeout as error:
        return finish_deferred(manifest, path, error)
