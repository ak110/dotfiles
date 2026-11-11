"""セッション振り返りの試験（session_evidence_warn_self_invocation_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import read_jsonl


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
        "python3 agent-toolkit/skills/review-session/scripts/session_review_evidence.py --warn /tmp/foo.jsonl",
        "uv run --project /plugin --locked --no-default-groups "
        "/plugin/skills/review-session/scripts/session_review_evidence.py /tmp/foo.jsonl",
        "./agent-toolkit/skills/review-session/scripts/session_review_evidence.py --grep 'warn' /tmp/foo.jsonl",
        "cd /repo && python3 agent-toolkit/skills/review-session/scripts/session_review_evidence.py --warn /tmp/foo.jsonl",
        "bash -lc 'python3 /plugin/skills/review-session/scripts/session_review_evidence.py --warn /tmp/foo.jsonl'",
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
    command = "python3 /plugin/skills/review-session/scripts/session_review_evidence.py --warn /tmp/foo.jsonl"
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
    command = "python3 agent-toolkit/skills/review-session/scripts/session_review_evidence.py --warn /tmp/foo.jsonl"
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
        "rg -n session_review_evidence agent-toolkit/skills/review-session/scripts",
        "grep -rn TODO agent-toolkit/skills/review-session/scripts/session_review_evidence.py",
        "cat agent-toolkit/skills/review-session/scripts/session_review_evidence.py",
        "sed -n '1,20p' agent-toolkit/skills/review-session/scripts/session_review_evidence.py",
        "head -n 5 agent-toolkit/skills/review-session/scripts/session_review_evidence_test.py",
        "tail -n 5 agent-toolkit/skills/review-session/scripts/session_review_evidence.py",
        "vim agent-toolkit/skills/review-session/scripts/session_review_evidence.py",
        "bash -lc 'rg -n session_review_evidence agent-toolkit/skills/review-session/scripts'",
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
