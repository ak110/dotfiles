"""証拠抽出の`--record-schema`・`--context-at`照会（`session_evidence_context.py`）を、`session_review_evidence.py`の起動で検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    compaction_entry,
    read_jsonl,
    write_subagent,
)


def test_record_schema_reports_paths_and_types_without_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """複数locatorのobject・arrayを再帰し、値を出力しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "message": {"role": "user", "content": "secret-text"},
                "nested": {"items": [{"flag": True, "count": 42}, {"flag": None}]},
            },
            {"type": "assistant", "message": {"role": "assistant", "content": "second-secret"}},
        ],
    )

    assert evidence.main([str(transcript), "--record-schema", "1", "--record-schema", "main:2"]) == 0

    events = read_jsonl(capsys)
    first = [event for event in events if event["line"] == 1]
    paths = {event["path"]: event["types"] for event in first}
    expected = {
        "$": ["object"],
        "$.nested": ["object"],
        "$.nested.items": ["array"],
        "$.nested.items[]": ["object"],
        "$.nested.items[].count": ["number"],
        "$.nested.items[].flag": ["boolean", "null"],
    }
    assert all(paths[path] == types for path, types in expected.items())
    serialized = json.dumps(events, ensure_ascii=False)
    assert "secret-text" not in serialized
    assert "second-secret" not in serialized
    assert "42" not in serialized


def test_record_schema_reports_codex_paths_and_types_without_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codex形式の元JSONも入れ子と配列を射影し、値を出力しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "codex-secret"},
                        {"type": "input_text", "text": "second-secret"},
                    ],
                    "nested": {"flags": [True, None], "count": 73},
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--record-schema", "1"]) == 0

    events = read_jsonl(capsys)
    paths = {event["path"]: event["types"] for event in events}
    assert paths["$.payload"] == ["object"]
    assert paths["$.payload.content"] == ["array"]
    assert paths["$.payload.content[]"] == ["object"]
    assert paths["$.payload.nested.flags[]"] == ["boolean", "null"]
    assert paths["$.payload.nested.count"] == ["number"]
    serialized = json.dumps(events, ensure_ascii=False)
    assert "codex-secret" not in serialized
    assert "second-secret" not in serialized
    assert "73" not in serialized


def test_record_schema_uses_detail_locator_errors(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--record-schema", "unknown:1"]) == 2

    events = read_jsonl(capsys)
    assert events[0]["kind"] == "error"
    assert "記録が不明" in events[0]["text"]


@pytest.mark.parametrize(
    ("locator", "message"),
    [("9", "行番号9は範囲外"), ("abc", "構造照会位置が不正")],
)
def test_record_schema_rejects_out_of_range_and_invalid_locators(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    locator: str,
    message: str,
) -> None:
    """範囲外の行と不正なlocatorを終了コード2で拒否する。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--record-schema", locator]) == 2

    events = read_jsonl(capsys)
    assert events[0]["kind"] == "error"
    assert message in events[0]["text"]


def test_new_query_modes_are_mutually_exclusive(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--fixed-string", "依頼", "--record-schema", "1"]) == 2

    assert "併用できない" in read_jsonl(capsys)[0]["text"]


def _context_verdicts(capsys: pytest.CaptureFixture[str]) -> dict[str, dict]:
    """`--context-at`の出力をphraseごとの判定と一致の組へまとめる。"""
    events = read_jsonl(capsys)
    verdicts = {event["phrase"]: dict(event, matches=[]) for event in events if event["kind"] == "context-verdict"}
    for event in events:
        if event["kind"] == "context-match":
            verdicts[event["phrase"]]["matches"].append(event)
    return verdicts


def test_context_at_judges_claude_context_across_compaction(tmp_path: pathlib.Path, capsys) -> None:
    """条文が事象の時点でその記録の文脈にあったかを、圧縮境界との前後と文脈へ入る方法から判定する。

    Skill本文は圧縮前なら`present`（`channel`は`meta`）、Readで読んだ参照資料は圧縮で`dropped-by-compaction`、
    ルールファイルは境界後の再注入で`present`となる。起動済みスキル本文の末尾は再注入の切り詰めで
    境界後に現れないため`dropped-by-compaction`、事象行より後にだけある文字列と
    親セッションだけにある文字列は`absent`となる。判定を誤ると、振り返りが原因の区分を取り違える。
    """
    skill_body = "スキル冒頭の条文\n" + "x" * 50 + "\nスキル末尾の条文"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "isMeta": True,
                "timestamp": "2026-09-29T00:00:01Z",
                "message": {"role": "user", "content": [{"type": "text", "text": skill_body}]},
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-29T00:00:02Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "read-1",
                            "name": "Read",
                            "input": {"file_path": "/plugin/references/ref.md"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-29T00:00:03Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "read-1", "content": "参照資料の条文"}],
                },
            },
            {
                "type": "attachment",
                "timestamp": "2026-09-29T00:00:04Z",
                "attachment": {"type": "instructions", "files": [{"path": "/rules/a.md", "content": "ルールの条文"}]},
            },
            {"type": "user", "timestamp": "2026-09-29T00:00:05Z", "message": {"role": "user", "content": "圧縮前の事象"}},
            compaction_entry("2026-09-29T00:00:06Z"),
            {
                "type": "attachment",
                "timestamp": "2026-09-29T00:00:07Z",
                "attachment": {"type": "instructions", "files": [{"path": "/rules/a.md", "content": "ルールの条文"}]},
            },
            {
                "type": "attachment",
                "timestamp": "2026-09-29T00:00:08Z",
                "attachment": {"type": "invoked_skills", "skills": [{"name": "s", "content": skill_body[:30]}]},
            },
            {"type": "user", "timestamp": "2026-09-29T00:00:09Z", "message": {"role": "user", "content": "圧縮後の事象"}},
            {"type": "user", "timestamp": "2026-09-29T00:00:10Z", "message": {"role": "user", "content": "事象より後の言及"}},
        ],
    )
    write_subagent(
        transcript.parent / transcript.stem / "subagents",
        "agent-child",
        [
            {"type": "user", "timestamp": "2026-09-29T00:00:03Z", "message": {"role": "user", "content": "委譲プロンプト"}},
            {"type": "user", "timestamp": "2026-09-29T00:00:04Z", "message": {"role": "user", "content": "委譲先の事象"}},
        ],
    )

    assert evidence.main([str(transcript), "--context-at", "5", "--phrase=スキル冒頭の条文"]) == 0
    before = _context_verdicts(capsys)["スキル冒頭の条文"]
    assert before["verdict"] == "present"
    assert before["boundary_line"] is None
    assert [match["channel"] for match in before["matches"]] == ["meta"]

    phrases = ["参照資料の条文", "ルールの条文", "スキル末尾の条文", "事象より後の言及"]
    assert evidence.main([str(transcript), "--context-at", "main:9", *(f"--phrase={phrase}" for phrase in phrases)]) == 0
    after = _context_verdicts(capsys)
    assert after["参照資料の条文"]["verdict"] == "dropped-by-compaction"
    assert after["参照資料の条文"]["matches"][0]["channel"] == "tool-result"
    assert after["参照資料の条文"]["matches"][0]["tool"] == "Read"
    assert after["参照資料の条文"]["matches"][0]["tool_input"] == "/plugin/references/ref.md"
    assert after["参照資料の条文"]["boundary_line"] == 6
    assert after["ルールの条文"]["verdict"] == "present"
    assert after["ルールの条文"]["last_match_line"] == 7
    assert [(match["line"], match["channel"], match["before_boundary"]) for match in after["ルールの条文"]["matches"]] == [
        (4, "attachment:instructions", True),
        (7, "attachment:instructions", False),
    ]
    assert after["スキル末尾の条文"]["verdict"] == "dropped-by-compaction"
    assert after["事象より後の言及"]["verdict"] == "absent"
    assert after["事象より後の言及"]["match_count"] == 0

    assert evidence.main([str(transcript), "--context-at", "agent-child:2", "--phrase=圧縮前の事象"]) == 0
    assert _context_verdicts(capsys)["圧縮前の事象"]["verdict"] == "absent"


def test_context_at_keeps_codex_replacement_history(tmp_path: pathlib.Path, capsys) -> None:
    """Codexの`compacted`レコードが保持する本文は、圧縮後の事象行で`present`（`channel`は`compaction-retained`）となる。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"timestamp": "2026-09-29T00:00:00Z", "type": "session_meta", "payload": {"id": "thread"}},
            {
                "timestamp": "2026-09-29T00:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "AGENTSの条文"}],
                },
            },
            {
                "timestamp": "2026-09-29T00:00:02Z",
                "type": "compacted",
                "payload": {
                    "message": "",
                    "replacement_history": [
                        {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "AGENTSの条文"}]}
                    ],
                },
            },
            {
                "timestamp": "2026-09-29T00:00:03Z",
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "圧縮後の事象"}]},
            },
        ],
    )

    assert evidence.main([str(transcript), "--context-at", "4", "--phrase=AGENTSの条文"]) == 0
    verdict = _context_verdicts(capsys)["AGENTSの条文"]
    assert verdict["verdict"] == "present"
    assert [(match["channel"], match["before_boundary"]) for match in verdict["matches"]] == [
        ("developer", True),
        ("compaction-retained", False),
    ]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--context-at", "missing:1", "--phrase=x"], "記録が不明"),
        (["--context-at", "main:99", "--phrase=x"], "範囲外"),
        (["--context-at", "1"], "--phrase"),
        (["--context-at", "1", "--phrase="], "--phrase"),
        (["--context-at", "1", "--phrase=x", "--stats"], "併用できない"),
        (["--phrase=x"], "--context-at"),
    ],
)
def test_context_at_rejects_invalid_input(tmp_path: pathlib.Path, capsys, arguments: list[str], message: str) -> None:
    """不正な記録位置、phraseの欠落、他の照会モードとの併用を、エラーイベントと終了コード2で拒否する。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "本文"}}])

    assert evidence.main([str(transcript), *arguments]) == 2
    (event,) = read_jsonl(capsys)
    assert event["kind"] == "error"
    assert message in event["text"]
