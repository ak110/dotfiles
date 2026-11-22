"""Codex backendの資源解放の試験で、模擬App Serverを実プロセスとして起動・観測する。

模擬App Serverの本体は`fake_codex_app_server.py`にある。試験はbackendの起動コマンドを模擬App Serverへ差し替え、
backendが起動したApp Serverとthread用のMCPサーバー役の子プロセスを、模擬App Serverが残すイベント記録から特定する。
試験の後始末は、記録したPIDと起動時刻の組から特定した自身の起動したプロセスだけを終了させる。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import pathlib
import sys
from typing import Any

import psutil
import pytest

from agent_toolkit._agents_server import codex_rpc
from agent_toolkit._common import process_identity

FAKE_APP_SERVER = pathlib.Path(__file__).with_name("fake_codex_app_server.py")

REAL_CREATE_SUBPROCESS_EXEC = asyncio.create_subprocess_exec
"""試験の隔離が子プロセスの起動を拒否する前の`asyncio.create_subprocess_exec`。"""


@dataclasses.dataclass
class FakeAppServer:
    log_path: pathlib.Path

    def events(self, event: str | None = None) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        records = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [record for record in records if event is None or record["event"] == event]

    async def complete_turn(self, thread_id: str) -> None:
        async with asyncio.timeout(10):
            while True:
                ready = [
                    row
                    for row in self.events("turn/ready")
                    if row["thread"] == thread_id and not pathlib.Path(row["release"]).exists()
                ]
                if ready:
                    pathlib.Path(ready[-1]["release"]).touch()
                    return
                await asyncio.sleep(0.01)

    def server_pid(self, thread_id: str) -> int:
        return [record["server"] for record in self.events() if record.get("thread") == thread_id][-1]

    def mcp_pids(self, thread_id: str) -> list[int]:
        return [record["pid"] for record in self.events("mcp") if record["thread"] == thread_id]

    def _process(self, pid: int) -> psutil.Process | None:
        created = next(
            (record for record in self.events() if record["event"] in {"server", "mcp"} and record["pid"] == pid),
            None,
        )
        if created is None:
            raise AssertionError(f"pid {pid} was not started by the fake App Server")
        try:
            process = psutil.Process(pid)
            if not process_identity.matches(process, created["created"], created.get("start_id")) or process.status() in (
                psutil.STATUS_ZOMBIE,
                psutil.STATUS_DEAD,
            ):
                return None
        except psutil.Error:
            return None
        return process

    def alive(self, pid: int) -> bool:
        return self._process(pid) is not None

    def terminate_recorded(self) -> None:
        targets = [
            process
            for record in self.events()
            if record["event"] in {"server", "mcp"} and (process := self._process(record["pid"])) is not None
        ]
        for process in targets:
            with contextlib.suppress(psutil.Error):
                process.kill()
        psutil.wait_procs(targets, timeout=5)


def install(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> FakeAppServer:
    """Codex backendが起動するApp Serverを模擬App Serverへ差し替える。"""
    log_path = tmp_path / "fake-app-server.jsonl"
    monkeypatch.setattr(asyncio, "create_subprocess_exec", REAL_CREATE_SUBPROCESS_EXEC)
    monkeypatch.setattr(codex_rpc, "APP_SERVER_COMMAND", (sys.executable, str(FAKE_APP_SERVER), str(log_path)))
    return FakeAppServer(log_path)
