r"""agent-toolkit pluginの自律終了Stopフック。

環境変数`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`と、起動側が渡したセッションIDに
一致する会話を対象とする。本フックは対象セッションに限り、`atk agents-exit-session`の
起動漏れを検知してそのターンの継続をblockし再促する。

`atk agents-exit-session`起動の記録はpluginのPostToolUse
（`agent-toolkit/agent_toolkit/_hooks/posttooluse.py`）が担い、`autonomous_exit_invoked`フラグへ
反映する。本フックは同フラグをセッション状態ファイル経由で読み取るのみで、記録は行わない。

判定順序は以下のとおり。

1. 常駐処理が起動した会話でない: 常駐ループ外のセッションのため無条件approve
2. hookの呼出主体が最上位でない: 常駐ループの最上位ではないため無条件approve
3. 終了要求が`requested`で、Stop入力に有効な非`teammate`の背景作業が残る: 終了要求を`consumed`へ、
   `autonomous_exit_invoked`を偽へ戻してblockし、作業の終端を確かめてから終了工程をやり直すよう求める
4. `is_pending_async_work`が真: 非同期処理または未回収の終端結果が残るためapprove
5. `autonomous_exit_invoked`が真: 呼び出し済みのためapprove
6. 上記いずれでもない: blockして順序制約の再促文を返す

3の取り下げは、背景作業が残ったまま同じターン完了のFunction hooksが`/exit`を実行し、
無人のセッションが「Background work is running」の確認画面で止まることを防ぐ。
背景作業は停止すると途中の結果を失うため自動では止めず、本hookは終了要求を取り下げるだけとする。
Stopは同じターン完了の`turn.complete`より先に発火するため、取り下げた要求で`/exit`は実行されない。
次のStopでは要求が`consumed`のため4が背景作業の残存をapproveし、作業の終了後は5・6が再実行を促す。

連続blockの上限は共通入口`stop.py`が管理する。

LLM宛て出力は`_hook_notice`のblock専用整形関数経由で整形し、
`decision: "block"`＋`reason`フィールドへ載せて返す。

各判定分岐の最終判定ラベルと根拠は`_stop_gate.append_stop_log`で
常時ログへ記録する。

委譲先での実行可否: 委譲先は最上位セッションの終了工程を実行できないため、hook入力と環境印で除外する。
"""

import json
import os

from agent_toolkit._atk.agents_exit_session import function_hook_paths
from agent_toolkit._common.process_loop_session import is_process_loop_session
from agent_toolkit._hooks.agent_id import is_main_agent_context
from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter
from agent_toolkit._hooks.session_state import read_state, update_state
from agent_toolkit._hooks.stop_gate import active_non_teammate_tasks, append_stop_log, is_pending_async_work
from agent_toolkit._hooks.stop_gate import parse_stop_session as _parse_stop_session

# このスクリプトのhook識別子。
_HOOK_ID = "autonomous_exit"

# PostToolUse（`posttooluse.py`）が`atk agents-exit-session`の応答検出時に
# セッション状態へ記録するフラグ名。
_STATE_KEY = "autonomous_exit_invoked"

# 本hookが実際に判定した入力だけを述べる再促文。
# 起動元のCLIや起動時のスキル名、個々の工程の完了状態は判定していないため本文へ書かない。
_REASON_BODY = """\
このセッションには常駐ループの終了保証が適用される。
本判定の入力は、`atk agents-exit-session`の実行をセッション状態へ記録していないことだけである。
どの工程が未完了かは判定していない。"""


# 背景作業が残る終了要求を取り下げた場合の本文。残った作業の一覧は後ろへ続ける。
_WITHDRAW_BODY = """\
終了要求を取り下げた。背景作業が残ったまま終了すると、無人のセッションが終了確認の画面で止まるためである。
残っている背景作業:"""

_block_notice = _block_notice_formatter(_HOOK_ID)


def _approve() -> None:
    """空のapprove応答を返す。"""
    print(json.dumps({}, ensure_ascii=False))


def evaluate(payload_text: str) -> tuple[str, str]:
    """自律終了の判定結果と、遮断する場合の理由を返す。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved

    # 常駐ループ外のセッションでは本hookの誘導対象外とする。
    if not is_process_loop_session(session_id, os.environ):
        append_stop_log(session_id, "approve_no_env", {})
        return "approve", ""

    if not is_main_agent_context(payload):
        append_stop_log(session_id, "approve_delegated_session", {})
        return "approve", ""

    remaining = active_non_teammate_tasks(payload.get("background_tasks"))
    if remaining and _withdraw_exit_request(session_id):
        append_stop_log(session_id, "block_exit_request_withdrawn", {"tasks": len(remaining)})
        listing = "\n".join(
            f"- {task['type']}: {task.get('description') or task.get('id') or '説明なし'}" for task in remaining
        )
        reason = _block_notice(
            f"{_WITHDRAW_BODY}\n{listing}",
            fix=(
                "各作業の完了を待つか、自身が起動した作業を停止して終端を確かめる。"
                "その後に`atk agents-exit-session`を単独で再実行する。"
            ),
        )
        return "block", reason

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    state = read_state(session_id)
    if is_pending_async_work(
        transcript_path,
        session_id,
        background_tasks=payload.get("background_tasks"),
        session_state=state,
    ):
        append_stop_log(session_id, "approve_pending_async", {})
        return "approve", ""

    if state.get(_STATE_KEY) is True:
        append_stop_log(session_id, "approve_exit_invoked", {})
        return "approve", ""

    append_stop_log(session_id, "block_autonomous_exit", {})
    reason = _block_notice(
        _REASON_BODY,
        fix="`atk agents-exit-session`を単独で実行する。その実行の記録が本判定を通過させる。",
    )
    return "block", reason


def _withdraw_exit_request(session_id: str) -> bool:
    """終了要求が`requested`なら`consumed`へ戻して`autonomous_exit_invoked`を偽にし、真を返す。"""
    paths = function_hook_paths(session_id)
    if paths is None:
        return False
    _marker, request = paths
    try:
        if request.read_text(encoding="utf-8") != "requested":
            return False
        request.write_text("consumed", encoding="utf-8")
    except OSError:
        return False
    update_state(session_id, lambda state: {**state, _STATE_KEY: False})
    return True


def main(payload_text: str) -> int:
    """`atk agents-exit-session`の起動漏れを検知し再促するエントリポイント。"""
    decision, body = evaluate(payload_text)
    if decision == "block":
        print(json.dumps({"decision": "block", "reason": body}, ensure_ascii=False))
    else:
        _approve()
    return 0
