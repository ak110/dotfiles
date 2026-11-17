"""発話だけの要約文書の試験（session_conversation_test）。"""

from __future__ import annotations

import pytest
import session_conversation


def _utterance(role: str, text: str, record: str = "claude:main") -> dict[str, object]:
    return {"kind": "utterance", "role": role, "record": record, "line": 1, "timestamp": "2026-01-01T00:00:00Z", "text": text}


def test_digest_keeps_user_text_and_shortens_older_assistant_text() -> None:
    """ユーザー発話は全文、エージェント発話は直近だけを長く示し、操作・他担当・時刻を含めない。"""
    events = [
        _utterance("user", "最初の  依頼\n2行目"),
        {"kind": "tool-call", "tool": "Read", "record": "claude:main", "line": 2, "text": "{}"},
        _utterance("assistant", "報告" * 400),
        _utterance("assistant", "委譲先の発話", record="claude:child"),
        *[_utterance("assistant", f"途中経過{index}") for index in range(8)],
    ]

    document, meta = session_conversation.digest_document(
        events, main_record="claude:main", detail_command="atk detail", max_bytes=30000
    )

    rows = [line for line in document.splitlines() if line.startswith("- ")]
    assert rows[0] == "- ユーザー: 最初の 依頼 2行目"
    assert rows[1].startswith("- エージェント: 報告") and rows[1].endswith("…") and len(rows[1].encode("utf-8")) < 330
    assert rows[-1] == "- エージェント: 途中経過7"
    assert "Read" not in document and "委譲先の発話" not in document and "2026-01-01" not in document
    assert meta == {"mode": "digest", "user_count": 1, "assistant_count": 9, "omitted_assistant_count": 0, "excerpt_count": 1}


def test_digest_drops_oldest_assistant_text_within_budget() -> None:
    """予算を超える分は古いエージェント発話から省いて件数を示し、ユーザー発話と直近の発話を残す。"""
    events = [_utterance("user", "依頼")]
    events += [_utterance("assistant", f"{index}番目の" + "経過" * 40) for index in range(40)]

    document, meta = session_conversation.digest_document(events, main_record=None, detail_command="atk detail", max_bytes=4000)

    assert len(document.encode("utf-8")) <= 4000
    assert meta["omitted_assistant_count"] > 0
    assert f"古いエージェント発話{meta['omitted_assistant_count']}件を省いた" in document
    assert "- ユーザー: 依頼\n" in document and "- エージェント: 39番目の" in document
    assert "- エージェント: 0番目の" not in document
    with pytest.raises(ValueError, match="--conversation-max-bytes"):
        session_conversation.digest_document(events, main_record=None, detail_command="atk detail", max_bytes=300)
