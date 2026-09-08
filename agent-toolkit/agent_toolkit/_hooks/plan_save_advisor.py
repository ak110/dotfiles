r"""計画作業rootに残る計画バンドルの保存確認Stopフック。

計画作業rootに現存する計画ファイル（メイン）のうち、所有記録が当該セッションを示すものだけを
セッション終了時に1回だけ通知する。所有記録は`atk plans checkout`による取得時と計画バンドルの
新規作成時に生成され、委譲先が取得又は作成した計画には委譲元のセッションが記録される。
他のセッションを示す計画と所有記録を持たない計画は、当該セッションでは処置できないため通知しない。
これらの滞留は`atk plans list`の一覧で判別する。
実行レビューの収束有無は会話の意味に属し、フックが受け取るStop payloadと計画ファイルからは判定できない。
そのため、フックは実行できる処置の有無を根拠に終了を遮断せず、通知を受領した実行主体へ判断を委ねる。

委譲先での実行可否: 委譲先は委譲元が所有する計画バンドルを保存できないため、環境変数による除外が必要である。
"""

import json
import os
import pathlib

from agent_toolkit._hooks.notice import _WARN_TAG, set_warning_session_id
from agent_toolkit._hooks.notice import formatter as _notice_formatter
from agent_toolkit._hooks.session_state import read_state, update_state
from agent_toolkit._hooks.stop_gate import append_stop_log, is_pending_async_work
from agent_toolkit._hooks.stop_gate import parse_stop_session as _parse_stop_session
from agent_toolkit._plan.locations import is_plan_main_file, read_owner_session_id, working_plans_root

_HOOK_ID = "agent-toolkit/plan_save_advisor"
_ENV_DELEGATED_SESSION = "AGENT_TOOLKIT_DELEGATED_SESSION"
_ENV_PROCESS_LOOP_SESSION = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_LEGACY_ENV_PROCESS_LOOP_SESSION = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"
_NOTIFIED_STATE_KEY = "working_plan_save_notified"

_notice = _notice_formatter(_HOOK_ID, default_tag=_WARN_TAG)


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


def evaluate(payload_text: str) -> tuple[str, str]:
    """計画バンドルの保存判定結果と、通知する場合の本文を返す。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        append_stop_log("", "approve_invalid_payload", {})
        return "approve", ""
    session_id, payload = resolved
    set_warning_session_id(session_id)

    if os.environ.get(_ENV_DELEGATED_SESSION) == "1":
        append_stop_log(session_id, "approve_delegated_session", {})
        return "approve", ""

    if os.environ.get(_ENV_PROCESS_LOOP_SESSION) == "1" or os.environ.get(_LEGACY_ENV_PROCESS_LOOP_SESSION) == "1":
        append_stop_log(session_id, "approve_process_loop_session", {})
        return "approve", ""

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if is_pending_async_work(
        transcript_path,
        session_id,
        background_tasks=payload.get("background_tasks"),
    ):
        append_stop_log(session_id, "approve_pending_async", {})
        return "approve", ""

    state = read_state(session_id)
    if state.get(_NOTIFIED_STATE_KEY) is True:
        append_stop_log(session_id, "approve_already_notified", {})
        return "approve", ""

    paths = _owned_working_plan_paths(session_id)
    if not paths:
        append_stop_log(session_id, "approve_no_working_plans", {})
        return "approve", ""

    update_state(session_id, _mark_notified)
    path_list = ", ".join(str(path) for path in paths)
    body = _notice(
        f"当該セッションが所有する計画バンドルが計画作業ルートに残っている: {path_list}\n"
        "実行レビューが収束したバンドルだけを"
        "`atk plans commit <計画作業ルート内の計画ファイル（メイン）名>`でprivate-notesへ保存する。"
        "残りのバンドルはその場に残してターンを終了する。",
        removable_cause=True,
    )
    append_stop_log(session_id, "notify_working_plan_save", {"paths": len(paths)})
    return "notify", body


def main(payload_text: str) -> int:
    """所有記録が当該セッションを示す計画バンドルの保存確認を1回だけ促す。"""
    decision, body = evaluate(payload_text)
    if decision == "notify":
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "Stop",
                        "additionalContext": body,
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0
