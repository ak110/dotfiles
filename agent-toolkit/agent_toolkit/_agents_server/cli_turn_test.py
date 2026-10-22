"""CLIの正しい長文JSONLを、イベント全量と順序を保って受け取る。"""

import asyncio
import json
import pathlib
import sys

import pytest

from agent_toolkit._agents_server import antigravity, opencode
from agent_toolkit._agents_server.cli_turn import CliTurnManager


@pytest.mark.asyncio
@pytest.mark.parametrize("manager_type", [antigravity.AntigravityManager, opencode.OpenCodeManager], ids=["agy", "opencode"])
@pytest.mark.parametrize("final_lf", [True, False], ids=["lf", "eof"])
async def test_large_jsonl_event_is_preserved_through_backend(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_type: type[CliTurnManager],
    final_lf: bool,
) -> None:
    """64 KiBを超える日本語のツール結果も、後続の終端とともに保存する。"""
    full_body = "資料の全文開始\n" + "日本語の規範本文。\n" * 10_000 + "資料の全文終了"
    if manager_type is antigravity.AntigravityManager:
        events = [
            {"event": "init", "conversation_id": "large-conversation"},
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 1,
                    "step_type": "tool",
                    "state": "ERROR",
                    "tool_name": "read_file",
                    "tool_info": {"error": {"message": full_body}},
                },
            },
            {"event": "result", "result": {"status": "SUCCESS", "response": "全文を確認した"}},
        ]
    else:
        events = [
            {"type": "step_start", "sessionID": "large-conversation"},
            {
                "type": "tool_use",
                "sessionID": "large-conversation",
                "part": {"tool": "read_file", "state": {"status": "completed", "output": full_body}},
            },
            {"type": "text", "sessionID": "large-conversation", "part": {"text": "全文を確認した"}},
        ]
    lines = [json.dumps(event, ensure_ascii=False).encode() for event in events]
    assert len(lines[1]) > 64 * 1024
    data = tmp_path / "events.jsonl"
    data.write_bytes(b"\n".join(lines) + (b"\n" if final_lf else b""))
    emitter = tmp_path / "emit.py"
    emitter.write_text(
        "import pathlib, sys\nsys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())\n", encoding="utf-8"
    )
    monkeypatch.setattr(manager_type, "build_command", staticmethod(lambda *_args: [sys.executable, str(emitter), str(data)]))
    monkeypatch.setattr(manager_type, "system_prompt", staticmethod(lambda _kind: "受入の固定入力"))
    condition = asyncio.Condition()
    log_directory = tmp_path / "logs"
    async with manager_type(condition=condition, log_directory=log_directory) as manager:
        session = await manager.start("固定入力", str(tmp_path), "test-model", "low")
        async with condition:
            await asyncio.wait_for(condition.wait_for(lambda: session.terminal), timeout=10)
        assert session.status == "completed", session.error
        assert session.agent_message == "全文を確認した"
    records = [json.loads(line) for line in (log_directory / "large-conversation.jsonl").read_text().splitlines()]
    # OpenCodeの入力補足記録も維持したまま、CLIが出した3イベントの全fieldを比較する。
    observed = [record for record in records if record.get("type") != "agent_toolkit_input"]
    assert observed == events
