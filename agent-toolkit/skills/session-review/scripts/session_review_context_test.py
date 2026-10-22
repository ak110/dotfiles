"""公開された準備と圧縮再開抽出の境界・全担当費用を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_evidence_user_events as user_events
import session_review_prepare as prepare

from agent_toolkit._testing import session_evidence_support as support


@pytest.mark.parametrize("compacted", [False, True])
def test_prepare_keeps_whole_flow_and_delegate_statistics(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], *, compacted: bool
) -> None:
    """圧縮の有無によらず全体像を1文書へまとめ、子の費用を統計へ保持する。"""
    parent = tmp_path / "parent.jsonl"
    entries = [support.timestamped_entry("2026-10-10T00:00:00Z", "圧縮前の要求")]
    if compacted:
        # Claudeの圧縮境界の形はsession-records.mdの記録契約を使う。
        entries.append({"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-10T00:01:00Z"})
    entries.append(support.timestamped_entry("2026-10-10T00:02:00Z", "現在覚えている要求"))
    support.write_jsonl(parent, entries)
    support.write_subagent(
        parent.with_suffix("") / "subagents",
        "agent-child",
        [
            support.claude_call(
                "2026-10-10T00:00:01Z", "read-1", "Read", {"file_path": "/repo/rules.md", "offset": 1, "limit": 8}
            ),
            {
                "type": "user",
                "timestamp": "2026-10-10T00:00:02Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "read-1", "content": "結果" * 1000}],
                },
            },
            support.claude_call("2026-10-10T00:00:03Z", "skill", "Skill", {"skill": "agent-toolkit:bugfix"}),
        ],
    )
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(parent), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    document = pathlib.Path(result["conversation_path"]).read_text(encoding="utf-8")
    assert "圧縮前の要求" in document and "現在覚えている要求" in document
    assert "agent-child" in document
    assert result["conversation_utf8_bytes"] == len(document.encode("utf-8"))
    assert "reader_ranges" not in result and "conversation_ranges" not in result and "main_known_scope" not in result
    stats = pathlib.Path(result["stats_path"]).read_text(encoding="utf-8")
    assert "/repo/rules.md" in stats and "agent-toolkit:bugfix" in stats
    assert "2000字・6000バイト" in stats
    assert "agent-child:2" in stats
    context = user_events.extract_session_context(parent)
    assert context["last_compaction_line"] == (2 if compacted else 0)
    assert [event["text"] for event in context["user_events"]] == ["圧縮前の要求", "現在覚えている要求"]
    assert [event["text"] for event in context["conversation_events"]] == (
        ["現在覚えている要求"] if compacted else ["圧縮前の要求", "現在覚えている要求"]
    )
    handoff_work = tmp_path / "handoff"
    handoff_work.mkdir()
    assert prepare.main(["--transcript", str(parent), "--work-dir", str(handoff_work), "--conversation-only"]) == 0
    handoff = json.loads(capsys.readouterr().out)
    assert pathlib.Path(handoff["conversation_path"]).read_text(encoding="utf-8") == document
    assert handoff["conversation_utf8_bytes"] == result["conversation_utf8_bytes"]
    # 引継ぎでも統計の母集団は同じ親子の全記録である。
    assert (handoff_work / "bundle" / "stats.jsonl").read_bytes() == (work / "bundle" / "stats.jsonl").read_bytes()


def test_resume_context_rejects_unreadable_transcript(tmp_path: pathlib.Path) -> None:
    """読取不能は欠けた会話との区別が付くエラーになる。"""
    with pytest.raises(ValueError, match="セッション記録を読み込めない"):
        user_events.extract_session_context(tmp_path / "missing.jsonl")


def test_codex_resume_context_uses_last_compaction_boundary(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Codexの複数圧縮後も元発話を保ち、最後の境界で残る会話を選ぶ。"""
    path = tmp_path / "codex.jsonl"
    # response_itemとcompactedはCodex記録を扱う既存のuser-events/compaction試験と同じ形である。
    support.write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"id": "context-test"}},
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "最初の要求"}]},
            },
            {"type": "compacted", "payload": {"compaction_response_id": "first"}},
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "次の要求"}]},
            },
            {"type": "compacted", "payload": {"compaction_response_id": "last"}},
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "exec_command", "call_id": "call", "arguments": '{"cmd":"pwd"}'},
            },
        ],
    )
    context = user_events.extract_session_context(path)
    assert context["last_compaction_line"] == 5
    assert [event["text"] for event in context["user_events"]] == ["最初の要求", "次の要求"]
    assert [(event["kind"], event["line"]) for event in context["conversation_events"]] == [("tool-call", 6)]
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(path), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["compaction_count"] == 2
    document = pathlib.Path(result["conversation_path"]).read_text(encoding="utf-8")
    assert "最初の要求" in document and "次の要求" in document
    assert f"{context['record']}:6" in document
    assert result["conversation_utf8_bytes"] <= result["conversation_limit_bytes"]
