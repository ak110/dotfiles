"""証拠抽出の`--grep`・`--detail`照会（`session_evidence_detail.py`）を、`session_review_evidence.py`の起動で検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    read_jsonl,
    timestamped_entry,
)


def test_observation_boundary_keeps_original_line_numbers(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """境界適用後も詳細位置は元ファイルの行番号を指す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:03Z", "除外する先頭行"),
            timestamped_entry("2026-09-01T00:00:01Z", "保持する元の2行目"),
        ],
    )

    assert evidence.main([str(transcript), "--observation-boundary", "2026-09-01T00:00:02Z", "--detail", "2"]) == 0

    assert read_jsonl(capsys) == [
        {
            "kind": "detail",
            "line": 2,
            "timestamp": "2026-09-01T00:00:01Z",
            "role": "user",
            "text": "保持する元の2行目",
        }
    ]


def test_query_modes_ignore_structural_values_of_codex_envelopes(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codex形式の入れ子構造が持つ区分値・識別子は一致とせず、実行結果の本文は検索する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "id": "exec-1",
                        "command": ["echo", "ok"],
                        "status": "completed",
                        "stdout": "done",
                    },
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--grep", "completed"]) == 0
    assert read_jsonl(capsys) == [{"kind": "summary", "count": 0}]

    assert evidence.main([str(transcript), "--grep", "done"]) == 0
    assert read_jsonl(capsys) == [
        {"kind": "match", "line": 1, "timestamp": None, "text": "done"},
        {"kind": "summary", "count": 1},
    ]


def test_grep_mode_searches_nested_values_under_management_named_keys(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """入れ子の汎用語キーが持つtool_use入力を検索し、エントリ直下の管理用の値は除外し続ける。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "uuid": "needle-value-uuid",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "c1", "name": "SomeTool", "input": {"mode": "needle-value"}}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--grep", "needle-value"]) == 0

    events = read_jsonl(capsys)
    assert events == [
        {"kind": "match", "line": 1, "timestamp": None, "text": "needle-value"},
        {"kind": "summary", "count": 1},
    ]


def test_detail_mode_keeps_tool_use_input_shapes_and_result_body(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """入力形態を問わずtool_useの`input`全体を保持し、tool_result本文も照会する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "c1", "input": {"command": "atk wi list", "n": 1}},
                        {"type": "tool_use", "name": "Read", "id": "c2", "input": {"file_path": "/tmp/x.md"}},
                        {"type": "tool_use", "name": "Agent", "id": "c3", "input": {"prompt": "依頼本文"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "結果本文"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1", "--detail", "2"]) == 0

    events = read_jsonl(capsys)
    assert [event["line"] for event in events] == [1, 1, 1, 2]
    assert [event.get("name") for event in events[:3]] == ["Bash", "Read", "Agent"]
    assert events[0]["input"] == {"command": "atk wi list", "n": 1}
    assert events[1]["input"] == {"file_path": "/tmp/x.md"}
    assert events[2]["input"] == {"prompt": "依頼本文"}
    assert events[3] == {"kind": "detail", "line": 2, "timestamp": None, "tool": "c1", "text": "結果本文"}


@pytest.mark.parametrize(
    "content",
    [
        "<persisted-output>",
        "<persisted-output>\nOutput too large (76.4KB). Full output saved to: /tmp/tool-results/x.txt",
        "",
    ],
)
def test_detail_mode_returns_persisted_body_from_tool_use_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    content: str,
) -> None:
    """本文が退避されたtool_resultでは、退避通知ではなく実行結果側の本文を詳細として返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "echo warning: real output"}}
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": content}],
                },
                "toolUseResult": {"stdout": "warning: real output", "stderr": ""},
            },
        ],
    )

    assert evidence.main([str(transcript), "--detail", "2"]) == 0

    assert read_jsonl(capsys) == [
        {"kind": "detail", "line": 2, "timestamp": None, "tool": "c1", "text": "warning: real output"}
    ]


def test_detail_mode_shares_one_clip_budget_across_entry_blocks(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """複数ブロックを持つエントリでも、詳細本文の合計を1エントリ分の上限内へ収める。

    省略標識は予算の上限へ達した本文だけへ付き、以降の本文は空文字列となる。
    省略が生じた事実はエントリの全イベントへ付く`omitted`が示す。
    """
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Agent", "id": "c1", "input": {"prompt": "あ" * 9000}},
                        {"type": "tool_use", "name": "Agent", "id": "c2", "input": {"prompt": "い" * 9000}},
                        {"type": "tool_result", "tool_use_id": "c3", "content": "う" * 9000},
                        {"type": "tool_result", "tool_use_id": "c4", "content": ""},
                    ],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1"]) == 0

    events = read_jsonl(capsys)
    total = sum(len(event["input"]["prompt"]) for event in events if "input" in event)
    total += sum(len(event["text"]) for event in events if "text" in event)
    assert total <= 8000
    assert events[0]["input"]["prompt"].endswith("…[省略]")
    assert events[1]["input"]["prompt"] == ""
    assert events[2]["text"] == ""
    assert events[3]["text"] == ""
    assert all(event["omitted"] is True for event in events)


def test_detail_mode_marks_omission_when_budget_ends_exactly_before_next_value(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """先行する本文の長さが上限と一致する場合も、後続の非空値の省略を判別できる。

    上限に達した後の本文は空文字列となるため、`omitted`が無ければ
    元から空の値と省略された値を区別できない。
    """
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Agent", "id": "c1", "input": {"prompt": "あ" * 8000}},
                        {"type": "tool_use", "name": "Read", "id": "c2", "input": {"file_path": "/tmp/x.md"}},
                        {"type": "tool_result", "tool_use_id": "c3", "content": "後続本文"},
                    ],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1"]) == 0

    events = read_jsonl(capsys)
    assert len(events[0]["input"]["prompt"]) == 8000
    assert events[1]["input"]["file_path"] == ""
    assert events[2]["text"] == ""
    assert all(event["omitted"] is True for event in events)


def test_detail_mode_keeps_total_within_limit_for_many_string_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """文字列値を多数持つ入力でも、詳細本文の合計が1エントリ分の上限を超えない。

    上限へ達した後も本文ごとに省略標識を付けると、合計が文字列値の個数に比例して増える。
    """
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
                            "name": "Bash",
                            "id": "c1",
                            "input": {f"key{index}": "あ" * 20 for index in range(5000)},
                        }
                    ],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1"]) == 0

    events = read_jsonl(capsys)
    assert sum(len(value) for value in events[0]["input"].values()) <= 8000
    assert events[0]["omitted"] is True


def test_detail_mode_formats_entry_without_tool_blocks(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """tool_useもtool_resultも持たないエントリはJSON整形本文で照会する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "依頼"}]},
            }
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1"]) == 0

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["detail"]
    assert "依頼" in events[0]["text"]


def test_detail_mode_rejects_line_number_outside_transcript(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """範囲外の行番号は照会不能として終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--detail", "9"]) == 2

    assert read_jsonl(capsys) == [{"kind": "error", "text": "行番号9は範囲外"}]


def test_detail_mode_rejects_space_separated_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """複数位置は`--detail`を繰り返して指定し、空白区切りの複数値は受理しない。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "message": {"role": "user", "content": "依頼"}}],
    )

    with pytest.raises(SystemExit) as raised:
        evidence.main([str(transcript), "--detail", "1", "2"])

    assert raised.value.code == 2
    assert capsys.readouterr().out == ""


def test_grep_mode_reports_matching_lines_and_entry_count(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """tool_use入力とtool_result本文を含む可視テキストから一致行と一致エントリ数を照会する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "c1", "input": {"command": "atk wi list"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "失敗: atk wi list"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--grep", "atk wi"]) == 0

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["match", "match", "summary"]
    assert [event["line"] for event in events[:2]] == [2, 3]
    assert events[-1]["count"] == 2


def test_grep_mode_rejects_invalid_regular_expression(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """不正な正規表現は照会不能として終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--grep", "["]) == 2

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert "正規表現が不正" in events[0]["text"]


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_fixed_string_mode_reports_each_query_without_bodies(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    runtime: str,
) -> None:
    """固定文字列を結合せず、entry単位の件数と位置だけを両実行系で返す。"""
    text = "値 [.*] と同じ値 [.*]"
    entry = (
        {"type": "user", "timestamp": "2026-10-06T00:00:00Z", "message": {"role": "user", "content": text}}
        if runtime == "claude"
        else {
            "type": "response_item",
            "timestamp": "2026-10-06T00:00:00Z",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        }
    )
    transcript = _write_transcript(tmp_path, [entry])

    assert evidence.main([str(transcript), "--fixed-string", "[.*]", "--fixed-string", "不在"]) == 0

    events = read_jsonl(capsys)
    assert events == [
        {
            "kind": "fixed-string-summary",
            "query": "[.*]",
            "count": 1,
            "locators": [{"record": f"{runtime}:transcript", "line": 1, "timestamp": "2026-10-06T00:00:00Z"}],
        },
        {"kind": "fixed-string-summary", "query": "不在", "count": 0, "locators": []},
    ]
    assert text not in json.dumps(events, ensure_ascii=False)


def test_multi_line_detail_query_keeps_each_problem_locator_stable(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """先行行が複数イベントを返しても、同じ完全引数列なら各証拠位置を再取得する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "call-1", "input": {"command": "true"}},
                        {"type": "tool_use", "name": "Read", "id": "call-2", "input": {"file_path": "/tmp/x"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "別イベント"}],
                },
            },
        ],
    )
    query = "--detail 1 --detail 2"
    query_args = query.split()
    locators = [{"event_index": index} for index in range(3)]

    assert evidence.main([str(transcript), *query_args]) == 0
    first = read_jsonl(capsys)
    assert evidence.main([str(transcript), *query_args]) == 0
    second = read_jsonl(capsys)

    assert first == second
    assert [second[locator["event_index"]] for locator in locators] == first
    assert [event["line"] for event in second] == [1, 1, 2]
    assert second[2] == {"kind": "detail", "line": 2, "timestamp": None, "tool": "call-1", "text": "別イベント"}
    assert query == "--detail 1 --detail 2"
    assert "別イベント" not in query
    assert all(set(locator) == {"event_index"} for locator in locators)

    assert evidence.main([str(transcript), "--detail", "2"]) == 0
    individual = read_jsonl(capsys)
    assert individual[0] == second[2]
    assert individual[0] != second[0]


def test_detail_and_grep_include_timestamp(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--detail`と`--grep`の各イベントが元記録行の時刻を持ち、時刻の無い行では`null`となる。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "timestamp": "2026-09-25T01:02:03Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "c1", "input": {"command": "needle"}}],
                },
            },
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "needle結果"}]},
            },
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1", "--detail", "2"]) == 0
    assert [event["timestamp"] for event in read_jsonl(capsys)] == ["2026-09-25T01:02:03Z", None]

    assert evidence.main([str(transcript), "--grep", "needle"]) == 0
    matches = [event for event in read_jsonl(capsys) if event["kind"] == "match"]
    assert [(event["line"], event["timestamp"]) for event in matches] == [(1, "2026-09-25T01:02:03Z"), (2, None)]
