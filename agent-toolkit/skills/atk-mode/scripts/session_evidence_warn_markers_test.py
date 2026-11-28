from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib

import pytest
import session_review_evidence as evidence
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import read_jsonl
from session_evidence_warn import warning_count_main


def test_warning_count_codex_stop_counts_direct_output_not_replays_or_quotes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    archive = tmp_path / "archived_sessions"
    archive.mkdir()
    active = tmp_path / "sessions" / "2026" / "11" / "28"
    active.mkdir(parents=True)
    sid = "01234567-89ab-7abc-8def-123456789abc"
    tape = f"rollout-2026-11-28T00-00-00-{sid}.jsonl"
    metadata = {"type": "session_meta", "timestamp": "2026-11-28T00:00:00Z"}
    observed_at = "2026-11-28T00:12:00Z"
    quoted = {
        "type": "response_item",
        "timestamp": observed_at,
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": '<atk-auto source="norm_excuse_gate" kind="warn">引用</atk-auto>',
                }
            ],
        },
    }
    for root in (archive, active):
        rollout = root / tape
        rollout.write_text(f"{json.dumps(metadata)}\n{json.dumps(quoted)}\n", encoding="utf-8")
        last_record = datetime.datetime.fromisoformat(observed_at).timestamp()
        os.utime(rollout, (last_record, last_record))

    def event(at: str, character: str, source: str = "norm_excuse_gate") -> str:
        return f"{at} decision=codex_stop_warning source={source} occurrence={character * 64}\n"

    stop = tmp_path / f"claude-agent-toolkit-stop-{sid}.log"
    duplicate = event("2026-11-28T00:14:01Z", "a")
    stop.with_suffix(".log.1").write_text(event("2026-11-28T00:00:01Z", "e") + duplicate, encoding="utf-8")
    stop.write_text(
        duplicate
        + event("2026-11-28T00:14:02Z", "b")
        + event("2026-11-28T00:14:03Z", "c", "other")
        + event("2026-11-28T00:15:00Z", "d")
        + event("2026-11-28T00:14:08Z", "e")
        + event("2026-11-28T00:13:59Z", "f").replace(" decision=", " quoted decision=")
        + event("2026-11-28T00:14:05Z", "0").rstrip("\n"),
        encoding="utf-8",
    )
    assert (
        warning_count_main(
            ["--since", "2026-11-28T00:10:00Z", "--until", "2026-11-28T00:15:00Z", "--source", "norm_excuse_gate"]
        )
        == 0
    )
    row = read_jsonl(capsys)[0]
    assert row["sources"]["norm_excuse_gate"] == {"occurrences": 2, "conversations": 1}
    assert row["partial_records"] == 1
    assert sorted(item["record"] for item in row["occurrences"]) == sorted([f"{stop.with_suffix('.log.1')}:2", f"{stop}:2"])
    assert all(item["engine"] == "codex" and item["kind"] == "warn" for item in row["occurrences"])
    assert all(item["previous_speech"] is None and item["next_operation"] is None for item in row["occurrences"])


@pytest.mark.parametrize(
    ("state", "partial"),
    [("missing", 1), ("broken-rollout", 1), ("truncated", 1), ("stale", 1), ("available", 0), ("old", 0)],
)
def test_warning_count_distinguishes_unavailable_codex_stop_history_from_no_warning(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], state: str, partial: int
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    archive = tmp_path / "archived_sessions"
    archive.mkdir()
    sid = "01234567-89ab-7abc-8def-123456789abc"
    rollout = archive / f"rollout-2026-11-28T00-00-00-{sid}.jsonl"
    rollout.write_text('{"type":"session_meta","timestamp":"2026-11-28T00:00:00Z"}\n', encoding="utf-8")
    if state == "broken-rollout":
        with rollout.open("a", encoding="utf-8") as stream:
            stream.write('{"source":"norm_excuse_gate"\n')
    stop = tmp_path / f"claude-agent-toolkit-stop-{sid}.log"
    if state in {"truncated", "available"}:
        stop.write_text("2026-11-28T00:14:03Z decision=approve_input_wait\n", encoding="utf-8")
    if state == "stale":
        stop.write_text("2026-11-28T00:09:59Z decision=approve_input_wait\n", encoding="utf-8")
    if state == "truncated":
        stop.with_suffix(".log.1").write_text("2026-11-28T00:14:01Z decision=approve_input_wait\n", encoding="utf-8")
    modified = 0 if state == "old" else datetime.datetime.fromisoformat("2026-11-28T00:14:00Z").timestamp()
    os.utime(rollout, (modified, modified))
    assert (
        warning_count_main(
            ["--since", "2026-11-28T00:10:00Z", "--until", "2026-11-28T00:15:00Z", "--source", "norm_excuse_gate"]
        )
        == 0
    )
    row = read_jsonl(capsys)[0]
    assert row["sources"]["norm_excuse_gate"] == {"occurrences": 0, "conversations": 0}
    assert row["records"] == 1 and row["partial_records"] == partial


@pytest.mark.parametrize("lost_second_answer", [False, True])
def test_warning_count_codex_stop_joins_diagnostic_events_to_native_turn_evidence(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], lost_second_answer: bool
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    archive = tmp_path / "archived_sessions"
    archive.mkdir()
    sid = "01234567-89ab-7abc-8def-123456789abc"
    rollout = archive / f"rollout-2026-11-28T00-00-00-{sid}.jsonl"
    turn_id = "native-turn"
    timestamp = "2026-11-28T00:11:00Z"
    first, second = "最初の実応答。", "同じターンの別応答。"

    def response(text: str) -> dict:
        return {
            "type": "event_msg",
            "timestamp": timestamp,
            "payload": {
                "type": "item_completed",
                "turn_id": turn_id,
                "item": {"type": "AgentMessage", "content": [{"type": "Text", "text": text}]},
            },
        }

    records = [
        {"type": "session_meta", "timestamp": "2026-11-28T00:00:00Z"},
        response(first),
        {
            "type": "response_item",
            "timestamp": timestamp,
            "payload": {
                "type": "function_call",
                "name": "example_check",
                "call_id": "call-1",
                "arguments": "{}",
            },
        },
    ]
    if not lost_second_answer:
        records.append(response(second))
    rollout.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    log = tmp_path / f"claude-agent-toolkit-stop-{sid}.log"
    diagnostics = []
    for answer in (first, second):
        digest = hashlib.sha256(json.dumps((turn_id, answer)).encode()).hexdigest()
        diagnostics.append(f"{timestamp} decision=codex_stop_warning source=norm_excuse_gate occurrence={digest}\n")
    log.write_text("".join(diagnostics), encoding="utf-8")
    assert (
        warning_count_main(
            ["--since", "2026-11-28T00:10:00Z", "--until", "2026-11-28T00:15:00Z", "--source", "norm_excuse_gate"]
        )
        == 0
    )
    row = read_jsonl(capsys)[0]
    assert row["sources"]["norm_excuse_gate"] == {"occurrences": 2, "conversations": 1}
    assert row["partial_records"] == lost_second_answer
    before_tool, after_tool = row["occurrences"]
    assert before_tool["record"] == f"{rollout}:2" and before_tool["previous_speech"] == first
    assert before_tool["next_operation"]
    assert after_tool["next_operation"] is None
    if lost_second_answer:
        assert after_tool["record"] == f"{log}:2" and after_tool["previous_speech"] is None
    else:
        assert after_tool["record"] == f"{rollout}:4" and after_tool["previous_speech"] == second


def test_warn_mode_ignores_identifier_only_management_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event",
                "uuid": "warn-0001",
                "sessionId": "warning-session",
                "timestamp": "2026-08-18T00:00:00.000Z",
                "cwd": "/tmp/warn",
                "message": {"role": "user", "content": "依頼"},
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


@pytest.mark.parametrize(
    ("warning_line", "expected"),
    [
        ("[warn] 実行時警告", True),
        ("[warning] 実行時警告", True),
        ("[auto-generated: agent-toolkit/pretooluse][warn] 実行時警告", False),
        ("warning: 実行時警告", True),
        ("warn: 実行時警告", True),
        ("警告: 実行時警告", True),
        ("⚠: 実行時警告", True),
        ("⚠ 実行時警告", True),
        ("⚠    実行時警告", True),
    ],
)
def test_warn_mode_accepts_real_line_start_markers_only(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    warning_line: str,
    expected: bool,
) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "本文途中の warning と warn"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "call-1", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": warning_line}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    expected_events = (
        [{"kind": "warning", "line": 3, "text": warning_line, "tool": "call-1"}]
        if expected
        else [{"kind": "warning", "text": "一致なし"}]
    )
    assert read_jsonl(capsys) == expected_events


@pytest.mark.parametrize(
    ("warning_line", "matched"),
    [
        ("  [warn] 明示警告", True),
        ("12\t[warning] 明示警告", True),
        ("12\t [warn] 明示警告", False),
        ("  warning: 一般警告", False),
        ("12\twarning: 一般警告", False),
        ("12\t warning: 一般警告", False),
        ("warning: 一般警告", True),
    ],
)
def test_warn_mode_restricts_generic_markers_to_actual_line_start(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    warning_line: str,
    matched: bool,
) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "call-1", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": warning_line}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    expected = (
        [{"kind": "warning", "line": 2, "text": warning_line.lstrip(), "tool": "call-1"}]
        if matched
        else [{"kind": "warning", "text": "一致なし"}]
    )
    assert read_jsonl(capsys) == expected


def test_warn_mode_accepts_structured_warning_fields_and_grep_keeps_arbitrary_search(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "警告という語の説明"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-1",
                            "content": json.dumps({"warning_message": "構造化された警告"}, ensure_ascii=False),
                        }
                    ],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert read_jsonl(capsys) == [{"kind": "warning", "line": 2, "text": "構造化された警告", "tool": "call-1"}]

    assert evidence.main([str(transcript), "--grep", "警告"]) == 0
    matches = read_jsonl(capsys)
    assert [event["line"] for event in matches[:-1]] == [1, 2]
    assert matches[-1] == {"kind": "summary", "count": 2}


def test_warn_mode_accepts_codex_custom_tool_call_output_structured_warning(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexの実行結果出力を構造化警告として受理し、入力は`--grep`だけで検索する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"warning_message": "引数内の警告"}, ensure_ascii=False),
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call-1",
                    "output": json.dumps({"warning_message": "Codex実行結果の警告"}, ensure_ascii=False),
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert read_jsonl(capsys) == [{"kind": "warning", "line": 2, "text": "Codex実行結果の警告"}]

    assert evidence.main([str(transcript), "--grep", "引数内の警告"]) == 0
    assert read_jsonl(capsys) == [
        {"kind": "match", "line": 1, "timestamp": None, "text": '{"warning_message": "引数内の警告"}'},
        {"kind": "summary", "count": 1},
    ]


def test_warn_mode_accepts_case_variants_of_structured_warning_fields(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"WarningMessage": "構造化警告"},
                "message": {"role": "user", "content": []},
            },
            {
                "type": "user",
                "toolUseResult": {"message": "本文途中の WarningMessage"},
                "message": {"role": "user", "content": []},
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "line": 1, "text": "構造化警告"}]


def test_warn_mode_keeps_ordinary_siblings_out_of_structured_warning_text(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"warning_message": "構造化警告", "message": "通常本文"},
                "message": {"role": "user", "content": []},
            },
            {
                "type": "user",
                "toolUseResult": {
                    "kind": "warning",
                    "text": "直接表す警告本文",
                    "message": "通常の兄弟本文",
                },
                "message": {"role": "user", "content": []},
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [
        {"kind": "warning", "line": 1, "text": "構造化警告"},
        {"kind": "warning", "line": 2, "text": "直接表す警告本文"},
    ]


@pytest.mark.parametrize(
    "marker",
    (
        {"severity": "warning"},
        {"type": "warning"},
        {"kind": "warning"},
        {"severity": "warning", "stdout": "通常の標準出力", "stderr": "通常の標準エラー"},
    ),
)
def test_warn_mode_rejects_structured_markers_without_a_warning_body(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    marker: dict[str, str],
) -> None:
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "toolUseResult": marker, "message": {"role": "user", "content": []}}],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


@pytest.mark.parametrize(
    ("entry", "grep_pattern", "expected_text"),
    [
        (
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "SomeTool",
                            "id": "call-1",
                            "input": {"warning_message": "入力内JSONの警告"},
                        }
                    ],
                },
            },
            "入力内JSON",
            "入力内JSONの警告",
        ),
        (
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "SomeTool",
                            "id": "call-1",
                            "input": {"command": "warning: 入力内マーカー"},
                        }
                    ],
                },
            },
            "入力内マーカー",
            "warning: 入力内マーカー",
        ),
        (
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "SomeTool",
                            "id": "call-1",
                            "input": {"command": "⚠ 入力内マーカー"},
                        }
                    ],
                },
            },
            "入力内マーカー",
            "⚠ 入力内マーカー",
        ),
        (
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"warning_message": "引数内JSONの警告"}, ensure_ascii=False),
                },
            },
            "引数内JSON",
            '{"warning_message": "引数内JSONの警告"}',
        ),
        (
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"command": "warning: 引数内マーカー"}, ensure_ascii=False),
                },
            },
            "引数内マーカー",
            '{"command": "warning: 引数内マーカー"}',
        ),
    ],
)
def test_warn_mode_ignores_warning_markers_and_structured_json_in_inputs_but_grep_finds_it(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    entry: dict[str, object],
    grep_pattern: str,
    expected_text: str,
) -> None:
    """入力の行頭マーカーと構造化警告を`--warn`へ昇格させず、`--grep`では検索する。"""
    transcript = _write_transcript(tmp_path, [entry])

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]

    assert evidence.main([str(transcript), "--grep", grep_pattern]) == 0
    assert read_jsonl(capsys) == [
        {"kind": "match", "line": 1, "timestamp": None, "text": expected_text},
        {"kind": "summary", "count": 1},
    ]


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("field", ["warnings", "warning_message"])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("runtime", [False, True])
def test_schema_definitions_are_not_runtime_warnings(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    host: str,
    field: str,
    wrapped: bool,
    runtime: bool,
) -> None:
    value: dict[str, object] = {
        "outputSchema": {"type": "object", "properties": {field: {"type": "string", "description": "定義の説明文"}}}
    }
    if runtime:
        value[field] = "実際の警告"
    output = json.dumps([{"nested": value}], ensure_ascii=False) if wrapped else value
    if host == "codex":
        entry = {
            "type": "response_item",
            "payload": {"type": "custom_tool_call_output", "call_id": "schema-control", "output": output},
        }
    else:
        entry = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "schema-control",
                        "content": json.dumps(output, ensure_ascii=False) if isinstance(output, dict) else output,
                    }
                ],
            },
        }
    transcript = _write_transcript(tmp_path, [entry])
    assert evidence.main([str(transcript), "--warn"]) == 0
    events = read_jsonl(capsys)
    assert [event["text"] for event in events] == (["実際の警告"] if runtime else ["一致なし"])
    if runtime:
        assert events[0]["line"] == 1
    assert evidence.main([str(transcript), "--grep", "定義の説明文"]) == 0
    assert read_jsonl(capsys)[-1]["count"] == 1
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    read_jsonl(capsys)
    candidates = (bundle / "candidates.jsonl").read_text(encoding="utf-8")
    assert "定義の説明文" not in candidates
    assert ("実際の警告" in candidates) is runtime
