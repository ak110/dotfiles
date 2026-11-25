from __future__ import annotations

import re

import pytest
import session_conversation


def _utterance(role: str, text: str, record: str = "claude:main") -> dict[str, object]:
    return {"kind": "utterance", "role": role, "record": record, "line": 1, "timestamp": "2026-01-01T00:00:00Z", "text": text}


def test_digest_keeps_user_text_and_shortens_older_assistant_text() -> None:
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
        "redundant_assistant_count": 0,
        "redundant_status_line_count": 0,
        "user_count": 1,
        "assistant_count": 9,
        "request_count": 0,
        "omitted_assistant_count": 0,
        "elided_count": 0,
        "excerpt_count": 1,
    }


def test_digest_drops_oldest_assistant_text_within_budget() -> None:
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
    with pytest.raises(ValueError, match="--conversation-max-bytes") as raised:
        session_conversation.digest_document(events, main_record=None, detail_command="atk detail", max_bytes=1200)
    # 失敗が示す上限で再実行すると、同じ失敗を繰り返さずに要約を作れる。
    needed = re.search(r"--conversation-max-bytes (\d+)", str(raised.value))
    assert needed is not None and int(needed.group(1)) > 1200
    retried, _ = session_conversation.digest_document(
        events, main_record=None, detail_command="atk detail", max_bytes=int(needed.group(1))
    )
    assert len(retried.encode("utf-8")) <= int(needed.group(1))


@pytest.mark.parametrize("kind", ["utterance", "delegation-request"])
def test_digest_elides_only_when_full_text_exceeds_limit(kind: str) -> None:
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


def test_compact_reports_preserves_delivered_text_locators_and_changed_facts() -> None:
    text = "検証結果: 12件合格"
    repeated = text + "\n" + text
    other_record = _utterance("assistant", text, record="claude:child")
    new_result = _utterance("assistant", "検証結果: 13件合格")
    waiting = _utterance("assistant", "完了を待ちます。")
    user = _utterance("user", text)
    request = {**_utterance("user", text), "kind": "delegation-request"}
    delivered = {**_utterance("assistant", text), "delivery": "send_to_user", "line": 10}
    after_user = {**_utterance("assistant", text), "line": 13}
    after_request = {**_utterance("assistant", text), "line": 15}
    events = [
        _utterance("assistant", repeated),
        other_record,
        waiting,
        delivered,
        new_result,
        user,
        user,
        after_user,
        request,
        after_request,
    ]
    output, counts = session_conversation.compact_assistant_reports(events)
    assert output == [other_record, delivered, new_result, user, user, after_user, request, after_request]
    assert events[0]["text"] == repeated
    assert counts == {"redundant_assistant_count": 2, "redundant_status_line_count": 0}


def test_compaction_keeps_truncated_reports_and_whitespace_in_literal_values() -> None:
    reports = [
        _utterance("assistant", '目的: 値は "a b"'),
        _utterance("assistant", '目的: 値は "a  b"'),
        {**_utterance("assistant", '目的: 値は "a b"'), "text_truncated": True},
    ]
    assert session_conversation.compact_assistant_reports(reports)[0] == reports


def test_compact_status_lines_keeps_changes_and_resets_for_user() -> None:
    events = [
        _utterance("assistant", "進捗: 検証を開始"),
        _utterance("assistant", "進捗: 検証を開始\n進捗: 検証を開始\n検証結果: 12件"),
        _utterance("assistant", "検証結果: 13件"),
        _utterance("user", "進捗: 検証を開始"),
        _utterance("assistant", "進捗: 検証を開始\n```text\n進捗: 検証を開始\n```"),
    ]
    rows, meta = session_conversation.compact_assistant_reports(events)
    assert rows == [events[0], {**events[1], "text": "進捗: 検証を開始\n検証結果: 12件"}, *events[2:]]
    assert meta["redundant_status_line_count"] == 1


def test_compact_reports_preserves_return_to_previous_decision() -> None:
    wait = _utterance("assistant", "状態: 次のCI結果を待つ")
    proceed = _utterance("assistant", "状態: 公開へ進む")
    rows = [wait, proceed, wait, proceed]
    assert session_conversation.compact_assistant_reports(rows)[0] == rows


def test_compaction_keeps_cross_label_reversal_and_partial_delivery() -> None:
    hold = _utterance("assistant", "状態: 統合を保留")
    proceed = _utterance("assistant", "処置: テストが成功したので、統合へ進む。")
    delivery = {**hold, "text": "状態: 統合を保留\n検証結果: 12件", "delivery": "send_to_user"}
    events = [hold, proceed, hold, delivery]
    assert session_conversation.compact_assistant_reports(events)[0] == events


def test_compaction_compares_understanding_fields_not_their_layout() -> None:
    compact = _utterance("assistant", "発話の理解: 確認する。補う前提: A\n別案: B\n分類: 非是正")
    listed = {
        **compact,
        "text": "発話の理解\n分類: 非是正\n- 確認する。\n- 補う前提: A\n- 別案: B",
        "delivery": "send_to_user",
    }
    changed = {**listed, "text": str(listed["text"]).replace("前提: A", "前提: C")}
    kept, meta = session_conversation.compact_assistant_reports([compact, listed, changed])
    assert kept == [listed, changed]
    assert meta["redundant_assistant_count"] == 1
