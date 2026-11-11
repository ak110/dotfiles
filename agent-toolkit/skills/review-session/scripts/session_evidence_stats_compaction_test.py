"""セッション振り返りの試験（session_evidence_stats_compaction_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_stats_support import _codex_token_count_entry
from agent_toolkit._testing.session_evidence_support import compaction_entry, events_by_kind, read_jsonl, write_subagent


def test_stats_reports_compaction_events_for_both_runtimes(tmp_path: pathlib.Path, capsys) -> None:
    """メイン記録とサブエージェント記録のコンパクションを全件数え、記録に無い欄を補わない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-09-02T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            compaction_entry(
                "2026-09-02T00:01:00Z",
                {"trigger": "auto", "preTokens": 493099, "postTokens": 14213, "durationMs": 201674},
            ),
            compaction_entry("2026-09-02T00:02:00Z", {"trigger": "manual"}),
        ],
    )
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [compaction_entry("2026-09-02T00:03:00Z", {"trigger": "auto", "durationMs": 1300})],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys, raw=True)
    assert events_by_kind(events, "stats-compaction") == [
        {
            "kind": "stats-compaction",
            "record": "claude:transcript",
            "line": 2,
            "engine": "claude",
            "timestamp": "2026-09-02T00:01:00Z",
            "trigger": "auto",
            "pre_tokens": 493099,
            "post_tokens": 14213,
            "duration_seconds": 201.7,
        },
        {
            "kind": "stats-compaction",
            "record": "claude:transcript",
            "line": 3,
            "engine": "claude",
            "timestamp": "2026-09-02T00:02:00Z",
            "trigger": "manual",
        },
        {
            "kind": "stats-compaction",
            "record": "claude:transcript/agent-child",
            "line": 1,
            "engine": "claude",
            "timestamp": "2026-09-02T00:03:00Z",
            "trigger": "auto",
            "duration_seconds": 1.3,
        },
    ]
    total = events_by_kind(events, "stats-compaction-total")[0]
    assert total == {
        "kind": "stats-compaction-total",
        "count": 3,
        "by_record": {"claude:transcript": 2, "claude:transcript/agent-child": 1},
        "total_duration_seconds": 203.0,
        "duration_unknown_count": 1,
    }
    assert list(total["by_record"]) == ["claude:transcript", "claude:transcript/agent-child"]


def test_stats_reports_codex_compaction_records(tmp_path: pathlib.Path, capsys) -> None:
    """Codex形式のコンパクションを`codex`として数え、所要時間の欄を付けない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _codex_token_count_entry("2026-09-02T00:00:00Z", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
            {
                "type": "compacted",
                "timestamp": "2026-09-02T00:01:00Z",
                "payload": {"message": "", "window_number": 1},
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys, raw=True)
    assert events_by_kind(events, "stats-compaction") == [
        {
            "kind": "stats-compaction",
            "record": "codex:transcript",
            "line": 2,
            "engine": "codex",
            "timestamp": "2026-09-02T00:01:00Z",
        }
    ]
    assert events_by_kind(events, "stats-compaction-total")[0] == {
        "kind": "stats-compaction-total",
        "count": 1,
        "by_record": {"codex:transcript": 1},
        "total_duration_seconds": 0.0,
        "duration_unknown_count": 1,
    }


def test_stats_assigns_codex_compaction_measurements_in_occurrence_order(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じthreadの計測記録を発生順に対応付け、残りは所要時間不明として数える。"""
    thread_id = "019945be-498f-70f2-a964-93e2c8d38954"
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "session_meta", "payload": {"id": thread_id}},
            _codex_token_count_entry("2026-09-02T00:00:00Z", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
            {"type": "compacted", "timestamp": "2026-09-02T00:01:00Z", "payload": {"window_number": 1}},
            {"type": "compacted", "timestamp": "2026-09-02T00:02:00Z", "payload": {"window_number": 2}},
        ],
    )
    record_dir = tmp_path / "compaction"
    record_dir.mkdir()
    (record_dir / f"{thread_id}.jsonl").write_text(
        json.dumps(
            {
                "version": 1,
                "thread_id": thread_id,
                "item_id": "item-1",
                "started_at_ms": 1000,
                "completed_at_ms": 3234,
                "duration_seconds": 2.2,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert evidence.main([str(transcript), "--stats", "--compaction-record-dir", str(record_dir)]) == 0

    events = read_jsonl(capsys, raw=True)
    compactions = events_by_kind(events, "stats-compaction")
    assert compactions[0]["duration_seconds"] == 2.2
    assert "duration_seconds" not in compactions[1]
    assert events_by_kind(events, "stats-compaction-total")[0] == {
        "kind": "stats-compaction-total",
        "count": 2,
        "by_record": {f"codex:{thread_id}": 2},
        "total_duration_seconds": 2.2,
        "duration_unknown_count": 1,
    }


def _claude_call(timestamp: str, call_id: str, name: str, tool_input: dict) -> dict:
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {"role": "assistant", "content": [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]},
    }


def _claude_result(timestamp: str, call_id: str, content) -> dict:
    return {
        "type": "user",
        "timestamp": timestamp,
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": content}]},
    }


def test_stats_reports_compaction_interval_usage_for_claude(tmp_path: pathlib.Path, capsys) -> None:
    """圧縮で終わる区間ごとに、ツール出力の合計とエージェント向け文書の表示を分けて返し、区間ごとに数え直す。"""
    norm = "x" * 300
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-09-02T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            _claude_call("2026-09-02T00:00:01Z", "t1", "Read", {"file_path": "/repo/agent-toolkit/share/exec.subagent.md"}),
            _claude_result("2026-09-02T00:00:02Z", "t1", norm),
            _claude_call("2026-09-02T00:00:03Z", "t2", "Bash", {"command": "uv run pytest -q"}),
            _claude_result("2026-09-02T00:00:04Z", "t2", [{"type": "text", "text": "y" * 100}]),
            _claude_call(
                "2026-09-02T00:00:05Z",
                "t3",
                "Bash",
                {"command": "sed -n 1,40p /home/u/.claude/plugins/cache/m/agent-toolkit/1.2.3/share/exec.subagent.md"},
            ),
            _claude_result("2026-09-02T00:00:06Z", "t3", "z" * 50),
            _claude_call("2026-09-02T00:00:07Z", "t4", "Bash", {"command": "rg -n x agent-basics/skills/bugfix/SKILL.md"}),
            _claude_result("2026-09-02T00:00:08Z", "t4", "w" * 20),
            compaction_entry("2026-09-02T00:01:00Z", {"trigger": "auto"}),
            _claude_call("2026-09-02T00:01:01Z", "t5", "Read", {"file_path": "/repo/.claude/skills/develop-dotfiles/SKILL.md"}),
            _claude_result("2026-09-02T00:01:02Z", "t5", "あ" * 10),
            compaction_entry("2026-09-02T00:02:00Z", {"trigger": "auto"}),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys, raw=True)
    assert events_by_kind(events, "stats-compaction-usage") == [
        {
            "kind": "stats-compaction-usage",
            "record": "claude:transcript",
            "line": 10,
            "tool_output_bytes": 470,
            "norm_doc_display_bytes": 350,
            "norm_doc_display_top": [{"path": "agent-toolkit/share/exec.subagent.md", "count": 2, "bytes": 350}],
        },
        {
            "kind": "stats-compaction-usage",
            "record": "claude:transcript",
            "line": 13,
            "tool_output_bytes": 30,
            "norm_doc_display_bytes": 30,
            "norm_doc_display_top": [{"path": ".claude/skills/develop-dotfiles/SKILL.md", "count": 1, "bytes": 30}],
        },
    ]
    assert len(events_by_kind(events, "stats-compaction")) == 2


def test_stats_reports_compaction_interval_usage_for_codex(tmp_path: pathlib.Path, capsys) -> None:
    """Codexの関数呼び出しの出力を区間ごとに数え、表示コマンドで読んだエージェント向け文書を内訳へ計上する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _codex_token_count_entry("2026-09-02T00:00:00Z", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
            {
                "type": "response_item",
                "timestamp": "2026-09-02T00:00:01Z",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "c1",
                    "arguments": json.dumps({"cmd": "cat agent-basics/skills/bugfix/references/root-cause-severity.md"}),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-02T00:00:02Z",
                "payload": {"type": "function_call_output", "call_id": "c1", "output": "s" * 64},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-02T00:00:03Z",
                "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c2", "input": "make test"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-02T00:00:04Z",
                "payload": {"type": "custom_tool_call_output", "call_id": "c2", "output": "t" * 36},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-02T00:00:05Z",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "c3",
                    "input": 'text(await tools.exec_command({cmd:"cat -- /repo/agent-toolkit/share/exec.subagent.md",'
                    "max_output_tokens:20000}));",
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-02T00:00:06Z",
                "payload": {"type": "custom_tool_call_output", "call_id": "c3", "output": "x" * 10},
            },
            {"type": "compacted", "timestamp": "2026-09-02T00:01:00Z", "payload": {"message": "", "window_number": 1}},
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys, raw=True)
    assert events_by_kind(events, "stats-compaction-usage") == [
        {
            "kind": "stats-compaction-usage",
            "record": "codex:transcript",
            "line": 8,
            "tool_output_bytes": 110,
            "norm_doc_display_bytes": 74,
            "norm_doc_display_top": [
                {"path": "agent-basics/skills/bugfix/references/root-cause-severity.md", "count": 1, "bytes": 64},
                {"path": "agent-toolkit/share/exec.subagent.md", "count": 1, "bytes": 10},
            ],
        }
    ]


def test_stats_reports_zero_compaction_total_without_records(tmp_path: pathlib.Path, capsys) -> None:
    """コンパクションの記録が無い場合は件数0の集計だけを返す。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "timestamp": "2026-09-02T00:00:00Z", "message": {"role": "user", "content": "依頼"}}],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys, raw=True)
    assert not events_by_kind(events, "stats-compaction")
    assert events_by_kind(events, "stats-compaction-total") == [
        {
            "kind": "stats-compaction-total",
            "count": 0,
            "by_record": {},
            "total_duration_seconds": 0.0,
            "duration_unknown_count": 0,
        }
    ]
