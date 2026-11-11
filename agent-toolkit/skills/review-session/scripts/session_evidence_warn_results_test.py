"""セッション振り返りの試験（session_evidence_warn_results_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import execution_tool_use, read_jsonl


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


def test_query_modes_search_hook_notice_stored_under_attachment(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """hook通知のようにattachment配下へ格納された警告行を`--warn`・`--grep`の双方で照会する。

    走査対象はエントリ内の全本文であり、
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
    # 詳細は出力量の上限で末尾を省くため、保存先の内容を返したことを先頭で確かめる。
    assert read_jsonl(capsys)[0]["text"].startswith("先頭の行")
    assert evidence.main([str(transcript), "--warn"]) == 0
    assert "tail-only-marker" in capsys.readouterr().out


def test_query_modes_with_distinct_results_can_be_combined(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """警告と検索結果を同じ照会から取得する。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--warn", "--grep", "依頼"]) == 0

    events = read_jsonl(capsys)
    assert {event["kind"] for event in events} >= {"warning", "match", "summary"}
