"""受理された報告本文と、その作業に対応する実際の可視発話を比較する。

委譲先での実行可否: 委譲先の返却はメインの報告発話の対象ではなく、入力と環境印で除外する。
"""

from agent_toolkit._hooks import termination_evidence
from agent_toolkit._hooks.agent_id import is_main_agent_context
from agent_toolkit._hooks.notice import block_formatter
from agent_toolkit._hooks.session_state import read_state
from agent_toolkit._hooks.stop_gate import append_stop_log, parse_stop_session

_block_notice = block_formatter("completion_report_delivery_advisor")


def evaluate(payload_text: str) -> tuple[str, str]:
    """本文の取得不能は非遮断とし、観測できた未発話だけを返す。"""
    resolved = parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    state = read_state(session_id)
    if not is_main_agent_context(payload) or state.get("autonomous_exit_invoked") is True:
        return "approve", ""
    if state.get("process_wi_skill_invoked") is True:
        # 自律モードの保存検収は報告用UWIが所有し、可視発話の比較へ混ぜない。
        return "approve", ""
    missing: list[str] = []
    for work_id, work in termination_evidence.pending_work(payload):
        for stage, report in work.get("reports", {}).items():
            if report.get("delivered") is True:
                continue
            observed = termination_evidence.report_is_visible(payload, report)
            if observed is None:
                append_stop_log(session_id, "report_delivery_unknown", {"work_id": work_id, "stage": stage})
            elif observed:
                termination_evidence.mark_delivered(session_id, work_id, stage, report["call_id"])
            else:
                missing.append(f"作業 {work_id}、報告 {stage}（呼び出し {report['call_id']}）:\n{report['text']}")
    if not missing:
        return "approve", ""
    append_stop_log(session_id, "block_report_not_delivered", {"count": len(missing)})
    return "block", _block_notice(
        "構造確認で受理した報告本文を可視発話で確認できない。\n\n"
        + "\n\n".join(missing)
        + "\n\n"
        + termination_evidence.decision_hint(payload),
        fix=(
            "列挙した受理本文を省略せず発話する。"
            "中止や待機の判断がある場合は原証拠と対象作業を`atk run-script termination-evidence`へ記録する。"
        ),
    )
