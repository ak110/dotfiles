"""常駐ループのセッションが無進捗の反復に入った状態を検知して停止するStopフック。

`atk wi process-loop`が起動したセッションでも、無進捗が続くときだけ終了を求める。
権限拒否などで工程が進まない状態では、ツール呼び出しを伴わないターンと続行要求だけが繰り返され、
ユーザーが介入するまでトークンを消費し続ける。本判定はこの反復を検知し、そのセッションへ終了要求を送る。

統括への終了要求は本モジュールの`terminate`だけが送る。
無進捗の停止（`halt`）と、`stop.py`が連続blockの上限で遮断を打ち切る場合の終了はどちらも`terminate`を呼び、
対話型の統括は入力待ちのまま残らず、ランチャーへ終了を返す。

適用対象は、統括の会話IDを確定でき（`is_identified_process_loop_session`）、hook入力の会話IDと一致する最上位セッションに限る。
委譲先セッション、対話セッション、統括が入れ子で起動したCLIは対象外とする。
終了要求は祖先の対話CLIへ届くため、環境印だけで判定すると、入れ子の非対話CLI（`codex exec`など）のhookから外側の統括を止めうる。

無進捗ターンは、前回のStop判定から今回のStop判定までにセッション記録へ加わったエントリの中に、
自セッションのツール呼び出しが1件も無いターンとする。連続回数が`_THRESHOLD`へ達した時点で停止工程へ進む。

本判定は、委譲先またはバックグラウンドタスクの完了を待つターンと無進捗ターンを`is_pending_async_work`で区別できることを
前提とする。この前提が成立しない場合、正当な待機のセッションが本判定で停止する。

本判定は閾値へ達すると停止の意図（`halt`）だけを返し、終了に関わる副作用は実行しない。
Stopの共通ハンドラー（`stop.py`）が全判定を終えてから`halt`を呼び、終了要求の成否に合わせて他の判定の継続要求を扱う。
判定の途中で終了要求を送ると、後続の判定が終了済みのセッションへ継続を求める応答と矛盾するためである。

停止工程（`terminate`）の順序は次のとおりとする。

1. 現在の対話CLI本体を再識別する。Function hooksが読み込まれたClaude Codeではターン後の`/exit`を要求する。
   それ以外（Codex・agyの対話CLIを含む）では一致した単一PIDへ終了要求を送る
2. 判定根拠と終了要求の結果を常時ログへ記録する
3. 停止の事実と終了要求の結果を`systemMessage`でユーザーへ伝える本文を返す

委譲先での実行可否: hook入力と委譲先の環境印で判定し、委譲先では何もしない。
"""

import os
from collections.abc import Mapping

lazy from agent_toolkit._atk import agents_exit_session as _agents_exit_session
lazy from agent_toolkit._common.process_loop_session import is_identified_process_loop_session
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context
lazy from agent_toolkit._hooks.background_tasks import is_pending_async_work
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.stop_session import parse_stop_session as _parse_stop_session
lazy from agent_toolkit._hooks.transcript_scan import has_tool_use_block, read_transcript_entries_cached

_THRESHOLD = 3


_COUNT_STATE_KEY = "stop_no_tool_turn_count"


_OBSERVED_STATE_KEY = "stop_observed_entry_count"


def _read_int_state(state: dict, key: str) -> int:
    value = state.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return value


def _store(session_id: str, count: int, observed: int) -> None:

    def _update(state: dict) -> dict:
        state[_COUNT_STATE_KEY] = count
        state[_OBSERVED_STATE_KEY] = observed
        return state

    update_state(session_id, _update)


def is_target_session(session_id: str, payload: dict) -> bool:
    return is_identified_process_loop_session(session_id, os.environ) and is_main_agent_context(payload)


def halt(session_id: str, count: int) -> tuple[str, str]:
    return terminate(
        session_id,
        f"無進捗のターンが{count}回続いた。",
        "halt_busy_loop",
        {"count": count, "threshold": _THRESHOLD},
    )


def terminate(session_id: str, cause: str, log_decision: str, log_context: Mapping[str, object]) -> tuple[str, str]:
    status, target = _agents_exit_session.request_termination(session_id=session_id)
    append_stop_log(session_id, log_decision, {**log_context, "termination": status})
    lines = [cause]
    if status == "exit_requested":
        lines.append("現在のセッションの終了を要求した。ターン終了後に/exitで終了する。")
    elif status == "terminating" and target is not None:
        lines.append(f"現在のセッションへ終了要求を送った: {target.host} pid={target.pid}。")
    elif status == "changed":
        lines.append("終了対象が識別後に変化したため、セッションの終了要求は送っていない。")
    else:
        lines.append("現在の対話CLI本体を一意に識別できないため、セッションの終了要求は送っていない。")
    if status in {"changed", "unsupported"}:
        lines.append(
            "次の操作: このセッションを操作できる端末でCtrl+Cを押して反復を中断せよ。"
            f"端末を操作できない場合は、実行環境の管理者へ会話ID `{session_id}` を伝え、当該セッションの終了を依頼せよ。"
        )
    return status, "\n".join(lines)


def evaluate(payload_text: str) -> tuple[str, str]:
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved

    if not is_target_session(session_id, payload):
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
        session_state=state,
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
    return "halt", str(count)
