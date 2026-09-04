r"""計画作業rootに残る計画バンドルの保存確認Stopフック。

計画作業rootに現存する計画ファイル（メイン）のうち、所有記録が当該セッションを示すものだけを
セッション終了時に1回だけ通知する。所有記録は`atk plans checkout`による取得時と計画バンドルの
新規作成時に生成され、委譲先が取得又は作成した計画には委譲元のセッションが記録される。
他のセッションを示す計画と所有記録を持たない計画は、当該セッションでは処置できないため通知しない。
これらの滞留は`atk plans list`の一覧で判別する。
実装レビューの収束有無は会話の意味に属するためフックでは判定せず、通知を受領した実行主体へ判断を委ねる。
"""

import json
import os
import pathlib

from _hook_notice import block_formatter as _block_notice_formatter
from _plan_file import is_plan_main_file, read_owner_session_id, working_plans_root
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


def _owned_working_plan_paths(session_id: str) -> list[pathlib.Path]:
    """所有記録が当該セッションを示すメイン計画の絶対パスを昇順で返す。

    所有記録が無い計画、他のセッションを示す計画、記録を読み取れない計画はいずれも対象から外す。
    作業rootが存在しない場合は走査が空となり、通知の対象も空になる。
    """
    root = working_plans_root().expanduser().resolve(strict=False)
    paths: set[pathlib.Path] = set()
    for path in root.rglob("*"):
        if path.is_file() and is_plan_main_file(str(path)) and read_owner_session_id(path) == session_id:
            paths.add(path)
    return sorted(paths)


def _mark_notified(state: dict) -> dict | None:
    """保存確認を通知済みにする。"""
    if state.get(_NOTIFIED_STATE_KEY) is True:
        return None
    state[_NOTIFIED_STATE_KEY] = True
    return state


def main(payload_text: str) -> int:
    """所有記録が当該セッションを示す計画バンドルの保存確認を1回だけ促す。"""
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

    paths = _owned_working_plan_paths(session_id)
    if not paths:
        append_stop_log(session_id, "approve_no_working_plans", {})
        _approve()
        return 0

    update_state(session_id, _mark_notified)
    path_list = ", ".join(str(path) for path in paths)
    reason = _block_notice(
        f"Plan bundles owned by this session remain under the plan working root: {path_list}\n"
        "Move a bundle into private-notes only when its implementation review has converged. "
        "Leave the remaining bundles in place and end the turn.",
        fix=(
            "Run `atk plans commit <main plan file name in the plan working root>` for each converged plan, "
            "or end the turn if none has converged."
        ),
    )
    append_stop_log(session_id, "block_working_plan_save", {"paths": len(paths)})
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0
