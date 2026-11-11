"""セッション振り返りの試験（session_evidence_warn_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import hook_attachment, read_jsonl


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


def test_warn_mode_reports_absence_when_no_entry_matches(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """一致が無い場合も、事実を1行で照会結果として返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]
