"""Claude Code 2.1.301で観測したAPI要求の写しと読取不能記録の境界を検証する。"""

import json
import pathlib

import pytest
import session_review_evidence as evidence
import session_review_prepare as prepare

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import read_jsonl


@pytest.mark.parametrize("query", [[], ["--tool-calls"], ["--warn"], ["--stats"], ["--user-events"]])
def test_request_copies_do_not_create_conversation_events(tmp_path: pathlib.Path, capsys, query) -> None:
    """会話と同じmessageを持つ別種のエントリを追加しても抽出結果を増やさない。"""
    entries = [
        {
            "type": "assistant",
            "timestamp": "2026-10-01T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "false"}}],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-10-01T00:00:01Z",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call", "content": "warning: failure", "is_error": True}],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-10-01T00:00:02Z",
            "message": {"role": "user", "content": "<task-notification><status>completed</status></task-notification>"},
        },
    ]
    transcript = _write_transcript(tmp_path, entries)
    assert evidence.main([str(transcript), *query]) == 0
    expected = read_jsonl(capsys, raw=True)
    copies = [
        {"type": kind, "message": entry["message"]} for kind in ("api-request-blob", "future-request-copy") for entry in entries
    ]
    _write_transcript(tmp_path, [*entries, *copies])
    assert evidence.main([str(transcript), *query]) == 0
    assert read_jsonl(capsys, raw=True) == expected
    assert evidence.main([str(transcript), "--detail", "4"]) == 0
    assert "false" in json.dumps(read_jsonl(capsys))
    assert evidence.main([str(transcript), "--grep", "false"]) == 0
    assert any(event.get("line") == 4 for event in read_jsonl(capsys))


def test_prepare_bundle_does_not_read_request_copies_as_conversation(tmp_path: pathlib.Path, capsys) -> None:
    """準備文書とbundleの位置が写しを追加する前と同じ実エントリを指す。"""
    entries = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "false"}}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call", "content": "failure", "is_error": True}],
            },
        },
    ]
    transcript = _write_transcript(tmp_path, entries)
    documents = []
    for index in range(2):
        if index:
            _write_transcript(
                tmp_path, [*entries, *[{"type": "api-request-blob", "message": entry["message"]} for entry in entries]]
            )
        work = tmp_path / f"work-{index}"
        work.mkdir()
        assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)]) == 0
        record = json.loads(capsys.readouterr().out)
        documents.append(pathlib.Path(record["conversation_path"]).read_text(encoding="utf-8"))
        for name in ("timeline", "warnings", "comparison-materials", "conversation"):
            events = [json.loads(line) for line in (work / "bundle" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]
            assert all(event.get("line", 0) <= 2 for event in events)
    assert documents[0] == documents[1]
