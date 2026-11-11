"""セッション振り返りの試験（session_evidence_stats_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_stats_support import _codex_token_count_entry
from agent_toolkit._testing.session_evidence_support import events_by_kind, read_jsonl, timestamped_entry


def test_elapsed_until_combines_with_other_query_options(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """経過時間と統計を区別して同じ照会から取得する。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:01Z", "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:00:02Z", "--stats"]) == 0

    assert {event["kind"] for event in read_jsonl(capsys)} >= {"session-elapsed", "stats-total"}


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
    """空白区間、同一入力の反復と、全成分合計の順位だけでは上位に入らないトークン消費の極値を出力する。"""
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
                    # 先行10件はキャッシュ読取が大半を占め、全成分合計が大きい。
                    # 末尾1件は出力とキャッシュ作成が大半を占め、全成分合計では11位となる。
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
