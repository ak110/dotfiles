"""証拠抽出の`--tool-calls`照会（`session_evidence_tool_calls.py`）を、`session_review_evidence.py`の起動で検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    EMBEDDED_COMMAND,
    SELF_COMMAND,
    TOOL_CALL_THREAD,
    read_jsonl,
    timestamped_entry,
    tool_call_session,
)


def test_custom_exec_preserves_opaque_input(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Codexのfreeform入力を保持し、命令文字列から読了した資料を推定しない。"""
    script = 'await tools.exec_command({cmd: "cat /rules/read.md"});'
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "functions.exec",
                    "call_id": "exec",
                    "input": script,
                },
            }
        ],
    )
    assert evidence.main([str(transcript), "--tool-calls"]) == 0
    events = read_jsonl(capsys, raw=True)
    assert next(event for event in events if event["kind"] == "tool-call")["text"] == script
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    event = json.loads((bundle / "comparison-materials.jsonl").read_text(encoding="utf-8"))
    assert event["category"] == "opaque-command" and not event["target"]
    assert event["summary"] == script and event["observed_body_characters"] is None


def test_tool_calls_lists_main_and_delegate_calls_in_time_order(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """メイン、サブエージェントおよびCodex委譲先の呼び出しを、自己呼び出しを含めて時刻順に切り詰めずに返す。"""
    long_command = "echo " + "x" * 3000
    transcript = tool_call_session(tmp_path, monkeypatch, long_command)
    codex_record = f"codex:{TOOL_CALL_THREAD}"

    assert evidence.main([str(transcript), "--tool-calls"]) == 0

    *calls, summary = read_jsonl(capsys, raw=True)
    assert [(call["record"], call["line"], call["tool"], call["call_id"], call["result_line"]) for call in calls] == [
        ("claude:parent-session", 2, "Bash", "bash-self", 3),
        ("claude:parent-session", 4, "Bash", "bash-embed", 5),
        ("claude:parent-session/agent-child", 1, "Read", "read-1", None),
        ("claude:parent-session", 6, "mcp__agents_server__start", "call-codex", 7),
        (codex_record, 2, "exec_command", "c-exec", 3),
        (codex_record, 4, "apply_patch", "c-patch", 5),
        ("claude:parent-session", 8, "Bash", "bash-long", None),
    ]
    assert all(call["kind"] == "tool-call" for call in calls)
    assert [call["text"] for call in calls if call["tool"] in {"Bash", "exec_command", "apply_patch"}] == [
        SELF_COMMAND,
        EMBEDDED_COMMAND,
        "rg needle",
        "b.py",
        long_command,
    ]
    assert summary == {
        "kind": "tool-call-summary",
        "count": 7,
        "by_tool": {"Bash": 3, "Read": 1, "apply_patch": 1, "exec_command": 1, "mcp__agents_server__start": 1},
        "by_record": {"claude:parent-session": 4, "claude:parent-session/agent-child": 1, codex_record: 2},
    }


def test_tool_calls_select_by_tool_and_whole_input_regex(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--tool`は完全一致のいずれか、`--input-regex`は代表入力全体への検索とし、`^`はヒアドキュメントへ埋め込んだ文字列を除く。"""
    transcript = tool_call_session(tmp_path, monkeypatch, "echo done")

    assert (
        evidence.main(
            [str(transcript), "--tool-calls", "--tool", "Bash", "--input-regex", "^atk run-script session-review-evidence"]
        )
        == 0
    )
    *calls, summary = read_jsonl(capsys, raw=True)
    assert [call["call_id"] for call in calls] == ["bash-self"]
    assert summary["count"] == 1

    assert evidence.main([str(transcript), "--tool-calls", "--input-regex", "session-review-evidence"]) == 0
    *calls, _ = read_jsonl(capsys, raw=True)
    assert [call["call_id"] for call in calls] == ["bash-self", "bash-embed"]

    assert evidence.main([str(transcript), "--tool-calls", "--tool", "Read", "--tool", "exec_command", "--tool", "Bas"]) == 0
    *calls, summary = read_jsonl(capsys, raw=True)
    assert [call["call_id"] for call in calls] == ["read-1", "c-exec"]
    assert summary["by_tool"] == {"Read": 1, "exec_command": 1}


def test_tool_call_locators_round_trip_to_detail(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`record`と`line`は呼び出しの入力全体、`record`と`result_line`は結果の本文を`--detail`で返す。"""
    transcript = tool_call_session(tmp_path, monkeypatch, "echo done")
    assert evidence.main([str(transcript), "--tool-calls", "--tool", "Bash", "--tool", "exec_command"]) == 0
    *calls, _ = read_jsonl(capsys, raw=True)
    embed = next(call for call in calls if call["call_id"] == "bash-embed")
    codex_exec = next(call for call in calls if call["call_id"] == "c-exec")

    for call, expected_input, expected_result in (
        (embed, "atk run-script session-review-evidence -- transcript.jsonl --user-events", "埋め込みの結果"),
        (codex_exec, "rg needle", "一致なし"),
    ):
        assert evidence.main([str(transcript), "--detail", f"{call['record']}:{call['line']}"]) == 0
        assert expected_input in json.dumps(read_jsonl(capsys, raw=True), ensure_ascii=False).replace("\\n", "\n")
        assert evidence.main([str(transcript), "--detail", f"{call['record']}:{call['result_line']}"]) == 0
        assert expected_result in json.dumps(read_jsonl(capsys, raw=True), ensure_ascii=False)


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(["--tool", "Bash"], id="tool-without-tool-calls"),
        pytest.param(["--input-regex", "x"], id="input-regex-without-tool-calls"),
        pytest.param(["--tool-calls", "--input-regex", "("], id="invalid-regex"),
        pytest.param(["--tool-calls", "--grep", "x"], id="other-query-mode"),
    ],
)
def test_tool_calls_reject_invalid_arguments(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    """`--tool-calls`に関する引数の誤りは次の操作付きのエラーと終了コード2を返す。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-10-06T00:00:00Z", "記録")])

    assert evidence.main([str(transcript), *arguments]) == 2

    (event,) = read_jsonl(capsys)
    assert event["kind"] == "error"
