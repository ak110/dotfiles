"""公開の準備から全体像と詳細の記録位置へ到達できる契約を検証する。"""

import json
import pathlib

import pytest
import session_review_prepare as prepare

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import write_subagent


def test_prepare_keeps_all_records_as_compact_flow(tmp_path: pathlib.Path, capsys) -> None:
    """候補に依存せずメインと委譲先の目的・操作量・失敗を概要へ残す。"""
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
    for marker in ("目的", "委譲の目的", "agent-child", "Bash×2", "失敗1件"):
        assert marker in text
    for result_body in (
        "中間の操作",
        "末尾の操作",
        "unique-normal-operation",
        "blocked-operation",
        "保存先: /results/success.txt",
        "正常結果の全内容",
        "遮断の理由",
    ):
        assert result_body not in text
    assert record["conversation_utf8_bytes"] == len(text.encode("utf-8"))
    assert len(text.encode("utf-8")) <= record["conversation_limit_bytes"]


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
def test_prepare_summarizes_native_and_agy_successful_operations(tmp_path: pathlib.Path, capsys, entries) -> None:
    """通常のツール結果以外の実行記録も種類と量へ縮約し、結果の本文は載せない。"""
    transcript = _write_transcript(tmp_path, entries)
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    text = pathlib.Path(result["conversation_path"]).read_text(encoding="utf-8")
    assert "normal-operation" not in text and "normal-result" not in text
    assert "呼出1件" in text
