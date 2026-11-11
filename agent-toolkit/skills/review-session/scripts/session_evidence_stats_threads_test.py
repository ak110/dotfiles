"""セッション振り返りの試験（session_evidence_stats_threads_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing import delegated_threads
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_stats_support import _write_rollout
from agent_toolkit._testing.session_evidence_support import (
    assistant_usage_entry,
    codex_tool_result_entry,
    codex_tool_use_entry,
    events_by_kind,
    read_jsonl,
    usage_record,
    write_subagent,
)


def test_stats_reports_breakdown_of_each_agent_thread(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """各agent threadの記録へメイン記録と同じ内訳の集計を適用し、threadの識別子と記録位置を付けて返す。

    内訳が無いと、律速区間となった委譲先の内部の工程を振り返りが追加の照会なしで読めない。
    委譲先の呼び出しがメイン記録の`stats-tool`の件数へ加わると、メインの工程の所要時間を誤って読む。
    記録位置は`--detail`へそのまま渡せる`<記録ID>:<行番号>`である必要がある。
    """
    threads = delegated_threads.write_delegated_threads(tmp_path)
    monkeypatch.setenv("HOME", str(threads.home))
    monkeypatch.setenv("CODEX_HOME", str(threads.codex_home))
    claude_id = delegated_threads.CLAUDE_THREAD_ID
    codex_id = delegated_threads.CODEX_THREAD_ID

    assert evidence.main([str(threads.transcript), "--stats"]) == 0
    events = read_jsonl(capsys, raw=True)

    assert {event["tool"]: event["count"] for event in events_by_kind(events, "stats-tool")} == {
        "Bash": 1,
        "mcp__agents_server__start": 2,
    }
    assert events_by_kind(events, "stats-critical-path")[0]["segments"] == [
        {"owner": claude_id, "exclusive_seconds": 175.0},
        {"owner": codex_id, "exclusive_seconds": 59.0},
    ]
    claude_record = f"claude:{claude_id}"
    codex_record = f"codex:{codex_id}"
    assert events_by_kind(events, "stats-thread-tool") == [
        {"kind": "stats-thread-tool", "tool": "Bash", "count": 2, "total_seconds": 40.0, "thread": claude_id},
        {"kind": "stats-thread-tool", "tool": "Read", "count": 1, "total_seconds": 1.0, "thread": claude_id},
        {"kind": "stats-thread-tool", "tool": "exec_command", "count": 1, "total_seconds": 20.0, "thread": codex_id},
    ]
    assert events_by_kind(events, "stats-thread-gap") == [
        {
            "kind": "stats-thread-gap",
            "seconds": 80.0,
            "thread": claude_id,
            "before": f"{claude_record}:3",
            "after": f"{claude_record}:4",
        }
    ]
    assert events_by_kind(events, "stats-thread-repeat") == [
        {
            "kind": "stats-thread-repeat",
            "tool": "Bash",
            "hint": "pytest",
            "count": 2,
            "thread": claude_id,
            "locations": [f"{claude_record}:2", f"{claude_record}:4"],
        }
    ]
    assert [
        (event["thread"], event["tool"], event["seconds"], event["location"], event["hint"])
        for event in events_by_kind(events, "stats-thread-slow-call")
    ] == [
        (claude_id, "Bash", 30.0, f"{claude_record}:2", "pytest"),
        (claude_id, "Bash", 10.0, f"{claude_record}:4", "pytest"),
        (claude_id, "Read", 1.0, f"{claude_record}:6", "/repo/a.md"),
        (codex_id, "exec_command", 20.0, f"{codex_record}:3", "make build"),
    ]

    # 記録位置を`--detail`へ渡すと、その呼び出しの記録が返る。
    assert evidence.main([str(threads.transcript), "--detail", f"{codex_record}:3"]) == 0
    detail = read_jsonl(capsys, raw=True)
    assert [(event["record"], event["line"]) for event in detail] == [(codex_record, 3)]
    assert detail[0]["input"] == {"cmd": "make build"}


def test_stats_separates_turn_completion_from_trailing_records(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """委譲先のturn完了までの秒数を、完了後に続く記録の区間と区別して出力する。

    記録の最初と最後の差だけを所要時間とすると、turn完了後に残るプロセスの記録が委譲先の所要時間へ入り、
    律速区間の判断を誤る。Codexは`task_started`から最後の`task_complete`まで、Claude Codeのサブエージェントは
    記録の最初から最後の`end_turn`までをturnの区間とし、完了を持たない記録には区間の項目を出力しない。
    """
    thread_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    codex_home = tmp_path / "codex"
    rollout_dir = codex_home / "sessions" / "2026" / "08" / "19"
    rollout_dir.mkdir(parents=True)
    rollout_entries = [
        {"timestamp": "2026-08-19T00:00:00Z", "type": "session_meta", "payload": {"id": thread_id}},
        {"timestamp": "2026-08-19T00:00:10Z", "type": "event_msg", "payload": {"type": "task_started"}},
        {"timestamp": "2026-08-19T00:05:10Z", "type": "event_msg", "payload": {"type": "task_complete"}},
        {"timestamp": "2026-08-19T00:06:00Z", "type": "event_msg", "payload": {"type": "task_started"}},
        {"timestamp": "2026-08-19T00:10:10Z", "type": "event_msg", "payload": {"type": "task_complete"}},
        {"timestamp": "2026-08-19T02:10:10Z", "type": "event_msg", "payload": {"type": "exec_command_end"}},
    ]
    (rollout_dir / f"rollout-test-{thread_id}.jsonl").write_text(
        "\n".join(json.dumps(entry) for entry in rollout_entries) + "\n", encoding="utf-8"
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            codex_tool_use_entry("2026-08-19T00:00:01Z", "call", thread_id),
            codex_tool_result_entry("2026-08-19T00:00:02Z", "call", thread_id),
        ],
    )
    subagents = transcript.parent / transcript.stem / "subagents"
    completed = assistant_usage_entry("2026-08-19T00:01:30Z", "m1", usage_record(1))
    completed["message"]["stop_reason"] = "end_turn"
    write_subagent(
        subagents,
        "agent-completed",
        [assistant_usage_entry("2026-08-19T00:00:30Z", "m0", usage_record(1)), completed],
    )
    write_subagent(subagents, "agent-unfinished", [assistant_usage_entry("2026-08-19T00:00:30Z", "m2", usage_record(1))])

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    thread = events_by_kind(events, "stats-agent-thread")[0]
    assert thread["elapsed_seconds"] == 2 * 3600 + 10 * 60 + 10
    assert thread["turn_elapsed_seconds"] == 10 * 60
    assert thread["last_turn_completed_at"] == "2026-08-19T00:10:10Z"
    assert thread["after_last_turn_seconds"] == 2 * 3600
    rows = {row["agent"]: row for row in events_by_kind(events, "stats-subagent")}
    completed_row = next(row for agent, row in rows.items() if agent.endswith("agent-completed"))
    assert completed_row["turn_elapsed_seconds"] == 60
    assert completed_row["last_turn_completed_at"] == "2026-08-19T00:01:30Z"
    assert completed_row["after_last_turn_seconds"] == 0
    unfinished_row = next(row for agent, row in rows.items() if agent.endswith("agent-unfinished"))
    assert "turn_elapsed_seconds" not in unfinished_row
    assert "after_last_turn_seconds" not in unfinished_row


def test_stats_outputs_every_subagent_without_limit(tmp_path: pathlib.Path, capsys) -> None:
    """21件以上のサブエージェント記録を件数制限なく全成分合計降順で出力する。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}}],
    )
    subagents = transcript.with_suffix("") / "subagents"
    for index in range(21):
        write_subagent(
            subagents,
            f"agent-{index:02d}",
            [assistant_usage_entry("2026-08-19T00:00:01Z", f"message-{index}", usage_record(index + 1))],
        )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    rows = events_by_kind(events, "stats-subagent")
    assert [row["agent"] for row in rows] == [f"claude:transcript/agent-{index:02d}" for index in range(20, -1, -1)]
    total = events_by_kind(events, "stats-subagent-total")[0]
    assert total["count"] == 21
    assert total["tokens"] == usage_record(sum(range(1, 22)))


def test_stats_outputs_every_codex_thread_without_limit(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """21件以上のCodexスレッドを件数制限なく`total_tokens`降順で出力する。"""
    codex_home = tmp_path / "codex"
    thread_ids = [f"{index:08d}-0000-4000-8000-000000000000" for index in range(21)]
    entries: list[dict] = [
        {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}}
    ]
    for index, thread_id in enumerate(thread_ids):
        _write_rollout(
            codex_home,
            thread_id,
            [("2026-08-19T00:00:01Z", {"input_tokens": index + 1, "output_tokens": 1, "total_tokens": index + 2})],
        )
        entries.append(codex_tool_use_entry("2026-08-19T00:00:01Z", f"call-{index}", thread_id))
        entries.append(codex_tool_result_entry("2026-08-19T00:00:01Z", f"call-{index}", thread_id))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    threads = events_by_kind(events, "stats-agent-thread")
    assert [event["thread"] for event in threads] == list(reversed(thread_ids))
    assert [event["tokens"]["total_tokens"] for event in threads] == list(range(22, 1, -1))


def test_stats_collects_thread_ids_from_every_subagent(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """全てのサブエージェント発の委譲を`agent`キー付きで出力し、種別による除外を行わない。"""
    normal_thread = "55555555-5555-4555-8555-555555555555"
    other_thread = "66666666-6666-4666-8666-666666666666"
    codex_home = tmp_path / "codex"
    _write_rollout(
        codex_home,
        normal_thread,
        [("2026-08-19T00:00:02Z", {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})],
    )
    _write_rollout(
        codex_home,
        other_thread,
        [("2026-08-19T00:00:02Z", {"input_tokens": 300, "output_tokens": 400, "total_tokens": 700})],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}}],
    )
    subagents = transcript.with_suffix("") / "subagents"
    write_subagent(
        subagents,
        "agent-normal",
        [
            codex_tool_use_entry("2026-08-19T00:00:01Z", "call-normal", normal_thread),
            codex_tool_result_entry("2026-08-19T00:00:01Z", "call-normal", normal_thread),
        ],
        {"agentType": "Explore"},
    )
    write_subagent(
        subagents,
        "agent-other",
        [
            codex_tool_use_entry("2026-08-19T00:00:01Z", "call-other", other_thread),
            codex_tool_result_entry("2026-08-19T00:00:01Z", "call-other", other_thread),
        ],
        {"agentType": "general-purpose"},
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    threads = events_by_kind(events, "stats-agent-thread")
    assert sorted(event["thread"] for event in threads) == sorted([normal_thread, other_thread])
    assert {event["thread"]: event["agent"] for event in threads} == {
        normal_thread: "claude:transcript/agent-normal",
        other_thread: "claude:transcript/agent-other",
    }


def test_stats_thread_line_only_for_main_transcript_threads(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """`line`はメイン記録発の委譲だけに付け、サブエージェント記録発の委譲には付けない。

    `line`は`--detail`が解決するメインtranscriptの行番号であり、サブエージェント記録の行番号を
    同じキーで出力すると`--detail`が無関係なエントリを返すため。
    """
    main_thread = "88888888-8888-4888-8888-888888888888"
    sub_thread = "99999999-9999-4999-8999-999999999999"
    codex_home = tmp_path / "codex"
    _write_rollout(
        codex_home, main_thread, [("2026-08-19T00:00:02Z", {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20})]
    )
    _write_rollout(
        codex_home, sub_thread, [("2026-08-19T00:00:02Z", {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})]
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            codex_tool_use_entry("2026-08-19T00:00:01Z", "call-main", main_thread),
            codex_tool_result_entry("2026-08-19T00:00:01Z", "call-main", main_thread),
        ],
    )
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-sub",
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:01Z", "message": {"role": "user", "content": "委譲"}},
            {"type": "user", "timestamp": "2026-08-19T00:00:01Z", "message": {"role": "user", "content": "追記"}},
            codex_tool_use_entry("2026-08-19T00:00:01Z", "call-sub", sub_thread),
            codex_tool_result_entry("2026-08-19T00:00:01Z", "call-sub", sub_thread),
        ],
        {"agentType": "Explore"},
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    threads = {event["thread"]: event for event in events_by_kind(events, "stats-agent-thread")}
    assert threads[main_thread]["line"] == 3
    assert "agent" not in threads[main_thread]
    assert threads[sub_thread]["agent"] == "claude:transcript/agent-sub"
    assert "line" not in threads[sub_thread]

    assert evidence.main([str(transcript), "--detail", "3"]) == 0
    detail = "".join(json.dumps(event, ensure_ascii=False) for event in read_jsonl(capsys))
    assert main_thread in detail


def test_stats_total_sums_main_subagent_and_normalized_codex(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """`stats-total`はCodex分をClaude形式の4成分へ変換してから3区分を合算する。"""
    thread_id = "77777777-7777-4777-8777-777777777777"
    codex_home = tmp_path / "codex"
    _write_rollout(
        codex_home,
        thread_id,
        [
            (
                "2026-08-19T00:00:03Z",
                {
                    "input_tokens": 100,
                    "cached_input_tokens": 90,
                    "cache_write_input_tokens": 7,
                    "output_tokens": 40,
                    "reasoning_output_tokens": 30,
                    "total_tokens": 140,
                },
            )
        ],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "id": "main",
                    "usage": {
                        "input_tokens": 2,
                        "output_tokens": 3,
                        "cache_creation_input_tokens": 4,
                        "cache_read_input_tokens": 5,
                    },
                },
            },
            codex_tool_use_entry("2026-08-19T00:00:02Z", "call-1", thread_id),
            codex_tool_result_entry("2026-08-19T00:00:02Z", "call-1", thread_id),
        ],
    )
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-normal",
        [assistant_usage_entry("2026-08-19T00:00:02Z", "sub", usage_record(10, 20))],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    total = events_by_kind(events, "stats-total")[0]
    assert total["tokens"] == {
        "input_tokens": 22,
        "output_tokens": 63,
        "cache_creation_input_tokens": 11,
        "cache_read_input_tokens": 95,
    }
    assert total["subagent_count"] == 1
    assert total["agent_thread_count"] == 1
    assert total["agent_thread_counts"] == {"codex": 1}
    thread_tokens = events_by_kind(events, "stats-agent-thread")[0]["tokens"]
    assert thread_tokens["total_tokens"] == 140
    assert thread_tokens["cached_input_tokens"] == 90


def test_stats_total_normalizes_codex_main_record(tmp_path: pathlib.Path, capsys) -> None:
    """メイン記録がCodex形式でも`stats-total`はClaude形式の4成分だけを持つ。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 900,
                            "cache_write_input_tokens": 8,
                            "output_tokens": 60,
                            "reasoning_output_tokens": 20,
                            "total_tokens": 1060,
                        },
                        "last_token_usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 900,
                            "cache_write_input_tokens": 8,
                            "output_tokens": 60,
                            "reasoning_output_tokens": 20,
                            "total_tokens": 1060,
                        },
                    },
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    total = events_by_kind(events, "stats-total")[0]
    assert total["tokens"] == {
        "input_tokens": 100,
        "output_tokens": 60,
        "cache_creation_input_tokens": 8,
        "cache_read_input_tokens": 900,
    }
    assert events_by_kind(events, "stats-summary")[0]["tokens"]["total_tokens"] == 1060
