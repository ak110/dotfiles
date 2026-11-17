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
    assert meta == {
        "mode": "digest",
        "user_count": 1,
        "assistant_count": 9,
        "request_count": 0,
        "omitted_assistant_count": 0,
        "elided_count": 0,
        "excerpt_count": 1,
    }


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


def test_digest_keeps_long_user_text_while_document_fits() -> None:
    """文書全体が予算に収まる間は、長いユーザー発話も末尾の制約まで全文を示す。"""
    long_text = "前提の説明。" * 400 + "最後の制約: 本番へは反映しない。"
    assert len(long_text.encode("utf-8")) > 6000

    document, meta = session_conversation.digest_document(
        [_utterance("user", long_text), _utterance("assistant", "了解")],
        main_record=None,
        detail_command="atk detail",
        max_bytes=30000,
    )

    assert f"- ユーザー: {long_text}\n" in document
    assert meta["elided_count"] == 0 and meta["excerpt_count"] == 0


def test_digest_elides_middle_of_longest_user_text_over_budget() -> None:
    """発話の累積が予算を超える場合は、最長のユーザー発話の中ほどだけを省き、先頭・末尾と省いた字数を示す。"""
    long_text = "冒頭の依頼。" + "経緯の説明。" * 1500 + "最後の制約: 本番へは反映しない。"
    events = [_utterance("user", "短い依頼その1"), _utterance("user", long_text), _utterance("user", "短い依頼その2")]
    events += [_utterance("assistant", f"{index}番目の報告") for index in range(3)]

    document, meta = session_conversation.digest_document(events, main_record=None, detail_command="atk detail", max_bytes=8000)

    assert len(document.encode("utf-8")) <= 8000
    assert "- ユーザー: 短い依頼その1\n" in document and "- ユーザー: 短い依頼その2\n" in document
    elided = next(line for line in document.splitlines() if line.startswith("- ユーザー: 冒頭の依頼。"))
    assert elided.endswith("最後の制約: 本番へは反映しない。")
    assert "字を省いた。原文は冒頭のコマンドで照会する）" in elided
    assert meta["elided_count"] == 1 and meta["excerpt_count"] == 1
    with pytest.raises(ValueError, match="--conversation-max-bytes"):
        session_conversation.digest_document(events, main_record=None, detail_command="atk detail", max_bytes=1200)


@pytest.mark.parametrize("kind", ["utterance", "delegation-request"])
def test_digest_elides_only_when_full_text_exceeds_limit(kind: str) -> None:
    """全文の文書が上限に収まる間はユーザー発話と委譲元の依頼を省かず、1バイト超える場合だけ中ほどを省く。"""
    text = "前提の説明。" * 200 + "最後の制約: 本番へは反映しない。"
    event = {
        "kind": kind,
        "role": "user",
        "record": "claude:main",
        "line": 1,
        "timestamp": "2026-01-01T00:00:00Z",
        "text": text,
    }

    def digest(max_bytes: int) -> tuple[str, dict[str, object]]:
        return session_conversation.digest_document([event], main_record=None, detail_command="atk detail", max_bytes=max_bytes)

    full, _ = digest(30000)
    size = len(full.encode("utf-8"))
    assert full.endswith(f": {text}\n")
    exact, meta = digest(size)
    assert exact == full and meta["elided_count"] == 0
    shorter, meta = digest(size - 1)
    assert len(shorter.encode("utf-8")) <= size - 1
    assert meta["elided_count"] == 1 and shorter.endswith("最後の制約: 本番へは反映しない。\n")


def test_digest_reserves_bytes_only_for_emitted_omission_note() -> None:
    """省いた件数の行は出す場合だけ上限に含め、省かない場合は上限いっぱいまで全行を保つ。"""
    events = [_utterance("user", "依頼")]
    events += [_utterance("assistant", f"{index}番目の" + "経過" * 40) for index in range(12)]

    def digest(max_bytes: int) -> tuple[str, dict[str, object]]:
        return session_conversation.digest_document(events, main_record=None, detail_command="atk detail", max_bytes=max_bytes)

    full, meta = digest(30000)
    size = len(full.encode("utf-8"))
    assert meta["omitted_assistant_count"] == 0 and "- エージェント: 0番目の" in full
    exact, meta = digest(size)
    assert exact == full and meta["omitted_assistant_count"] == 0
    reduced, meta = digest(size - 1)
    assert len(reduced.encode("utf-8")) <= size - 1
    assert meta["omitted_assistant_count"] == 1 and "古いエージェント発話1件を省いた" in reduced
    assert "- ユーザー: 依頼\n" in reduced and "- エージェント: 1番目の" in reduced


def test_digest_keeps_delegation_request_of_delegated_session() -> None:
    """委譲先の会話では、委譲元から受けた依頼を発話と同じ並びへ全文で残す。"""
    request = "実行レビューせよ。計画: /plans/a.md 引き継ぎ記録先: /temp/handoff.md"
    events = [
        {
            "kind": "delegation-request",
            "record": "codex:child",
            "line": 1,
            "timestamp": "2026-01-01T00:00:00Z",
            "text": request,
        },
        _utterance("assistant", "調査を開始する", record="codex:child"),
    ]

    document, meta = session_conversation.digest_document(
        events, main_record=None, detail_command="atk detail", max_bytes=30000
    )

    rows = [line for line in document.splitlines() if line.startswith("- ")]
    assert rows == [f"- 委譲元の依頼: {request}", "- エージェント: 調査を開始する"]
    assert meta["request_count"] == 1 and meta["user_count"] == 0 and meta["assistant_count"] == 1
