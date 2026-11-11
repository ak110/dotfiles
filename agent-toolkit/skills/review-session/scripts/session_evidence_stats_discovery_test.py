"""セッション振り返りの試験（session_evidence_stats_discovery_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_stats_support import _write_rollout
from agent_toolkit._testing.session_evidence_support import (
    assistant_usage_entry,
    codex_tool_result_entry,
    codex_tool_use_entry,
    events_by_kind,
    read_jsonl,
    usage_record,
)


def test_stats_collects_all_subagents_without_exclusion(tmp_path: pathlib.Path, capsys) -> None:
    """全てのサブエージェント記録を主体別集計へ含め、種別による除外を行わない。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}}],
    )
    subagents = transcript.with_suffix("") / "subagents"
    subagents.mkdir(parents=True)
    normal = subagents / "agent-normal.jsonl"
    normal.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {"role": "assistant", "id": "n", "usage": {"input_tokens": 2, "output_tokens": 3}},
            }
        )
        + "\n"
    )
    (subagents / "agent-normal.meta.json").write_text(json.dumps({"agentType": "Explore"}))
    other = subagents / "agent-other.jsonl"
    other.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {"role": "assistant", "id": "o", "usage": {"input_tokens": 100, "output_tokens": 100}},
            }
        )
        + "\n"
    )
    (subagents / "agent-other.meta.json").write_text(json.dumps({"agentType": "general-purpose"}))

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    assert sorted(event["agent"] for event in events_by_kind(events, "stats-subagent")) == [
        "claude:transcript/agent-normal",
        "claude:transcript/agent-other",
    ]
    total = events_by_kind(events, "stats-subagent-total")[0]
    assert total["count"] == 2
    assert "excluded_review_agents" not in total
    assert total["tokens"]["input_tokens"] == 102


def test_stats_omits_subagent_events_without_subagents_directory(tmp_path: pathlib.Path, capsys) -> None:
    """`subagents/`が無い場合は主体別集計のイベントを出力しない。"""
    transcript = _write_transcript(
        tmp_path,
        [assistant_usage_entry("2026-08-19T00:00:00Z", "main", usage_record(2, 3))],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    assert events_by_kind(events, "stats-subagent") == []
    assert events_by_kind(events, "stats-subagent-total") == []
    assert events_by_kind(events, "stats-total")[0]["subagent_count"] == 0


def test_stats_discovers_codex_threads_from_structured_shapes(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """起動ツールの構造化終了時の結果からthreadIdを収集し、引用と既存session操作は除外する。

    `mcpMeta.structuredContent`・JSON文字列型`toolUseResult`は起動`tool_result`へ対応付け、
    同一threadIdへ重複排除する。タスク通知の`<result>`要素だけで到達するthreadIdも収集する。
    引用UUIDにも対応するrolloutを配置するため、誤って収集した場合はそのスレッドの
    `stats-agent-thread`が出力され、本テストが失敗する。
    """
    thread_id = "11111111-1111-4111-8111-111111111111"
    notified_id = "33333333-3333-4333-8333-333333333333"
    quoted_id = "22222222-2222-4222-8222-222222222222"
    missing_id = "44444444-4444-4444-8444-444444444444"
    sent_id = "55555555-5555-4555-8555-555555555555"
    codex_home = tmp_path / "codex"
    rollout_dir = codex_home / "sessions" / "2026" / "08" / "19"
    rollout_dir.mkdir(parents=True)
    rollout = rollout_dir / f"rollout-test-{thread_id}.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
                        "last_token_usage": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
                    },
                },
            }
        )
        + "\n"
    )
    _write_rollout(
        codex_home, quoted_id, [("2026-08-19T00:00:00Z", {"input_tokens": 6, "output_tokens": 7, "total_tokens": 13})]
    )
    _write_rollout(
        codex_home, notified_id, [("2026-08-19T00:00:00Z", {"input_tokens": 8, "output_tokens": 9, "total_tokens": 17})]
    )
    notification = (
        "<task-notification>\n"
        "<source>codex/codex</source>\n"
        "<status>completed</status>\n"
        f"<result>{json.dumps({'engine': 'codex', 'threadId': notified_id})}</result>\n"
        "</task-notification>"
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:00Z",
                "message": {"role": "user", "content": "引用本文にはthreadId: " + quoted_id},
            },
            codex_tool_use_entry("2026-08-19T00:00:01Z", "call-missing", missing_id),
            codex_tool_use_entry(
                "2026-08-19T00:00:01Z",
                "call-send",
                sent_id,
                tool_name="mcp__agents_server__kill",
            ),
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__agents_server__start",
                            "id": "a",
                            "input": {"engine": "codex"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:02Z",
                "mcpMeta": {"structuredContent": {"engine": "codex", "threadId": thread_id}},
                "toolUseResult": json.dumps({"engine": "codex", "conversationId": thread_id}),
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "a", "content": "完了"}],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:03Z",
                "message": {"role": "user", "content": [{"type": "text", "text": notification}]},
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    threads = events_by_kind(events, "stats-agent-thread")
    assert [event["thread"] for event in threads] == [notified_id, thread_id]
    assert [event["tokens"]["total_tokens"] for event in threads] == [17, 9]
    assert quoted_id not in {event["thread"] for event in threads}
    assert missing_id not in {event["thread"] for event in threads}
    assert sent_id not in {event["thread"] for event in threads}


def test_stats_resolves_claude_session_from_codex_rollout_tool_result(
    tmp_path: pathlib.Path,
    monkeypatch,
    capsys,
) -> None:
    """Codex rolloutのcustom tool call終了時の結果からClaude sessionを解決し、エンジン別に集計する。"""
    session_id = "claude-session-11111111"
    claude_home = tmp_path / "home"
    claude_transcript = claude_home / ".claude" / "projects" / "repo" / f"{session_id}.jsonl"
    claude_transcript.parent.mkdir(parents=True)
    claude_transcript.write_text(
        json.dumps(assistant_usage_entry("2026-08-19T00:00:02Z", "claude-message", usage_record(4, 5))) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(claude_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "mcp__agents_server__start",
                    "call_id": "call-claude",
                    "arguments": json.dumps({"engine": "claude"}),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:01Z",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call-claude",
                    "output": {"engine": "claude", "session_id": session_id},
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    thread = events_by_kind(events, "stats-agent-thread")[0]
    assert thread["engine"] == "claude"
    assert thread["session_id"] == session_id
    assert thread["tokens"] == usage_record(4, 5)
    assert events_by_kind(events, "stats-total")[0]["agent_thread_counts"] == {"claude": 1}


def test_collect_includes_start_custom_and_start_write_delegates(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """統合前の記録の`start_custom`と`start_write`で起動した委譲先も収集し、記録の無い委譲先は`unresolved-record`にする。"""
    custom_session = "claude-session-custom-1111"
    write_session = "agy-session-write-2222"
    home = tmp_path / "home"
    custom_transcript = home / ".claude" / "projects" / "repo" / f"{custom_session}.jsonl"
    custom_transcript.parent.mkdir(parents=True)
    delegate_request = {
        "type": "user",
        "timestamp": "2026-08-19T00:00:01Z",
        "message": {"role": "user", "content": "委譲先への依頼"},
    }
    custom_transcript.write_text(
        json.dumps(delegate_request)
        + "\n"
        + json.dumps(assistant_usage_entry("2026-08-19T00:00:02Z", "custom-message", usage_record(4, 5)))
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    transcript = _write_transcript(
        tmp_path,
        [
            codex_tool_use_entry(
                "2026-08-19T00:00:00Z",
                "call-custom",
                custom_session,
                tool_name="mcp__plugin_agent-toolkit_agents_server__start_custom",
            ),
            codex_tool_result_entry("2026-08-19T00:00:01Z", "call-custom", custom_session, engine=None),
            codex_tool_use_entry(
                "2026-08-19T00:00:03Z",
                "call-write",
                write_session,
                tool_name="mcp__plugin_agent-toolkit_agents_server__start_write",
            ),
            codex_tool_result_entry("2026-08-19T00:00:04Z", "call-write", write_session, engine=None),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    stats_events = read_jsonl(capsys, raw=True)
    assert [event["session_id"] for event in events_by_kind(stats_events, "stats-agent-thread")] == [custom_session]
    (unresolved,) = [dict(event) for event in events_by_kind(stats_events, "unresolved-record")]
    next_action = unresolved.pop("next_action")
    assert write_session in next_action and "記録保持者" in next_action
    assert unresolved == {"kind": "unresolved-record", "record": write_session, "line": 4}

    assert evidence.main([str(transcript)]) == 0
    default_events = read_jsonl(capsys, raw=True)
    assert f"claude:{custom_session}" in {event.get("record") for event in default_events}
    (unresolved,) = [dict(event) for event in events_by_kind(default_events, "unresolved-record")]
    next_action = unresolved.pop("next_action")
    assert write_session in next_action and "記録保持者" in next_action
    assert unresolved == {"kind": "unresolved-record", "record": write_session, "line": 4}


def test_collect_resolves_codex_agents_server_delegations(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexの3つの完了形状から`structuredContent`直下の識別子を解決する。"""
    codex_home = tmp_path / "codex"
    thread_ids = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]
    for index, thread_id in enumerate(thread_ids, start=1):
        _write_rollout(
            codex_home,
            thread_id,
            [(f"2026-08-19T00:00:0{index}Z", {"input_tokens": index, "total_tokens": index})],
        )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "McpToolCall",
                        "server": "agents_server",
                        "tool": "start",
                        "arguments": {"engine": "codex"},
                        "result": json.dumps({"structuredContent": {"session_id": thread_ids[0]}}),
                    },
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:01Z",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "mcp__agents_server__start_explore",
                    "call_id": "custom",
                    "arguments": {"engine": "codex"},
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:02Z",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "custom",
                    "output": {"structuredContent": {"session_id": thread_ids[1]}},
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:03Z",
                "payload": {
                    "type": "function_call",
                    "name": "mcp__agents_server__start_shell",
                    "call_id": "function",
                    "arguments": {"engine": "codex"},
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:04Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "function",
                    "output": {"structuredContent": {"session_id": thread_ids[2]}},
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    assert {event["session_id"] for event in events_by_kind(events, "stats-agent-thread")} == set(thread_ids)
    assert not events_by_kind(events, "unresolved-delegation")
