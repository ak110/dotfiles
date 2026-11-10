"""JSON LinesのLF分割と非同期対象の開始バイト位置の対応を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import background_tasks


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("trailing", [False, True])
def test_async_launch_offsets_keeps_raw_byte_positions(tmp_path: pathlib.Path, ending: bytes, trailing: bool) -> None:
    """Unicode本文・空行・壊れた行・重複ID・sidechainがあっても最初の起動バイト位置を保つ。"""
    first = {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "前\u2028中\u2029後\u0085末"}}]
        },
    }
    result = {
        "type": "user",
        "toolUseResult": {"backgroundTaskId": "background"},
        "message": {"content": [{"type": "tool_result", "tool_use_id": "call", "content": "結果"}]},
    }
    rows = [
        json.dumps({"type": "user", "message": {"content": "依頼"}}, ensure_ascii=False).encode(),
        b"",
        b"broken",
        json.dumps(first, ensure_ascii=False).encode(),
        json.dumps(
            {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "tool_use", "id": "side"}]}},
            ensure_ascii=False,
        ).encode(),
        json.dumps(result, ensure_ascii=False).encode(),
    ]
    prefix = ending.join(rows[:3]) + ending
    result_prefix = ending.join(rows[:5]) + ending
    path = tmp_path / "record.jsonl"
    path.write_bytes(ending.join(rows) + (ending if trailing else b""))

    assert background_tasks.async_launch_offsets(str(path)) == {"call": len(prefix), "background": len(result_prefix)}
