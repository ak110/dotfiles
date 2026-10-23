"""Antigravityの対話・委譲先ログを証拠抽出の公開照会で検証する。"""

import argparse
import datetime
import json
from pathlib import Path

import pytest
import session_review_evidence as evidence
from cost_metrics import _scan_sessions

from agent_toolkit._atk import run_script
from agent_toolkit._testing.session_evidence_support import events_by_kind, read_jsonl, write_jsonl


def test_native_user_answers_tools_and_stats(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """質問側と回答側の由来を保ち、同じ原本をuser-events/tool-calls/statsが扱う。"""
    path = tmp_path / "brain/conversation/.system_generated/logs/transcript_full.jsonl"
    write_jsonl(
        path,
        [
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>\n調査して\n</USER_REQUEST>",
                "created_at": "2026-10-22T00:00:00Z",
            },
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "input_tokens": 30,
                "cache_read_tokens": 5,
                "output_tokens": 10,
                "created_at": "2026-10-22T00:00:01Z",
                "tool_calls": [
                    {
                        "name": "ask_question",
                        "args": {"questions": json.dumps([{"question": "反映範囲は？", "options": ["全体", "一部"]}])},
                    }
                ],
            },
            {
                "step_index": 2,
                "source": "MODEL",
                "type": "GENERIC",
                "content": "Created At: 時刻\nCompleted At: 時刻\nA1: 全体",
                "created_at": "2026-10-22T00:00:04Z",
            },
            {
                "step_index": 3,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "了解した",
                "created_at": "2026-10-22T00:00:05Z",
            },
        ],
    )
    assert evidence.main([str(path), "--user-events"]) == 0
    users = events_by_kind(read_jsonl(capsys, raw=True), "user")
    assert [event["text"] for event in users] == ["調査して", "全体"]
    assert all(event["record"] == "agy:conversation" for event in users)
    assert users[1]["assistant_context"][0]["question"] == "反映範囲は？"
    assert users[1]["user_response"] == [{"answers": ["全体"]}]
    assert evidence.main([str(path), "--tool-calls"]) == 0
    calls = events_by_kind(read_jsonl(capsys, raw=True), "tool-call")
    assert len(calls) == 1 and calls[0]["tool"] == "ask_question"
    assert evidence.main([str(path), "--stats"]) == 0
    stats = read_jsonl(capsys, raw=True)
    summary = events_by_kind(stats, "stats-summary")[0]
    assert summary["engine"] == "agy"
    assert summary["tokens"]["input_tokens"] == 30
    assert summary["tokens"]["cache_read_input_tokens"] == 5
    assert events_by_kind(stats, "stats-tool")[0]["count"] == 1


def test_native_delegates_cost_discovery_and_quote_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """native起動のpretty JSONから子会話を集め、原文抽出を引用CLIへ渡す。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    brain = tmp_path / ".gemini/antigravity-cli/brain"
    child = brain / "child/.system_generated/logs/transcript_full.jsonl"
    write_jsonl(
        child,
        [
            {
                "step_index": 0,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-10-22T00:00:01Z",
                "content": "子の報告",
                "input_tokens": 7,
                "output_tokens": 2,
            }
        ],
    )
    parent = brain / "parent/.system_generated/logs/transcript_full.jsonl"
    entries = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "content": "<USER_REQUEST>\n子へ調査を依頼して\n</USER_REQUEST>",
            "created_at": "2026-10-22T00:00:00Z",
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "tool_calls": [{"name": "invoke_subagent", "args": {"Prompt": json.dumps("調査")}}],
        },
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "GENERIC",
            "content": "Created the following subagent:\n"
            + json.dumps({"conversationId": "child", "logAbsoluteUri": child.as_uri()}, indent=2),
        },
    ]
    write_jsonl(parent, entries)
    write_jsonl(parent.with_name("transcript.jsonl"), entries)
    assert evidence.main([str(parent), "--stats"]) == 0
    stats = read_jsonl(capsys, raw=True)
    total = events_by_kind(stats, "stats-total")[0]
    assert total["agent_thread_counts"] == {"agy": 1}
    assert total["tokens"]["input_tokens"] == 7
    roots, _paths, _coverage = _scan_sessions({"agy": brain}, datetime.datetime(2026, 10, 21, tzinfo=datetime.UTC))
    assert [row.item.record_id for row in roots] == ["agy:parent"]

    assert evidence.main([str(parent), "--user-events"]) == 0
    extracted = capsys.readouterr().out
    users = [json.loads(line) for line in extracted.splitlines() if json.loads(line).get("kind") == "user"]
    assert len(users) == 1
    source = tmp_path / "events.jsonl"
    source.write_text(extracted, encoding="utf-8")
    draft = tmp_path / "draft.md"
    draft.write_text("# 題\n\n## ユーザー指摘の逐語引用\n\n旧引用\n", encoding="utf-8")
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps([{"source": str(source), "record": users[0]["record"], "line": users[0]["line"], "mode": "full"}]),
        encoding="utf-8",
    )
    assert (
        run_script.dispatch(
            argparse.Namespace(script_name="wi-quote-check", script_args=[str(draft), "--import-spec", str(selection)])
        )
        == 0
    )
    assert "子へ調査を依頼して" in draft.read_text(encoding="utf-8")


def test_delegated_stream_still_extracts_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """委譲先公開JSONストリームの失敗標識と使用量を引き続き取得する。"""
    path = tmp_path / "delegate.jsonl"
    write_jsonl(
        path,
        [
            {"event": "init", "init": {"conversation_id": "delegate"}},
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "agent_response",
                    "step_index": 1,
                    "usage": {"input_tokens": 10, "output_tokens": 3, "cache_read_tokens": 2},
                },
            },
            {"event": "result", "result": {"status": "ERROR", "error": "失敗", "response": "失敗を報告する"}},
        ],
    )
    assert evidence.main([str(path)]) == 0
    events = read_jsonl(capsys, raw=True)
    assert any(event.get("kind") == "failed-tool" for event in events)
    assert evidence.main([str(path), "--stats"]) == 0
    summary = events_by_kind(read_jsonl(capsys, raw=True), "stats-summary")[0]
    assert summary["tokens"]["output_tokens"] == 3
