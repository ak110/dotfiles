"""公開の準備から全担当・長文・成功結果を通読できる契約を検証する。"""

import json
import pathlib

import pytest
import session_review_prepare as prepare

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import write_subagent


def test_prepare_keeps_all_records_inputs_results_and_read_ranges(tmp_path: pathlib.Path, capsys) -> None:
    """候補に依存せず委譲先の正常操作と長い複合入力・遮断理由へ到達する。"""
    command = "echo " + "x" * 2500 + "中間の操作" + "y" * 6500 + "末尾の操作"
    output = "z" * 3999 + "\n保存先: /results/success.txt"
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "目的"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "main-call", "name": "Bash", "input": {"command": command}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "main-call", "content": output}],
                },
            },
        ],
    )
    write_subagent(
        transcript.parent / transcript.stem / "subagents",
        "agent-child",
        [
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "委譲の目的"}},
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "child-call",
                            "name": "Bash",
                            "input": {"command": "printf unique-normal-operation"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "isSidechain": True,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "child-call", "content": "正常結果の全内容"}],
                },
            },
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "blocked-call", "name": "Bash", "input": {"command": "blocked-operation"}}
                    ],
                },
            },
            {
                "type": "user",
                "isSidechain": True,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "blocked-call",
                            "is_error": True,
                            "content": (
                                'PreToolUse:Bash hook error: [hook]: <atk-auto source="pretooluse" kind="block">'
                                "遮断の理由\n次の操作: 修正して再実行</atk-auto>\n</tool_use_error>"
                            ),
                        }
                    ],
                },
            },
        ],
    )
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)]) == 0
    record = json.loads(capsys.readouterr().out)
    text = pathlib.Path(record["conversation_path"]).read_text(encoding="utf-8")
    for marker in (
        "中間の操作",
        "末尾の操作",
        "保存先: /results/success.txt",
        "委譲の目的",
        "unique-normal-operation",
        "正常結果の全内容",
        "agent-child",
        "blocked-operation",
        "遮断の理由",
        "次の操作: 修正して再実行",
    ):
        assert marker in text
    ranges = record["conversation_ranges"]
    assert ranges[0]["start_line"] == 1
    assert ranges[-1]["end_line"] == len(text.splitlines())
    assert all(left["end_line"] + 1 == right["start_line"] for left, right in zip(ranges, ranges[1:], strict=False))
    assert all(item["utf8_bytes"] <= 32000 for item in ranges)
    assert "z" * 3999 + "\n\n```" in text


@pytest.mark.parametrize(
    "entries",
    [
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "command": ["printf", "normal-operation"],
                        "status": "completed",
                        "exit_code": 0,
                        "aggregated_output": "normal-result",
                    },
                },
            }
        ],
        [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "state": "DONE",
                    "tool_name": "shell",
                    "tool_info": {"parameters": {"command": "normal-operation"}, "result": "normal-result"},
                },
            }
        ],
    ],
)
def test_prepare_keeps_native_and_agy_successful_operations(tmp_path: pathlib.Path, capsys, entries) -> None:
    """通常のツール結果以外の実行記録も操作と成功結果を通読へ渡す。"""
    transcript = _write_transcript(tmp_path, entries)
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    text = pathlib.Path(result["conversation_path"]).read_text(encoding="utf-8")
    assert "normal-operation" in text and "normal-result" in text
