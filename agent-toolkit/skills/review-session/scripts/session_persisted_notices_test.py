"""退避案内の生成から証拠照会まで、通知の種類と取得不能を検証する。"""

import pytest
import session_review_evidence as evidence

from agent_toolkit._common import message_format, persisted_output_reference
from agent_toolkit._hooks.pretooluse import warning_context
from agent_toolkit._testing import session_evidence_support as support


@pytest.mark.parametrize("runtime", ["codex", "claude"])
def test_saved_hook_notices_restore_kinds_and_report_missing_files(tmp_path, capsys, runtime):
    """実生成したCodexの案内とClaudeの退避形式を照会し、Preview外の警告も数える。"""
    first = message_format.auto_message("先頭通知 " + "通知の詳細。" * 300, source="pretooluse", kind="warn")
    last = message_format.auto_message("分類対象の後段警告", source="read-delivery", kind="warn")
    body = first + "\n" + last
    if runtime == "codex":
        reference = warning_context.bounded_codex_context(body, session_id="persisted-notice-test")
        path = persisted_output_reference.reference_path(reference)
        assert path is not None and path.read_text(encoding="utf-8") == body
    else:
        # 2026年11月に観測したClaude Codeのhook_additional_context退避形式。
        path = tmp_path / "hook-additionalContext.txt"
        path.write_text(body, encoding="utf-8")
        reference = (
            f"<persisted-output>\nOutput too large (8KB). Full output saved to: {path}\n\n"
            f"Preview (first 2KB):\n{body[:300]}\n</persisted-output>"
        )
    transcript = tmp_path / "notices.jsonl"
    support.write_jsonl(
        transcript,
        [support.hook_attachment({"type": "hook_additional_context", "toolUseID": "notice", "content": [reference]})],
    )
    assert evidence.main([str(transcript), "--hook-notices"]) == 0
    events = support.read_jsonl(capsys)
    kinds = {event["kind_text"]: event for event in events if event["kind"] == "hook-notice"}
    assert kinds["分類対象の後段警告"]["hook"] == "read-delivery"
    assert kinds["分類対象の後段警告"]["count"] == 1
    assert events[-1] == {"kind": "summary", "count": 2}
    assert not any("詳細を保存した" in kind for kind in kinds)
    path.unlink()
    assert evidence.main([str(transcript), "--hook-notices"]) == 0
    missing = support.read_jsonl(capsys)
    assert missing[0]["kind_text"].startswith("通知詳細未展開: ")
    assert missing[-1] == {"kind": "summary", "count": 1, "unexpanded_count": 1}
