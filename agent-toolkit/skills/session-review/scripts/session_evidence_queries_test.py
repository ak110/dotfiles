"""区別可能な照会を同じ記録へまとめる公開CLIの契約を検証する。"""

import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing import delegated_threads
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import read_jsonl, write_subagent

_SUMMARY_KINDS = {
    "record-provenance",
    "unresolved-record",
    "unresolved-delegation",
    "stats-total",
    "stats-summary",
    "stats-subagent",
    "stats-agent-thread",
}


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (["--context-at", "2", "--phrase=条文"], ["--detail", "1"]),
        (["--context-at", "2", "--phrase=条文"], ["--stats"]),
        (["--warn"], ["--grep", "条文"]),
        (["--elapsed-until", "2026-10-01T00:00:10Z"], ["--stats"]),
        (["--fixed-string", "条文"], ["--record-schema", "1"]),
    ],
)
def test_compatible_queries_preserve_results(tmp_path: pathlib.Path, capsys, left, right) -> None:
    """併用結果は各単独照会の全イベントを持ち、共通イベントを重複させない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"role": "user", "content": "条文"}},
            {"type": "user", "timestamp": "2026-10-01T00:00:01Z", "message": {"role": "user", "content": "事象"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "delegate",
                            "name": "mcp__agents_server__start",
                            "input": {"engine": "codex"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "toolUseResult": {"session_id": "unresolved-child"},
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "delegate", "content": "起動"}]},
            },
        ],
    )
    expected = []
    for query in (left, right):
        assert evidence.main([str(transcript), *query]) == 0
        expected.extend(read_jsonl(capsys, raw=True))
    assert evidence.main([str(transcript), *left, *right]) == 0
    actual = read_jsonl(capsys, raw=True)
    assert all(event in actual for event in expected)
    provenance = [event for event in actual if event["kind"] == "record-provenance"]
    assert len(provenance) == len({event["record"] for event in provenance})
    unresolved = [event for event in actual if event["kind"] == "unresolved-record"]
    assert len(unresolved) == len({(event["record"], event["line"]) for event in unresolved})


def test_combined_query_preserves_error(tmp_path: pathlib.Path, capsys) -> None:
    """一方の照会不能を他方の成功で覆わない。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "条文"}}])
    assert evidence.main([str(transcript), "--context-at", "99", "--phrase=x", "--detail", "1"]) == 2
    events = read_jsonl(capsys)
    assert {event["kind"] for event in events} >= {"error", "detail"}


@pytest.mark.parametrize("query", [["--grep", "x", "--user-events"], ["--hook-notices", "--user-events"]])
def test_ambiguous_queries_explain_conflict(capsys, query) -> None:
    """summaryを共有する照会には理由と分割操作を返す。"""
    assert evidence.main(["/unused", *query]) == 2
    (event,) = read_jsonl(capsys, raw=True)
    assert "summary" in event["text"]
    assert event["next_action"]


def test_stats_summary_preserves_complete_summary_events(tmp_path: pathlib.Path, capsys) -> None:
    """統計の表示選択は由来と診断を保ち、要約の全フィールドと順序を保持する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            {"type": "assistant", "timestamp": "2026-10-01T00:00:01Z", "message": {"role": "assistant", "content": "結果"}},
        ],
    )
    assert evidence.main([str(transcript), "--stats"]) == 0
    full = read_jsonl(capsys, raw=True)
    assert evidence.main([str(transcript), "--stats", "--summary-only"]) == 0
    summary = read_jsonl(capsys, raw=True)
    assert summary == [event for event in full if event["kind"] in _SUMMARY_KINDS]


def test_stats_summary_includes_claude_and_codex_delegates(tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
    """混在する実行系の担当要約も全量と同じ順序・値で返す。"""
    family = delegated_threads.write_delegated_threads(tmp_path)
    write_subagent(
        family.transcript.with_suffix("") / "subagents",
        "agent-native",
        [
            {
                "type": "assistant",
                "isSidechain": True,
                "timestamp": "2026-09-06T00:00:10Z",
                "message": {"role": "assistant", "content": "完了"},
            }
        ],
    )
    monkeypatch.setenv("HOME", str(family.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(family.home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(family.codex_home))
    arguments = [str(family.transcript), "--stats"]
    assert evidence.main(arguments) == 0
    full = read_jsonl(capsys, raw=True)
    assert evidence.main([*arguments, "--summary-only"]) == 0
    summary = read_jsonl(capsys, raw=True)
    assert summary == [event for event in full if event["kind"] in _SUMMARY_KINDS]
    assert {event["kind"] for event in summary} >= {"stats-total", "stats-summary", "stats-subagent", "stats-agent-thread"}
