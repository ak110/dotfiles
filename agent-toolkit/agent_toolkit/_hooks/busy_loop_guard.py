"""常駐ループのセッションが無進捗の反復に入った状態を検知して停止するStopフック。

`atk wi process-loop`が起動したセッションでは、ターンを終えるたびに`/goal`の目標評価が発動する。
権限拒否などで工程が進まない状態では、ツール呼び出しを伴わないターンと目標評価だけが繰り返され、
利用者が介入するまでトークンを消費し続ける。本判定はこの反復を検知し、常駐処理へ中断を要求したうえで
当該セッションへ終了要求を送る。

適用対象は環境変数`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`（移行互換名`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`）の
最上位セッションに限り、委譲先セッションと対話セッションは対象の外に置く。

無進捗ターンは、前回のStop判定から今回のStop判定までに会話記録へ加わったエントリの中に、
自セッションのツール呼び出しが1件も無いターンとする。連続回数が`_THRESHOLD`へ達した時点で停止工程へ進む。

本判定は、委譲先又は背景ジョブの完了を待つターンと無進捗ターンを`is_pending_async_work`で区別できることを
前提とする。この前提が崩れた場合、正当な待機のセッションが本判定で停止する。

停止工程の順序は次のとおりとする。

1. 判定根拠を常時ログへ記録する
2. 常駐処理への中断要求を作成し、現反復の終了後に次の反復へ進まない状態にする
3. 停止の事実を`systemMessage`で利用者へ伝える
4. 現在の対話CLI本体を再識別し、一致した単一PIDへ終了要求を送る

Stopの戻り値は`approve`とし、ターンの継続は強制しない。

委譲先での実行可否: hook入力と委譲先の環境印で判定し、委譲先では何もしない。
"""

import json
import os

from agent_toolkit._atk import agents_exit_session as _agents_exit_session
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log
from agent_toolkit._hooks.agent_id import is_main_agent_context
from agent_toolkit._hooks.session_state import read_state, update_state
from agent_toolkit._hooks.stop_gate import (
    append_stop_log,
    has_tool_use_block,
    is_pending_async_work,
    read_transcript_entries_cached,
)
from agent_toolkit._hooks.stop_gate import parse_stop_session as _parse_stop_session

# 常駐ループから起動されたセッションであることを示す環境変数名。
_ENV_REQUIRED = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"

# 更新中に旧process-loopと併存するため受理する移行互換名。
_LEGACY_ENV_REQUIRED = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"

# 停止工程へ進む連続無進捗ターン数。
_THRESHOLD = 3

# 連続した無進捗ターン数を保持するセッション状態のキー。
_COUNT_STATE_KEY = "stop_no_tool_turn_count"

# 観測済みの会話記録エントリ数を保持するセッション状態のキー。
_OBSERVED_STATE_KEY = "stop_observed_entry_count"


def _read_int_state(state: dict, key: str) -> int:
    """セッション状態から非負整数を読み取る。値が不正な場合は0を返す。"""
    value = state.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return value


def _store(session_id: str, count: int, observed: int) -> None:
    """連続回数と観測済みエントリ数をセッション状態へ書き込む。"""

    def _update(state: dict) -> dict:
        state[_COUNT_STATE_KEY] = count
        state[_OBSERVED_STATE_KEY] = observed
        return state

    update_state(session_id, _update)


def _is_target_session(payload: dict) -> bool:
    """常駐ループの最上位セッションである場合に真を返す。"""
    if os.environ.get(_ENV_REQUIRED) != "1" and os.environ.get(_LEGACY_ENV_REQUIRED) != "1":
        return False
    return is_main_agent_context(payload)


def _halt(session_id: str, count: int) -> str:
    """中断要求の作成と終了要求の送出を行い、利用者へ伝える本文を返す。"""
    abort_path = _process_loop_log.request_abort()
    status, target = _agents_exit_session.request_termination()
    append_stop_log(
        session_id,
        "halt_busy_loop",
        {"count": count, "threshold": _THRESHOLD, "termination": status},
    )
    lines = [
        f"無進捗のターンが{count}回続いたため、常駐処理の停止とセッションの終了を実行した。",
        f"常駐処理への中断要求: {abort_path}（解除は`atk wi process-loop abort-cancel`）。",
    ]
    if status == "terminating" and target is not None:
        lines.append(f"現在のセッションへ終了要求を送った: {target.host} pid={target.pid}。")
    elif status == "changed":
        lines.append("終了対象が識別後に変化したため、セッションの終了要求は送っていない。")
    else:
        lines.append("現在の対話CLI本体を一意に識別できないため、セッションの終了要求は送っていない。")
    return "\n".join(lines)


def evaluate(payload_text: str) -> tuple[str, str]:
    """無進捗ターンの連続を判定し、停止した場合は利用者向けの本文を返す。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved

    if not _is_target_session(payload):
        append_stop_log(session_id, "approve_busy_loop_not_applicable", {})
        return "approve", ""

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if not transcript_path:
        append_stop_log(session_id, "approve_busy_loop_no_transcript", {})
        return "approve", ""

    entries = read_transcript_entries_cached(transcript_path)
    total = len(entries)
    state = read_state(session_id)
    observed = min(_read_int_state(state, _OBSERVED_STATE_KEY), total)
    count = _read_int_state(state, _COUNT_STATE_KEY)

    pending = is_pending_async_work(
        transcript_path,
        session_id,
        background_tasks=payload.get("background_tasks"),
    )
    if pending or has_tool_use_block(entries[observed:]):
        _store(session_id, 0, total)
        append_stop_log(session_id, "approve_busy_loop_progress", {"pending": pending})
        return "approve", ""

    count += 1
    if count < _THRESHOLD:
        _store(session_id, count, total)
        append_stop_log(session_id, "approve_busy_loop_below_threshold", {"count": count, "threshold": _THRESHOLD})
        return "approve", ""

    _store(session_id, 0, total)
    return "notify_user", _halt(session_id, count)


def main(payload_text: str) -> int:
    """無進捗の反復を検知して常駐処理とセッションを停止するエントリポイント。"""
    decision, body = evaluate(payload_text)
    if decision == "notify_user":
        print(json.dumps({"systemMessage": body}, ensure_ascii=False))
    else:
        print(json.dumps({}, ensure_ascii=False))
    return 0
