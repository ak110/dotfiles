"""CLIの正しい長文JSONLを、イベント全量と順序を保って受け取る。"""

import asyncio
import json
import os
import pathlib
import sys
from asyncio.subprocess import Process
from collections.abc import Callable

import psutil
import pytest

from agent_toolkit._agents_server import antigravity, cli_turn, opencode
from agent_toolkit._agents_server.cli_turn import CliTurnManager
from agent_toolkit._common import process_tree


@pytest.mark.asyncio
@pytest.mark.parametrize("manager_type", [antigravity.AntigravityManager, opencode.OpenCodeManager], ids=["agy", "opencode"])
@pytest.mark.parametrize("final_lf", [True, False], ids=["lf", "eof"])
async def test_large_jsonl_event_is_preserved_through_backend(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_type: type[CliTurnManager],
    final_lf: bool,
) -> None:
    """64 KiBを超える日本語のツール結果も、後続の終了とともに保存する。"""
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
    # OpenCodeの入力補足記録を保持し、CLIが出力した3イベントの全fieldを比較する。
    observed = [record for record in records if record.get("type") != "agent_toolkit_input"]
    assert observed == events


@pytest.mark.skipif(os.name == "nt", reason="POSIXのTERM無視と別process groupの実子孫を観測する")
@pytest.mark.parametrize("manager_type", [antigravity.AntigravityManager, opencode.OpenCodeManager], ids=["agy", "opencode"])
@pytest.mark.asyncio
async def test_close_reclaims_descendants_when_kill_wait_stalls(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    manager_type: type[CliTurnManager],
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """KILL後のwaitが返らなくても実CLIと保存済み子孫を回収する。"""
    script = tmp_path / "runtime.py"
    event = (
        {"event": "init", "conversation_id": "shutdown"}
        if manager_type is antigravity.AntigravityManager
        else {"type": "step_start", "sessionID": "shutdown"}
    )
    script.write_text(
        "import json, os, pathlib, signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "directory = pathlib.Path(sys.argv[1])\n"
        "leaf = len(sys.argv) > 2\n"
        "(directory / ('leaf.pid' if leaf else 'runtime.pid')).write_text(str(os.getpid()))\n"
        "if not leaf:\n"
        "    subprocess.Popen([sys.executable, __file__, str(directory), 'leaf'],\n"
        "        stdout=subprocess.DEVNULL, start_new_session=True)\n"
        f"    print({json.dumps(json.dumps(event))}, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        manager_type, "build_command", staticmethod(lambda *_args: [sys.executable, str(script), str(tmp_path)])
    )
    monkeypatch.setattr(manager_type, "system_prompt", staticmethod(lambda _kind: "停止試験"))
    monkeypatch.setattr(cli_turn, "_STOP_TIMEOUT", 0.05)
    monkeypatch.setattr(cli_turn, "_child_env", lambda _owner: host_environ())
    targets: list[psutil.Process] = []
    async with manager_type(condition=asyncio.Condition(), log_directory=tmp_path / "logs") as manager:
        try:
            await manager.start("停止試験", str(tmp_path), "test-model", "low")

            async def ready() -> None:
                while not (tmp_path / "leaf.pid").exists():
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(ready(), 10)
            runtime_pid = int((tmp_path / "runtime.pid").read_text())
            targets = [psutil.Process(runtime_pid), *process_tree.collect_descendants(runtime_pid)]
            wait = Process.wait
            calls: list[int] = []

            async def stalled_wait(process: Process) -> int:
                if process.pid == runtime_pid:
                    calls.append(process.pid)
                    await asyncio.Event().wait()
                return await wait(process)

            monkeypatch.setattr(Process, "wait", stalled_wait)
            await asyncio.wait_for(manager.close(), 15)
            assert len(calls) >= 2
            assert all(not target.is_running() or target.status() == psutil.STATUS_ZOMBIE for target in targets)
        finally:
            await asyncio.to_thread(process_tree.reclaim_processes, targets, timeout=0.5)
