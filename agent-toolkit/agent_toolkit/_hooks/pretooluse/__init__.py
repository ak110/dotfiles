"""PreToolUse統合フックの責務別実装。"""

from agent_toolkit._hooks import termination_evidence
lazy from agent_toolkit._common.next_action import recover_from_failure
lazy from agent_toolkit._hooks.notice import formatter
lazy from agent_toolkit._hooks.pretooluse import dispatch as _dispatch


def main(payload_text: str) -> int:
    notices: tuple[str, ...] = ()
    try:
        termination_evidence.observe_tool(payload_text, after=False)
    except (OSError, ValueError, TypeError, KeyError) as error:
        notices = (
            formatter("pretooluse")(
                f"ツール呼び出しの開始記録を保存できなかった。元の操作の成否はこの診断では分からない: {error}",
                tag="warn",
                fix=recover_from_failure(
                    component="ツール開始の記録",
                    evidence="元の操作結果と記録の診断",
                    first_step="元の操作の成否を確認する",
                    retry="元の操作は再発行せず、記録だけを回復する",
                ),
                removable_cause=False,
            ),
        )
    return _dispatch.main(payload_text, initial_notices=notices)


__all__ = ["main"]
