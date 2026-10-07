"""証拠抽出の`--warn`照会（`session_evidence_warn.py`）を、`session_review_evidence.py`の起動で検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    execution_tool_use,
    hook_attachment,
    read_jsonl,
)


def _execution_result_transcript(tmp_path: pathlib.Path, *contents: str) -> pathlib.Path:
    """実行ツールの呼び出しと結果を持つ記録を書く。"""
    return _write_transcript(
        tmp_path,
        [
            execution_tool_use("call-1"),
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": content} for content in contents],
                },
            },
        ],
    )


def test_warn_excludes_hook_marker_in_command_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """コマンド出力に現れたフック通知標識を実行時のフック警告として返さない。"""
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 文書中の例示"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": notice},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": notice}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


def test_warn_keeps_hook_marker_in_hook_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """フック実行の記録に由来する通知標識を実行時警告として返す。"""
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 実行時の警告"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [notice],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "line": 1, "text": notice}]


@pytest.mark.parametrize("element", ["agent-toolkit-hook-message", "agent-toolkit-auto-inserted"])
@pytest.mark.parametrize(
    "attributes",
    ['source="agent-toolkit/pretooluse" kind="warn"', 'kind="warn" source="agent-toolkit/pretooluse"'],
)
def test_warn_keeps_xml_hook_marker_in_hook_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    element: str,
    attributes: str,
) -> None:
    """XML境界のwarn通知を実行時警告として返す。"""
    notice = f"<{element} {attributes}>\n実行時の警告\n</{element}>"
    transcript = _write_transcript(
        tmp_path,
        [
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-xml-warn",
                    "content": [notice],
                }
            )
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "line": 1, "text": "実行時の警告"}]


def test_warn_keeps_command_output_warning(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """フック通知標識を持たないコマンド出力の警告は検出対象に保つ。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: build failed"},
                "message": {"role": "user", "content": []},
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "line": 1, "text": "warning: build failed"}]


def test_warn_excludes_quoted_warning_inside_code_fence_of_command_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """コマンドが表示した文書のコードフェンス内の警告は、実行時警告として扱わない。

    `sed`や`cat -n`で過去の振り返りのAWIなどのMarkdownを表示すると、過去の警告の引用がフェンス内に現れる。
    これを候補にすると、対象セッションで発生していない警告が`candidates.md`へ載る。フェンス外の警告は保持する。
    """
    shown_document = "\n".join(
        [
            "### c0071 warning",
            "```text",
            "warn: 一括stageに編集記録が無いファイルが含まれている",
            "```",
            "  12\t~~~",
            "  13\twarning: 行番号付きで表示した引用",
            "  14\t~~~",
            "warning: 表示の後に出た実行時警告",
        ]
    )
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "call-1", "input": {"command": "sed -n '1,9p' a.md"}}
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": shown_document}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert [event["text"] for event in read_jsonl(capsys)] == ["warning: 表示の後に出た実行時警告"]


def test_warn_excludes_absence_statement(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """問題の不在を述べる警告本文を除き、実在する警告は保持する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "警告: なし\n警告: 3件\nwarning: none.\nwarning: build failed"},
                "message": {"role": "user", "content": []},
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert [event["text"] for event in read_jsonl(capsys)] == ["警告: 3件", "warning: build failed"]


def test_warn_collects_codex_item_completed_command_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexの完了したコマンド実行から通常の実行時警告を返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "completed",
                        "aggregated_output": "warning: build failed, waiting for other jobs to finish...",
                    },
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [
        {
            "kind": "warning",
            "line": 1,
            "text": "warning: build failed, waiting for other jobs to finish...",
        }
    ]


def test_warn_excludes_hook_marker_in_codex_command_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexのコマンド出力に現れたフック通知標識を警告として返さない。"""
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 文書中の例示"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "CommandExecution", "status": "completed", "aggregated_output": notice},
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


def test_warn_mode_reports_matching_entries_with_line_and_tool(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """警告に一致したエントリだけを、行番号と手掛かりのツール識別子付きで照会する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "call-1", "input": {"command": "make test"}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "warning: 警告が出た"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["warning"]
    assert events[0]["line"] == 3
    assert events[0]["tool"] == "call-1"
    assert events[0]["text"] == "warning: 警告が出た"


@pytest.mark.parametrize("tool_name", ["WebSearch", "WebFetch", "Read", "web__run"])
def test_warn_mode_excludes_plain_warning_lines_from_external_content_tools(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    tool_name: str,
) -> None:
    """外部検索および文書取得の本文にある警告語を実行時警告へ数えない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": tool_name, "id": "call-1", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "warning: 外部本文"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


def test_warn_mode_keeps_structured_warning_from_external_tool(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """外部ツールも構造化して返した警告は実行時警告として保持する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "WebSearch", "id": "call-1", "input": {}}],
                },
            },
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


def test_warn_mode_excludes_plain_warning_lines_from_codex_web_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexの外部検索結果に含まれる警告語も実行時警告へ数えない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "web__run", "call_id": "call-1", "arguments": "{}"},
            },
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "call_id": "call-1", "output": "warning: 外部本文"},
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


def test_warn_mode_excludes_plain_warning_lines_from_unmatched_results(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """呼び出し名を対応付けられない結果本文を実行時警告へ数えない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "missing-tool", "content": "warning: 外部本文"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "missing-function",
                    "output": "warning: 外部本文",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "missing-custom",
                    "output": "warning: 外部本文",
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


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
    """構造化警告フィールドの大文字表記差を許容し、本文途中の語は拾わない。"""
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


def test_query_modes_search_hook_notice_stored_under_attachment(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """hook通知のようにattachment配下へ格納された警告行を`--warn`・`--grep`の双方で照会する。

    走査対象は既知フィールドの列挙ではなくエントリ内の全本文であり、
    hook通知の格納先（実行結果のJSON・追加コンテキストの配列）を問わず一致する。
    """
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 検証コマンドの出力を切り詰めている"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "attachment",
                "uuid": "hook-success",
                "attachment": {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "stdout": json.dumps({"hookSpecificOutput": {"additionalContext": notice}}, ensure_ascii=False),
                    "stderr": "",
                    "exitCode": 0,
                },
            },
            {
                "type": "attachment",
                "uuid": "hook-context",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [notice],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    warnings = read_jsonl(capsys)
    assert [event["line"] for event in warnings] == [1]
    assert notice in warnings[0]["text"]

    assert evidence.main([str(transcript), "--grep", "切り詰めている"]) == 0
    matches = read_jsonl(capsys)
    assert [event["line"] for event in matches[:2]] == [1, 2]
    assert matches[-1] == {"kind": "summary", "count": 2}


def test_warn_mode_keeps_hook_notices_with_distinct_or_missing_tool_use_ids(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """異なるツール呼び出しの通知と識別子を持たない通知は個別の警告として保持する。"""
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 検証コマンドの出力を切り詰めている"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [notice],
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-2",
                    "content": [notice],
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "content": [notice],
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "content": [notice],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    warnings = read_jsonl(capsys)
    assert [event["line"] for event in warnings] == [1, 2, 3, 4]
    assert [event["text"] for event in warnings] == [notice] * 4


def test_warn_mode_reports_matches_found_only_in_tool_use_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """本文が外部退避されたBash出力の警告を、ツール実行結果側から照会する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: 退避された警告", "stderr": "警告: 標準エラー"},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "<persisted-output>"}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    events = read_jsonl(capsys)
    assert [event["text"] for event in events] == ["warning: 退避された警告", "警告: 標準エラー"]
    assert [event["line"] for event in events] == [1, 1]


def test_warn_mode_ignores_read_result_body_and_keeps_hook_notice(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """読み取り結果の本文を除外し、同じレコードのフック通知だけを警告として返す。"""
    file_body = 'warning: 文書内の例示\n{"warning_message": "文書内の構造化警告"}'
    notice = "[auto-generated: agent-toolkit/posttooluse][warn] 読み取り後の通知"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {
                    "type": "text",
                    "file": {"filePath": "/tmp/rules.md", "content": file_body, "numLines": 2},
                },
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "read-1", "content": file_body}],
                },
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PostToolUse:Read",
                    "toolUseID": "read-1",
                    "content": [notice],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert [event["text"] for event in read_jsonl(capsys)] == [notice]


def test_warn_mode_does_not_duplicate_tool_use_result_already_visible(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """可視テキストと同一の実行結果を重ねて照会しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: 同じ警告\n"},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "warning: 同じ警告\n"}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert [event["text"] for event in read_jsonl(capsys)] == ["warning: 同じ警告"]


@pytest.mark.parametrize(
    ("option", "pattern"),
    [("--warn", None), ("--grep", "warning")],
)
def test_query_modes_normalize_line_number_prefix_across_body_fields(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    option: str,
    pattern: str | None,
) -> None:
    """別本文間だけ行番号接頭辞を正規化し、最初に現れた原文を表示する。"""
    transcript = _execution_result_transcript(tmp_path, "12\twarning: 同じ本文", "warning: 同じ本文")

    arguments = [str(transcript), option]
    if pattern is not None:
        arguments.append(pattern)
    assert evidence.main(arguments) == 0

    events = read_jsonl(capsys)
    expected_text = "warning: 同じ本文" if option == "--warn" else "12\twarning: 同じ本文"
    expected_event: dict[str, object] = {
        "kind": "warning" if option == "--warn" else "match",
        "line": 2,
        "text": expected_text,
    }
    if option == "--warn":
        expected_event["tool"] = "call-1"
    else:
        expected_event["timestamp"] = None
    assert events[0] == expected_event
    if option == "--grep":
        assert events[-1] == {"kind": "summary", "count": 1}
        assert len(events) == 2
    else:
        assert len(events) == 1


@pytest.mark.parametrize(
    ("option", "pattern"),
    [("--warn", None), ("--grep", "warning")],
)
def test_query_modes_keep_numbered_and_unnumbered_lines_in_one_body_distinct(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    option: str,
    pattern: str | None,
) -> None:
    """同一本文内の行番号接頭辞は、別行を表すため重複として除かない。"""
    transcript = _execution_result_transcript(tmp_path, "12\twarning: 本文\nwarning: 本文")

    arguments = [str(transcript), option]
    if pattern is not None:
        arguments.append(pattern)
    assert evidence.main(arguments) == 0

    events = read_jsonl(capsys)
    expected = ["warning: 本文"] if option == "--warn" else ["12\twarning: 本文", "warning: 本文"]
    assert [event["text"] for event in events if event["kind"] in {"warning", "match"}] == expected
    if option == "--grep":
        assert events[-1] == {"kind": "summary", "count": 1}


def test_grep_mode_searches_tool_use_result_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--grep`も`--warn`と同じくツール実行結果の生出力を走査対象に含める。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "退避された本文の照合語"},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "<persisted-output>"}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--grep", "照合語"]) == 0

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["match", "summary"]
    assert events[0] == {"kind": "match", "line": 1, "timestamp": None, "text": "退避された本文の照合語"}
    assert events[-1]["count"] == 1


def _self_invocation_entries(command: str) -> list[dict]:
    """本スクリプト自身を呼び出したBashコマンドと、その照会結果からなるエントリ列を構成する。"""
    return [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": command}}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu1",
                        "content": json.dumps({"kind": "warning", "text": "過去の照会結果"}, ensure_ascii=False),
                    }
                ],
            },
        },
    ]


@pytest.mark.parametrize(
    "command",
    [
        "python3 agent-toolkit/skills/session-review/scripts/session_review_evidence.py --warn /tmp/foo.jsonl",
        "uv run --project /plugin --locked --no-default-groups "
        "/plugin/skills/session-review/scripts/session_review_evidence.py /tmp/foo.jsonl",
        "./agent-toolkit/skills/session-review/scripts/session_review_evidence.py --grep 'warn' /tmp/foo.jsonl",
        "cd /repo && python3 agent-toolkit/skills/session-review/scripts/session_review_evidence.py --warn /tmp/foo.jsonl",
        "bash -lc 'python3 /plugin/skills/session-review/scripts/session_review_evidence.py --warn /tmp/foo.jsonl'",
    ],
)
def test_query_modes_ignore_own_invocation_and_its_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    """起動形式を問わず、本スクリプトを実行したコマンドとその照会結果を報告しない。"""
    transcript = _write_transcript(tmp_path, _self_invocation_entries(command))

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]

    assert evidence.main([str(transcript), "--grep", "warning"]) == 0
    assert read_jsonl(capsys) == [{"kind": "summary", "count": 0}]


def test_query_modes_ignore_own_invocation_recorded_as_codex_exec_command(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexのコマンド実行記録（`arguments`が`cmd`キーを持つ形式）の自己呼び出しも報告しない。"""
    command = "python3 /plugin/skills/session-review/scripts/session_review_evidence.py --warn /tmp/foo.jsonl"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"cmd": command, "workdir": "/repo"}, ensure_ascii=False),
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": json.dumps({"kind": "warning", "text": "過去の照会結果"}, ensure_ascii=False),
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]

    assert evidence.main([str(transcript), "--grep", "warning"]) == 0
    assert read_jsonl(capsys) == [{"kind": "summary", "count": 0}]


def test_query_modes_keep_warnings_outside_own_invocation(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """自己呼び出しの除外はその呼び出し記録だけに留め、無関係なエントリの警告は照会し続ける。"""
    command = "python3 agent-toolkit/skills/session-review/scripts/session_review_evidence.py --warn /tmp/foo.jsonl"
    entries = [
        *_self_invocation_entries(command),
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Bash", "id": "tu2", "input": {}}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu2", "content": "warning: 実在の警告"}],
            },
        },
    ]
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--warn"]) == 0

    events = read_jsonl(capsys)
    assert [event["text"] for event in events] == ["warning: 実在の警告"]
    assert [event["line"] for event in events] == [4]


@pytest.mark.parametrize(
    "command",
    [
        "rg -n session_review_evidence agent-toolkit/skills/session-review/scripts",
        "grep -rn TODO agent-toolkit/skills/session-review/scripts/session_review_evidence.py",
        "cat agent-toolkit/skills/session-review/scripts/session_review_evidence.py",
        "sed -n '1,20p' agent-toolkit/skills/session-review/scripts/session_review_evidence.py",
        "head -n 5 agent-toolkit/skills/session-review/scripts/session_review_evidence_test.py",
        "tail -n 5 agent-toolkit/skills/session-review/scripts/session_review_evidence.py",
        "vim agent-toolkit/skills/session-review/scripts/session_review_evidence.py",
        "bash -lc 'rg -n session_review_evidence agent-toolkit/skills/session-review/scripts'",
    ],
)
def test_query_modes_keep_records_that_only_reference_the_script_file(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    """スクリプトを検索・閲覧・編集するだけのコマンドは実行と扱わず、その警告を照会し続ける。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": command}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c1",
                            "content": [{"type": "text", "text": "warning: relevant output"}],
                        }
                    ],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    events = read_jsonl(capsys)
    assert [event["text"] for event in events] == ["warning: relevant output"]
    assert [event["line"] for event in events] == [2]


def test_warn_mode_reports_absence_when_no_entry_matches(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """一致が無い場合も、事実を1行で照会結果として返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


def test_replaced_persisted_output_is_read_from_saved_file(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PostToolUseが退避した出力の`stdout`を通知へ置き換えた記録でも、保存先の内容を検索・詳細・警告の実体とする。

    置き換え後の記録は元の出力を`persistedOutputPath`のファイルだけに持つ。記録の`stdout`だけを読むと、
    保存先の末尾にだけある文字列が検索で見つからず、振り返りが出力の不在を誤って結論づける。
    記録の形は、Claude Code 2.1.291でPostToolUseの`updatedToolOutput`が`stdout`を置き換えたときのtranscriptの
    `toolUseResult`（置き換えた`stdout`と元の`persistedOutputPath`・`persistedOutputSize`）から写した。
    """
    saved = tmp_path / "tool-results" / "b1.txt"
    saved.parent.mkdir()
    saved.write_text("先頭の行\n" * 3000 + "warning: tail-only-marker\n", encoding="utf-8")
    notice = f"このコマンドの出力はホストの上限を超えたため保存先へ退避された。保存先: {saved}"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "cat large.md"}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c1",
                            "content": f"<persisted-output>\nOutput too large (48.8KB). Full output saved to: {saved}\n\n"
                            f"Preview (first 2KB):\n{notice}\n</persisted-output>",
                        }
                    ],
                },
                "toolUseResult": {
                    "stdout": notice,
                    "stderr": "",
                    "persistedOutputPath": str(saved),
                    "persistedOutputSize": saved.stat().st_size,
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--fixed-string", "tail-only-marker"]) == 0
    assert read_jsonl(capsys)[0]["count"] == 1
    assert evidence.main([str(transcript), "--detail", "2"]) == 0
    # 詳細は出力量の上限で末尾を省くため、通知ではなく保存先の内容を返したことを先頭で確かめる。
    assert read_jsonl(capsys)[0]["text"].startswith("先頭の行")
    assert evidence.main([str(transcript), "--warn"]) == 0
    assert "tail-only-marker" in capsys.readouterr().out


def test_query_modes_are_mutually_exclusive(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """照会モードの併用は引数誤用として終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--warn", "--grep", "依頼"]) == 2

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert "併用できない" in events[0]["text"]
