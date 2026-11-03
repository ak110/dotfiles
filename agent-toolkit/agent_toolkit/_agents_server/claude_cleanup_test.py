"""SDK切断中の動的な実子孫と、正常終了・残存再試行の所有契約を検証する。"""

from __future__ import annotations

import asyncio
import contextlib
import pathlib
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from agent_toolkit._agents_server import claude
from agent_toolkit._common import process_tree
from agent_toolkit._testing.agents_server_support import QueueClaudeClient, ResultMessage, SystemMessage

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize(
    "outcome", ["normal", "connect_failure", "initialization_failure", "timeout", "cancel", "residual", "unresolved"]
)
@pytest.mark.asyncio
async def test_disconnect_tracks_new_descendant_until_parent_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, host_environ, outcome: str
) -> None:
    """一覧取得後に子を生成して親を終了させ、観測済みの子と無関係なプロセスを区別する。"""
    script = tmp_path / "runtime.py"
    child_pid = tmp_path / "child.pid"
    finished = tmp_path / "normal-finish"
    script.write_text(
        "import pathlib, subprocess, sys\n"
        "assert sys.stdin.readline().strip() == 'spawn'\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "assert sys.stdin.readline().strip() == 'exit'\n"
        "pathlib.Path(sys.argv[2]).write_text('finished')\n",
        encoding="utf-8",
    )
    first_snapshot = asyncio.Event()
    child_observed = asyncio.Event()
    disconnect_entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    collect = claude._owned_runtime_processes  # pylint: disable=protected-access

    def observed_collect(root, known=(), **_kwargs):
        targets = collect(root, known) if known else collect(root)
        loop.call_soon_threadsafe(first_snapshot.set)
        if child_pid.exists() and int(child_pid.read_text(encoding="utf-8")) in {target.pid for target in targets}:
            loop.call_soon_threadsafe(child_observed.set)
        return targets

    monkeypatch.setattr(claude, "_owned_runtime_processes", observed_collect)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(claude, "_DISCONNECT_TIMEOUT", 0.8)
    targets: list[psutil.Process] = []
    with (
        process_tree.process_scope(
            [sys.executable, str(script), str(child_pid), str(finished)], stdin=subprocess.PIPE, env=host_environ()
        ) as process,
        process_tree.process_scope([sys.executable, "-c", "import time; time.sleep(60)"], env=host_environ()) as foreign,
    ):

        class DisconnectClient(QueueClaudeClient):
            _transport: Any

            async def connect(self) -> None:
                await super().connect()
                if outcome == "connect_failure":
                    raise RuntimeError("初期化の失敗")

            async def query(self, prompt: str) -> None:
                if outcome == "initialization_failure":
                    raise RuntimeError("初期化の失敗")
                await super().query(prompt)

            async def disconnect(self) -> None:
                disconnect_entered.set()
                await asyncio.wait_for(first_snapshot.wait(), 2)
                assert process.stdin is not None
                process.stdin.write(b"spawn\n")
                process.stdin.flush()
                async with asyncio.timeout(2):
                    while not child_pid.exists():
                        await asyncio.sleep(0.005)
                targets.append(psutil.Process(int(child_pid.read_text(encoding="utf-8"))))
                # 修正前の単発列挙でも親終了まで進め、残留そのものを後段で検出する。
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(child_observed.wait(), 0.3)
                process.stdin.write(b"exit\n")
                process.stdin.flush()
                await asyncio.to_thread(process.wait, timeout=2)
                self.disconnected = True
                if outcome == "timeout":
                    await asyncio.Event().wait()

        client = DisconnectClient([[SystemMessage("cleanup-session"), ResultMessage("完了")]])
        client._transport = SimpleNamespace(_process=process)  # pylint: disable=protected-access
        backend = claude.ClaudeServerManager({}, client_factory=lambda _options: client)
        real_stop = process_tree.stop_tree
        fail_reclaim = outcome in {"residual", "unresolved"}

        def reclaim(root, **kwargs):
            if fail_reclaim:
                known = list(kwargs["known"])
                return process_tree.TreeStopResult(
                    known,
                    known if outcome == "residual" else [],
                    ["生成の停止を確認できない"] if outcome == "unresolved" else [],
                )
            return real_stop(root, **kwargs)

        monkeypatch.setattr(process_tree, "stop_tree", reclaim)
        try:
            if outcome in {"initialization_failure", "connect_failure"}:
                with pytest.raises(RuntimeError, match="初期化の失敗"):
                    await backend.start("失敗する初期化", str(tmp_path))
            else:
                session = await backend.start("終了試験", str(tmp_path))
                release = asyncio.create_task(backend.release_session(session))
                try:
                    await asyncio.wait_for(disconnect_entered.wait(), 2)
                    if outcome == "cancel":
                        release.cancel()
                    if outcome in {"residual", "unresolved"}:
                        with pytest.raises(RuntimeError, match="所有資源"):
                            await release
                        assert targets[0].is_running()
                        fail_reclaim = False
                        await backend.release_session(session)
                    else:
                        await asyncio.gather(release, return_exceptions=outcome == "cancel")
                        if outcome == "cancel":
                            assert release.cancelled()
                finally:
                    if not release.done():
                        release.cancel()
                    await asyncio.gather(release, return_exceptions=True)
            await backend.close()
            assert finished.read_text(encoding="utf-8") == "finished"
            assert client.disconnected
            assert targets and all(
                not target.is_running() or target.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD) for target in targets
            )
            assert child_observed.is_set()
            assert foreign.poll() is None
        finally:
            fail_reclaim = False
            await backend.close()
            await asyncio.to_thread(process_tree.reclaim_processes, targets, timeout=0.5)
