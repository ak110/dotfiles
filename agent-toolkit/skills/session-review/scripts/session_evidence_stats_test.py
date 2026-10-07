"""証拠抽出の`--stats`照会（`session_evidence_stats.py`）を、`session_review_evidence.py`の起動で検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing import delegated_threads
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    assistant_usage_entry,
    codex_tool_result_entry,
    codex_tool_use_entry,
    compaction_entry,
    events_by_kind,
    read_jsonl,
    timestamped_entry,
    usage_record,
    write_subagent,
)


def test_elapsed_until_conflicts_with_other_query_options(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """経過時間照会と他の照会モードの同時指定を拒否する。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:01Z", "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:00:02Z", "--stats"]) == 2

    (event,) = read_jsonl(capsys)
    assert event["kind"] == "error"
    assert "併用できない" in event["text"]


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        pytest.param(
            # Claude Code 2.1.291の記録の形。モデルは`message.model`、推論量は最上位の`effort`に置かれる。
            [
                {"type": "user", "timestamp": "2026-10-06T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
                {
                    "type": "assistant",
                    "timestamp": "2026-10-06T00:00:01Z",
                    "effort": "medium",
                    "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "結果"}]},
                },
            ],
            [{"engine": "claude", "model": "claude-opus-5-5", "effort": "medium", "source": "observed"}],
            id="claude",
        ),
        pytest.param(
            # Codex 0.160.1の記録の形。最上位の`type`が`turn_context`の行の`payload`に`model`と`effort`を持つ。
            [
                {
                    "type": "turn_context",
                    "timestamp": "2026-10-06T00:00:00Z",
                    "payload": {"model": "gpt-6-luna", "effort": "medium"},
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-10-06T00:00:01Z",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "依頼"}]},
                },
            ],
            [{"engine": "codex", "model": "gpt-6-luna", "effort": "medium", "source": "observed"}],
            id="codex",
        ),
    ],
)
def test_stats_reports_observed_identity_from_host_records(
    tmp_path: pathlib.Path, capsys, entries: list[dict], expected: list[dict]
) -> None:
    """実記録の形のClaude CodeとCodexの記録から、`--stats`が観測したモデルと推論量を表示する。

    記録の形と異なる場所から読むと、振り返りの統計が実行したモデルと推論量を欠く。
    """
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    assert events_by_kind(read_jsonl(capsys), "stats-summary")[0]["observed_identities"] == expected


def test_stats_deduplicates_claude_usage_and_reports_tool_breakdown(tmp_path: pathlib.Path, capsys) -> None:
    """Claudeの重複messageとツール所要時間を集計し、呼び出し行を保持する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "id": "message-1",
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 4,
                        "cache_creation_input_tokens": 5,
                        "cache_read_input_tokens": 6,
                    },
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "call-1", "input": {"command": "make test"}},
                        {"type": "tool_use", "name": "Read", "id": "call-2", "input": {"file_path": "/tmp/target.py"}},
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:04Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "完了"}]},
            },
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:11Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-2", "content": "本文"}]},
            },
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:01:10Z",
                "message": {
                    "role": "assistant",
                    "id": "message-1",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "cache_creation_input_tokens": 30,
                        "cache_read_input_tokens": 40,
                    },
                    "content": [{"type": "text", "text": "結果"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    summary = events_by_kind(events, "stats-summary")[0]
    assert summary["tokens"] == {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 40,
    }
    assert summary["api_messages"] == 1
    assert summary["elapsed_seconds"] == 70
    assert events_by_kind(events, "stats-tool") == [
        {"kind": "stats-tool", "tool": "Read", "count": 1, "total_seconds": 10.0},
        {"kind": "stats-tool", "tool": "Bash", "count": 1, "total_seconds": 3.0},
    ]
    assert events_by_kind(events, "stats-slow-call") == [
        {"kind": "stats-slow-call", "tool": "Read", "seconds": 10.0, "line": 2, "hint": "/tmp/target.py"},
        {"kind": "stats-slow-call", "tool": "Bash", "seconds": 3.0, "line": 2, "hint": "make test"},
    ]
    assert events_by_kind(events, "stats-token-peak")[0]["line"] == 5


def test_stats_reports_gap_repeat_and_token_peak_union(tmp_path: pathlib.Path, capsys) -> None:
    """空白区間、同一入力の反復、単一順位では除外されるトークン極値を出力する。"""
    entries: list[dict] = [
        {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
    ]
    for index in range(11):
        timestamp = f"2026-08-19T00:{2 + index:02d}:00Z"
        entries.append(
            {
                "type": "assistant",
                "timestamp": timestamp,
                "message": {
                    "role": "assistant",
                    "id": f"message-{index}",
                    # 先行10件はキャッシュ読取が支配し全成分合計が大きい。
                    # 末尾1件は生成が支配し、全成分合計では11位となる。
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 1 if index < 10 else 100,
                        "cache_creation_input_tokens": 0 if index < 10 else 50,
                        "cache_read_input_tokens": 1000 if index < 10 else 0,
                    },
                    "content": [
                        {"type": "tool_use", "name": "Read", "id": f"read-{index}", "input": {"command": "same input"}}
                    ],
                },
            }
        )
        entries.append(
            {
                "type": "user",
                "timestamp": f"2026-08-19T00:{2 + index:02d}:01Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": f"read-{index}", "content": "完了"}],
                },
            }
        )
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    gaps = events_by_kind(events, "stats-gap")
    # 60秒以上の空白は先頭の依頼から最初のassistantまでの1件だけで、59秒の空白は出力されない。
    assert gaps == [{"kind": "stats-gap", "seconds": 120.0, "before_line": 1, "after_line": 2}]
    assert all(event["seconds"] >= 60 for event in gaps)
    repeats = events_by_kind(events, "stats-repeat")
    assert repeats and repeats[0]["tool"] == "Read" and repeats[0]["count"] == 11
    peaks = events_by_kind(events, "stats-token-peak")
    generative = next(event for event in peaks if event["line"] == 22)
    assert generative["total_tokens"] == 150
    assert all(event["total_tokens"] > generative["total_tokens"] for event in peaks if event["line"] != 22)


def test_stats_sums_codex_last_token_usage_and_pairs_tool_calls(tmp_path: pathlib.Path, capsys) -> None:
    """Codexのトークンは各`token_count`の実消費の加算とし、call_idで所要時間を対応付ける。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {"type": "message", "role": "user", "content": "依頼"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:01Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                        "last_token_usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                    },
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:02Z",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"command": ["make", "test"]}),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:04Z",
                "payload": {"type": "custom_tool_call_output", "call_id": "call-1", "output": "完了"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:05Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"input_tokens": 22, "output_tokens": 33, "total_tokens": 55},
                        "last_token_usage": {"input_tokens": 20, "output_tokens": 30, "total_tokens": 50},
                    },
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    assert events_by_kind(events, "stats-summary")[0]["tokens"]["total_tokens"] == 55
    assert events_by_kind(events, "stats-tool")[0]["total_seconds"] == 2.0
    assert events_by_kind(events, "stats-slow-call")[0]["line"] == 3


def _codex_token_count_entry(timestamp: str, usage: dict[str, int], cumulative: dict[str, int] | None = None) -> dict:
    """Codexの`token_count`エントリを作成する。

    `usage`はそのリクエストの実消費（`last_token_usage`）、`cumulative`はセッション累積
    （`total_token_usage`）とする。`cumulative`を省略した場合は同じ値を与える。
    """
    return {
        "type": "response_item",
        "timestamp": timestamp,
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": usage if cumulative is None else cumulative, "last_token_usage": usage},
        },
    }


def _codex_usage(input_tokens: int, cached: int, cache_write: int, output_tokens: int, reasoning: int) -> dict[str, int]:
    """Codex形式の6成分`token_usage`を作成する。`total_tokens`は入力と出力の合計とする。"""
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": cache_write,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning,
        "total_tokens": input_tokens + output_tokens,
    }


def test_stats_sums_codex_last_token_usage_across_rewind(tmp_path: pathlib.Path, capsys) -> None:
    """累積値が巻き戻る記録でも、各リクエストの実消費の単純加算として集計する。

    Codexは過去のチェックポイントへ戻ると`total_token_usage`を巻き戻し先の値へ戻して再累積するため、
    累積値の減少を区間境界とみなして減少前の値を加算すると、巻き戻し先までの消費を二重計上する。
    本フィクスチャでは減少前の累積（入力150）を加算すると入力が280となり、実消費の合計180と一致しない。
    """
    transcript = _write_transcript(
        tmp_path,
        [
            _codex_token_count_entry(
                "2026-08-19T00:00:00Z", _codex_usage(100, 80, 5, 20, 10), _codex_usage(100, 80, 5, 20, 10)
            ),
            _codex_token_count_entry("2026-08-19T00:00:01Z", _codex_usage(50, 40, 2, 10, 4), _codex_usage(150, 120, 7, 30, 14)),
            _codex_token_count_entry("2026-08-19T00:00:02Z", _codex_usage(30, 20, 1, 5, 2), _codex_usage(130, 100, 6, 25, 12)),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    summary = events_by_kind(events, "stats-summary")[0]
    assert summary["tokens"] == _codex_usage(180, 140, 8, 35, 16)
    assert summary["api_messages"] == 3
    assert events_by_kind(events, "stats-total")[0]["tokens"] == {
        "input_tokens": 40,
        "output_tokens": 35,
        "cache_creation_input_tokens": 8,
        "cache_read_input_tokens": 140,
    }


def test_stats_skips_codex_duplicate_token_count_records(tmp_path: pathlib.Path, capsys) -> None:
    """同一リクエストを再送した`token_count`は合計へ加算しない。

    Codexはターン終了時に直前と同一の`total_token_usage`・`last_token_usage`を持つレコードを
    再記録する。無条件加算では入力が200となり、実際のリクエスト2件分の150と一致しない。
    """
    duplicated = _codex_usage(150, 120, 7, 30, 14)
    transcript = _write_transcript(
        tmp_path,
        [
            _codex_token_count_entry(
                "2026-08-19T00:00:00Z", _codex_usage(100, 80, 5, 20, 10), _codex_usage(100, 80, 5, 20, 10)
            ),
            _codex_token_count_entry("2026-08-19T00:00:01Z", _codex_usage(50, 40, 2, 10, 4), duplicated),
            _codex_token_count_entry("2026-08-19T00:00:02Z", _codex_usage(50, 40, 2, 10, 4), duplicated),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    summary = events_by_kind(read_jsonl(capsys), "stats-summary")[0]
    assert summary["tokens"] == duplicated
    assert summary["api_messages"] == 2


def test_stats_skips_codex_zero_usage_record_after_compact(tmp_path: pathlib.Path, capsys) -> None:
    """compact直後の実消費0のレコードは合計へ影響しない。

    このレコードは`last_token_usage`の6成分が全て0でありながら`total_token_usage`は直前と同一のため、
    加算対象へ含めると`api_messages`が実際のリクエスト数を上回る。
    """
    cumulative = _codex_usage(100, 80, 5, 20, 10)
    transcript = _write_transcript(
        tmp_path,
        [
            _codex_token_count_entry("2026-08-19T00:00:00Z", _codex_usage(100, 80, 5, 20, 10), cumulative),
            _codex_token_count_entry("2026-08-19T00:00:01Z", _codex_usage(0, 0, 0, 0, 0), cumulative),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    summary = events_by_kind(read_jsonl(capsys), "stats-summary")[0]
    assert summary["tokens"] == cumulative
    assert summary["api_messages"] == 1


def test_stats_token_peak_normalizes_codex_cache_components(tmp_path: pathlib.Path, capsys) -> None:
    """Codexの`stats-token-peak`はキャッシュ成分をClaude形式へ変換して出力する。"""
    transcript = _write_transcript(
        tmp_path,
        [_codex_token_count_entry("2026-08-19T00:00:00Z", _codex_usage(300, 250, 7, 40, 20))],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    peak = events_by_kind(read_jsonl(capsys), "stats-token-peak")[0]
    assert peak["total_tokens"] == 347
    assert peak["input_tokens"] == 50
    assert peak["cache_read_input_tokens"] == 250
    assert peak["cache_creation_input_tokens"] == 7
    assert peak["output_tokens"] == 40


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
    """起動ツールの構造化終端結果からthreadIdを収集し、引用と既存session操作は除外する。

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
    """Codex rolloutのcustom tool call終端結果からClaude sessionを解決し、エンジン別に集計する。"""
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
    assert events_by_kind(stats_events, "unresolved-record") == [
        {"kind": "unresolved-record", "record": write_session, "line": 4}
    ]

    assert evidence.main([str(transcript)]) == 0
    default_events = read_jsonl(capsys, raw=True)
    assert f"claude:{custom_session}" in {event.get("record") for event in default_events}
    assert events_by_kind(default_events, "unresolved-record") == [
        {"kind": "unresolved-record", "record": write_session, "line": 4}
    ]


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
    assert events_by_kind(read_jsonl(capsys, raw=True), "unresolved-delegation") == [
        {"kind": "unresolved-delegation", "record": "codex:transcript", "line": 2}
    ]


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
    assert events_by_kind(read_jsonl(capsys, raw=True), "unresolved-delegation") == [
        {"kind": "unresolved-delegation", "record": "codex:transcript", "line": 1}
    ]


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
    assert "make build" in detail[0]["text"]


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


def _write_rollout(codex_home: pathlib.Path, thread_id: str, usages: list[tuple[str, dict[str, int]]]) -> None:
    """`CODEX_HOME`配下へthreadIdに対応するrolloutを書き込む。

    `usages`の各要素はそのリクエストの実消費（`last_token_usage`）とし、
    `total_token_usage`にはそこまでの走行合計を与える。
    """
    rollout_dir = codex_home / "sessions" / "2026" / "08" / "19"
    rollout_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    cumulative: dict[str, int] = {}
    for timestamp, usage in usages:
        for key, value in usage.items():
            cumulative[key] = cumulative.get(key, 0) + value
        entries.append(
            {
                "timestamp": timestamp,
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": dict(cumulative), "last_token_usage": usage},
                },
            }
        )
    (rollout_dir / f"rollout-test-{thread_id}.jsonl").write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n",
        encoding="utf-8",
    )


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


def test_stats_reports_no_target_without_timestamp_and_tokens(tmp_path: pathlib.Path, capsys) -> None:
    """timestampもトークン情報も無い入力では集計対象なしを返し、終了コード0で終わる。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "message": {"role": "user", "content": "依頼"}}],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    assert events_by_kind(events, "stats-summary") == [{"kind": "stats-summary", "text": "集計対象なし"}]


def test_stats_repeat_limits_to_ten_groups_and_requires_hint(tmp_path: pathlib.Path, capsys) -> None:
    """反復呼び出しは入力ヒントを持つ組だけを回数降順で最大10件出力する。"""
    entries: list[dict] = []
    for index in range(12):
        for repetition in range(2):
            call_id = f"bash-{index}-{repetition}"
            entries.append(
                {
                    "type": "assistant",
                    "timestamp": f"2026-08-19T00:{index:02d}:{repetition:02d}Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": f"command-{index}"}}
                        ],
                    },
                }
            )
            entries.append(
                {
                    "type": "user",
                    "timestamp": f"2026-08-19T00:{index:02d}:{repetition:02d}Z",
                    "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "済"}]},
                }
            )
    for index in range(3):
        call_id = f"todo-{index}"
        entries.append(
            {
                "type": "assistant",
                "timestamp": f"2026-08-19T00:30:{index:02d}Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "TodoWrite", "id": call_id, "input": {"todos": []}}],
                },
            }
        )
        entries.append(
            {
                "type": "user",
                "timestamp": f"2026-08-19T00:30:{index:02d}Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "済"}]},
            }
        )
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    repeats = events_by_kind(events, "stats-repeat")
    assert len(repeats) == 10
    assert {event["tool"] for event in repeats} == {"Bash"}
    assert all(event["hint"].startswith("command-") for event in repeats)


def test_stats_repeat_uses_target_input_keys_of_tools_without_command(tmp_path: pathlib.Path, capsys) -> None:
    """`command`を持たないツールでも対象を表す入力キーをヒントとし、反復を集計する。"""
    entries: list[dict] = []
    inputs = [{"file_path": "/tmp/same.py"}] * 3 + [{"pattern": "同じ検索語"}] * 2 + [{"file_path": "/tmp/other.py"}]
    names = ["Read"] * 3 + ["Grep"] * 2 + ["Read"]
    for index, (name, block_input) in enumerate(zip(names, inputs, strict=True)):
        call_id = f"call-{index}"
        entries.append(
            {
                "type": "assistant",
                "timestamp": f"2026-08-19T00:00:{index:02d}Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": name, "id": call_id, "input": block_input}],
                },
            }
        )
        entries.append(
            {
                "type": "user",
                "timestamp": f"2026-08-19T00:00:{index:02d}Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "済"}]},
            }
        )
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    repeats = events_by_kind(events, "stats-repeat")
    assert [(event["tool"], event["hint"], event["count"]) for event in repeats] == [
        ("Read", "/tmp/same.py", 3),
        ("Grep", "同じ検索語", 2),
    ]
    assert repeats[0]["lines"] == [1, 3, 5]


def _bash_call_entries(commands: list[str]) -> list[dict]:
    """Bashの呼び出しと結果を2秒間隔で交互に並べたClaude Codeの記録を返す。"""
    entries: list[dict] = []
    for index, command in enumerate(commands):
        call_id = f"call-{index}"
        entries.append(
            {
                "type": "assistant",
                "timestamp": f"2026-08-19T00:00:{index * 2:02d}Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": command}}],
                },
            }
        )
        entries.append(
            {
                "type": "user",
                "timestamp": f"2026-08-19T00:00:{index * 2 + 1:02d}Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "済"}]},
            }
        )
    return entries


def test_stats_repeat_distinguishes_multiline_commands_sharing_first_line(tmp_path: pathlib.Path, capsys) -> None:
    """先頭行が同一の複数行コマンドは、本体が異なれば同じ反復組へ集約しない。"""
    commands = ["cd /repo\nmake test", "cd /repo\nmake lint", "cd /repo\nmake test"]
    transcript = _write_transcript(tmp_path, _bash_call_entries(commands))

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    assert events_by_kind(events, "stats-repeat") == [
        {"kind": "stats-repeat", "tool": "Bash", "hint": "cd /repo\nmake test", "count": 2, "lines": [1, 5]}
    ]


def test_stats_repeat_distinguishes_long_commands_sharing_clipped_prefix(tmp_path: pathlib.Path, capsys) -> None:
    """表示上の切り詰め長を超えて前方一致するだけの呼び出しは、同じ反復組へ集約しない。"""
    shared_prefix = "echo " + "a" * 2100
    commands = [f"{shared_prefix} first", f"{shared_prefix} second", f"{shared_prefix} first"]
    transcript = _write_transcript(tmp_path, _bash_call_entries(commands))

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    repeats = events_by_kind(events, "stats-repeat")
    assert [(event["tool"], event["count"], event["lines"]) for event in repeats] == [("Bash", 2, [1, 5])]
    assert repeats[0]["hint"].endswith("…[省略]")
    assert len(repeats[0]["hint"]) == 2000 + len("…[省略]")


def test_stats_takes_codex_hint_from_input_without_arguments(tmp_path: pathlib.Path, capsys) -> None:
    """`arguments`を持たないCodexの呼び出しでは`input`の本文をヒントとする。"""
    command_input = 'const out = await sh({cmd: "rg -n \'foo\' src", workdir: "/repo"});'
    entries: list[dict] = [
        {
            "type": "response_item",
            "timestamp": "2026-08-19T00:00:00Z",
            "payload": {"type": "message", "role": "user", "content": "依頼"},
        }
    ]
    for index, seconds in enumerate((2, 6)):
        call_id = f"call-{index}"
        started = index * 10 + 1
        entries.append(
            {
                "type": "response_item",
                "timestamp": f"2026-08-19T00:00:{started:02d}Z",
                "payload": {
                    "type": "custom_tool_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": "exec",
                    "input": command_input,
                },
            }
        )
        entries.append(
            {
                "type": "response_item",
                "timestamp": f"2026-08-19T00:00:{started + seconds:02d}Z",
                "payload": {"type": "custom_tool_call_output", "call_id": call_id, "output": "完了"},
            }
        )
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = read_jsonl(capsys)
    assert events_by_kind(events, "stats-repeat") == [
        {"kind": "stats-repeat", "tool": "exec", "hint": command_input, "count": 2, "lines": [2, 4]}
    ]
    assert events_by_kind(events, "stats-slow-call") == [
        {"kind": "stats-slow-call", "tool": "exec", "seconds": 6.0, "line": 4, "hint": command_input},
        {"kind": "stats-slow-call", "tool": "exec", "seconds": 2.0, "line": 2, "hint": command_input},
    ]


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
