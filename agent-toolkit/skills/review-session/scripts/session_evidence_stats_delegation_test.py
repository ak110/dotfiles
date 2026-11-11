"""セッション振り返りの試験（session_evidence_stats_delegation_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_stats_support import _codex_token_count_entry, _write_rollout
from agent_toolkit._testing.session_evidence_support import (
    codex_tool_result_entry,
    codex_tool_use_entry,
    events_by_kind,
    read_jsonl,
)


@pytest.mark.parametrize("tool", ["start", "start_explore", "start_shell"])
def test_collect_reports_unresolved_delegation(
    tool: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """agents_server起動の出力から識別子を得られない場合は未確認範囲を返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": f"mcp__agents_server__{tool}",
                    "call_id": "missing",
                    "arguments": {"engine": "codex"},
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output", "call_id": "missing", "output": {"status": "done"}},
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    (unresolved,) = [dict(event) for event in events_by_kind(read_jsonl(capsys, raw=True), "unresolved-delegation")]
    next_action = unresolved.pop("next_action")
    assert "codex:transcript:2" in next_action and "委譲先の識別子" in next_action
    assert unresolved == {"kind": "unresolved-delegation", "record": "codex:transcript", "line": 2}


def test_collect_reports_unresolved_event_msg_delegation(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Codexのitem_completedから識別子を得られない場合は未確認範囲を返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "McpToolCall",
                        "server": "agents_server",
                        "tool": "start",
                        "arguments": {"engine": "codex"},
                        "result": {"status": "done"},
                    },
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    (unresolved,) = [dict(event) for event in events_by_kind(read_jsonl(capsys, raw=True), "unresolved-delegation")]
    next_action = unresolved.pop("next_action")
    assert "codex:transcript:1" in next_action and "委譲先の識別子" in next_action
    assert unresolved == {"kind": "unresolved-delegation", "record": "codex:transcript", "line": 1}


def test_collect_ignores_existing_session_operations_as_delegation_sources(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """wait等の既存session操作と外側実行セルの文字列は委譲発見元にしない。"""
    missing_session_id = "99999999-9999-4999-8999-999999999999"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "functions.exec",
                    "call_id": "outer-exec",
                    "arguments": (
                        'await tools.mcp__agents_server__wait({"session_id":"known"}); '
                        'await tools.mcp__agents_server__start_shell({"command":"true"});'
                    ),
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output", "call_id": "outer-exec", "output": {"status": "done"}},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "mcp__agents_server__wait",
                    "call_id": "missing-wait",
                    "arguments": {"session_id": missing_session_id},
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "missing-wait",
                    "output": {"status": "failed"},
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "server": "agents_server",
                        "tool": "mcp__agents_server__list",
                        "arguments": {},
                        "result": {"structuredContent": {"sessions": [{"session_id": missing_session_id}]}},
                    },
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__plugin_agent-toolkit_agents_server__wait",
                            "id": "claude-wait",
                            "input": {"session_id": missing_session_id},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "mcpMeta": {"structuredContent": {"engine": "codex", "threadId": missing_session_id}},
                "toolUseResult": {"engine": "codex", "session_id": missing_session_id},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "claude-wait", "content": "完了"}],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__plugin_agent-toolkit_agents_server__list",
                            "id": "claude-list",
                            "input": {},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "mcpMeta": {"structuredContent": {"engine": "codex", "threadId": missing_session_id}},
                "toolUseResult": {"engine": "codex", "session_id": missing_session_id},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "claude-list", "content": "完了"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys, raw=True)
    assert not events_by_kind(events, "stats-agent-thread")
    assert not events_by_kind(events, "unresolved-delegation")
    assert not events_by_kind(events, "unresolved-record")


def test_stats_does_not_discover_session_from_claude_plugin_kill_tool_call(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Claude transcriptの`kill`入力は新しい委譲先の発見元にしない。"""
    session_id = "claude-session-kill-11111111"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__plugin_agent-toolkit_agents_server__kill",
                            "id": "call-claude-kill",
                            "input": {"engine": "claude", "session_id": session_id},
                        }
                    ],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    assert not events_by_kind(events, "stats-agent-thread")
    assert not events_by_kind(events, "unresolved-delegation")
    assert not events_by_kind(events, "unresolved-record")


def test_stats_recursively_discovers_native_subagent_activity_without_cycles(
    tmp_path: pathlib.Path,
    monkeypatch,
    capsys,
) -> None:
    """native `SubAgentActivity`の子孫を重複なく再帰集計し、循環参照で停止しない。"""
    root_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    child_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    grandchild_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    codex_home = tmp_path / "codex"
    rollout_dir = codex_home / "sessions" / "2026" / "08" / "19"
    rollout_dir.mkdir(parents=True)

    def write_rollout(thread_id: str, entries: list[dict]) -> None:
        (rollout_dir / f"rollout-test-{thread_id}.jsonl").write_text(
            "\n".join(json.dumps(entry) for entry in entries) + "\n",
            encoding="utf-8",
        )

    def activity(thread_id: str) -> dict:
        return {"type": "SubAgentActivity", "agent_thread_id": thread_id}

    write_rollout(
        root_id,
        [
            _codex_token_count_entry("2026-08-19T00:00:01Z", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
            {"timestamp": "2026-08-19T00:00:02Z", "payload": {"type": "message", "activity": activity(child_id)}},
            {"timestamp": "2026-08-19T00:00:03Z", "payload": {"type": "message", "activity": activity(child_id)}},
        ],
    )
    write_rollout(
        child_id,
        [
            _codex_token_count_entry("2026-08-19T00:00:04Z", {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4}),
            {"timestamp": "2026-08-19T00:00:05Z", "payload": {"type": "message", "activity": activity(grandchild_id)}},
            {"timestamp": "2026-08-19T00:00:06Z", "payload": {"type": "message", "activity": activity(root_id)}},
        ],
    )
    write_rollout(
        grandchild_id,
        [_codex_token_count_entry("2026-08-19T00:00:07Z", {"input_tokens": 3, "output_tokens": 3, "total_tokens": 6})],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {"type": "message", "role": "assistant", "activity": activity(root_id)},
            }
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    threads = events_by_kind(events, "stats-agent-thread")
    assert [event["thread"] for event in threads] == [grandchild_id, child_id, root_id]
    assert [event["tokens"]["total_tokens"] for event in threads] == [6, 4, 2]
    assert events_by_kind(events, "stats-total")[0]["tokens"] == {
        "input_tokens": 6,
        "output_tokens": 6,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


def test_stats_resolves_runtime_unspecified_thread_once(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実行系未指定の識別子を実体から解決し、実行系付きの再出現と重複させない。"""
    thread_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    codex_home = tmp_path / "codex"
    _write_rollout(
        codex_home,
        thread_id,
        [("2026-08-19T00:00:01Z", {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__agents_server__start",
                            "id": "unspecified",
                            "input": {"session_id": thread_id},
                        }
                    ],
                },
            },
            codex_tool_result_entry("2026-08-19T00:00:01Z", "unspecified", thread_id, engine=None),
            codex_tool_use_entry("2026-08-19T00:00:02Z", "specified", thread_id),
            codex_tool_result_entry("2026-08-19T00:00:02Z", "specified", thread_id),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    assert not events_by_kind(events, "unresolved-record")
    assert [event["session_id"] for event in events_by_kind(events, "stats-agent-thread")] == [thread_id]
