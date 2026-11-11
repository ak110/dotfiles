"""セッション振り返りの試験（session_evidence_warn_markers_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import read_jsonl


def test_warn_mode_ignores_identifier_only_management_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """警告形式でない管理用の値への一致を警告として報告しない。"""
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
    """実在する行頭マーカーを受理し、コマンド出力内のフック通知標識を除く。"""
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
    """一般警告語だけを引用・行番号付き表示から除外し、明示マーカーは維持する。"""
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
    """構造化警告は受理し、任意本文の検索は`--grep`へ分離する。"""
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
    """構造化警告フィールドの大文字表記差を許容し、本文途中の語は抽出しない。"""
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
    """警告キーの値または直接警告辞書の本文だけを抽出し、兄弟の通常本文を除外する。"""
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
    """区分だけを持つ構造化結果は、警告本文が無いため警告として数えない。"""
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
    """実記録形式で、定義と兄弟の実警告を公開照会・候補化で区別する。"""
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
