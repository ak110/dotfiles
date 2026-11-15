"""PreToolUse統合フックの責務別実装。"""

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._hooks import termination_evidence
lazy from agent_toolkit._hooks.notice import formatter
lazy from agent_toolkit._hooks.pretooluse import dispatch as _dispatch


def main(payload_text: str) -> int:
    """ツール呼び出しの開始を終了工程の証拠へ記録してから、元の判定（`dispatch.main`）をそのまま実行する。"""
    notices: tuple[str, ...] = ()
    try:
        termination_evidence.observe_tool(payload_text, after=False)
    except (OSError, ValueError, TypeError, KeyError) as error:
        notices = (
            formatter("pretooluse")(
                f"ツール呼び出しの開始記録を保存できなかった。元の操作の成否はこの診断では分からない: {error}",
                tag="warn",
                fix="元の操作の結果を確認せよ。記録失敗が再発する場合は、この診断をagent-toolkitの不具合として委譲元またはユーザーへ報告せよ。",
                removable_cause=False,
            ),
        )
    status = _dispatch.main(payload_text, initial_notices=notices)
    termination_evidence.observe_delivered_documents(payload_text)
    return status


__all__ = ["main"]
