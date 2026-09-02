r"""計画作業rootに残る計画バンドルの保存確認Stopフック。

計画作業rootに現存する計画ファイル（メイン）をセッション終了時に1回だけ通知する。
当該セッションが作成した計画に限らず、取得しただけの計画と別のセッションが残した計画も対象とする。
担当の別と実装レビューの収束有無は会話の意味に属するためフックでは判定せず、
通知を受領した実行主体へ判断を委ねる。
"""

import json
import os
import pathlib

from _hook_notice import block_formatter as _block_notice_formatter
from _plan_file import is_plan_main_file, working_plans_root
from _session_state import read_state, update_state
from _stop_gate import append_stop_log, is_pending_async_work
from _stop_gate import parse_stop_session as _parse_stop_session

_HOOK_ID = "agent-toolkit/plan_save_advisor"
_ENV_DELEGATED_SESSION = "AGENT_TOOLKIT_DELEGATED_SESSION"
_ENV_PROCESS_LOOP_SESSION = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_LEGACY_ENV_PROCESS_LOOP_SESSION = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"
_NOTIFIED_STATE_KEY = "working_plan_save_notified"

_block_notice = _block_notice_formatter(_HOOK_ID)


def _approve() -> None:
    """空のapprove応答を返す。"""
    print(json.dumps({}, ensure_ascii=False))


def _existing_working_plan_paths() -> list[pathlib.Path]:
    """計画作業rootに現存するメイン計画の絶対パスを昇順で返す。

    走査で対象を求めるため、当該セッションが編集していない計画も通知の対象へ入る。
    作業rootが存在しない場合は走査が空となり、通知の対象も空になる。
    """
    root = working_plans_root().expanduser().resolve(strict=False)
    paths: set[pathlib.Path] = set()
    for path in root.rglob("*"):
        if path.is_file() and is_plan_main_file(str(path)):
            paths.add(path)
    return sorted(paths)


def _mark_notified(state: dict) -> dict | None:
    """保存確認を通知済みにする。"""
    if state.get(_NOTIFIED_STATE_KEY) is True:
        return None
    state[_NOTIFIED_STATE_KEY] = True
    return state


def main(payload_text: str) -> int:
    """計画作業rootに残る計画バンドルの保存確認を1回だけ促す。"""
    resolved = _parse_stop_session(payload_text, _approve)
    if resolved is None:
        append_stop_log("", "approve_invalid_payload", {})
        return 0
    session_id, payload = resolved

    if os.environ.get(_ENV_DELEGATED_SESSION) == "1":
        append_stop_log(session_id, "approve_delegated_session", {})
        _approve()
        return 0

    if os.environ.get(_ENV_PROCESS_LOOP_SESSION) == "1" or os.environ.get(_LEGACY_ENV_PROCESS_LOOP_SESSION) == "1":
        append_stop_log(session_id, "approve_process_loop_session", {})
        _approve()
        return 0

    if payload.get("stop_hook_active") is True:
        append_stop_log(session_id, "approve_stop_hook_active", {"stop_hook_active": True})
        _approve()
        return 0

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if is_pending_async_work(
        transcript_path,
        session_id,
        background_tasks=payload.get("background_tasks"),
    ):
        append_stop_log(session_id, "approve_pending_async", {})
        _approve()
        return 0

    state = read_state(session_id)
    if state.get(_NOTIFIED_STATE_KEY) is True:
        append_stop_log(session_id, "approve_already_notified", {})
        _approve()
        return 0

    paths = _existing_working_plan_paths()
    if not paths:
        append_stop_log(session_id, "approve_no_working_plans", {})
        _approve()
        return 0

    update_state(session_id, _mark_notified)
    path_list = ", ".join(str(path) for path in paths)
    reason = _block_notice(
        f"Plan bundles remain under the plan working root: {path_list}\n"
        "Move a bundle into private-notes only when it is yours and its implementation review has converged. "
        "Leave bundles owned by other sessions in place and end the turn.",
        fix=(
            "Run `atk plans commit <relative main plan path>` for each converged plan, or end the turn if none has converged."
        ),
    )
    append_stop_log(session_id, "block_working_plan_save", {"paths": len(paths)})
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0
