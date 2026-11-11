"""SDK切断中の動的な実子孫と、正常終了・残存再試行の所有契約を検証する。"""

from __future__ import annotations

import asyncio
import contextlib
import pathlib
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from agent_toolkit._agents_server import claude, claude_runtime, claude_stream
from agent_toolkit._common import process_tree
from agent_toolkit._testing import late_descendant
from agent_toolkit._testing.agents_server_support import QueueClaudeClient, ResultMessage, SystemMessage

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize(
    ("outcome", "delay_collection"),
    [
        *(
            pytest.param(name, False, id=name)
            for name in ("normal", "connect_failure", "initialization_failure", "timeout", "cancel", "residual", "unresolved")
        ),
        pytest.param("timeout", True, id="timeout-delayed-collection"),
        pytest.param("residual", True, id="residual-delayed-collection"),
    ],
)
@pytest.mark.asyncio
async def test_disconnect_tracks_new_descendant_until_parent_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, host_environ, outcome: str, delay_collection: bool
) -> None:
    """一覧取得後に子を生成して親を終了させ、観測済みの子と無関係なプロセスを区別する。"""
    script = tmp_path / "runtime.py"
    child_pid = tmp_path / "child.pid"
    finished = tmp_path / "normal-finish"
    runtime_ready = tmp_path / "runtime-ready"
    script.write_text(
        "import pathlib, subprocess, sys\n"
        "pathlib.Path(sys.argv[3]).write_text('ready')\n"
        "assert sys.stdin.readline().strip() == 'spawn'\n"
        f"child = subprocess.Popen([sys.executable, '-c', {late_descendant.held_source(tmp_path / 'nested.release')!r}])\n"
        "pending_pid = pathlib.Path(sys.argv[1]).with_suffix('.tmp')\n"
        "pending_pid.write_text(str(child.pid))\n"
        "pending_pid.replace(sys.argv[1])\n"
        "assert sys.stdin.readline().strip() == 'exit'\n"
        "pathlib.Path(sys.argv[2]).write_text('finished')\n",
        encoding="utf-8",
    )
    first_snapshot = asyncio.Event()
    child_observed = asyncio.Event()
    disconnect_entered = asyncio.Event()
    disconnect_prepared = asyncio.Event()
    disconnect_cancelled = asyncio.Event()
    loop = asyncio.get_running_loop()
    collect = claude._owned_runtime_processes  # pylint: disable=protected-access
    collection_delayed = False

    def observed_collect(root, known=(), **_kwargs):
        nonlocal collection_delayed
        if delay_collection and first_snapshot.is_set() and not collection_delayed:
            collection_delayed = True
            # 短い切断期限を超える列挙遅延をworker側で作り、準備と切断待ちの境界を検証する。
            time.sleep(1)
        targets = collect(root, known, **_kwargs)
        loop.call_soon_threadsafe(first_snapshot.set)
        if child_pid.exists() and int(child_pid.read_text(encoding="utf-8")) in {target.pid for target in targets}:
            loop.call_soon_threadsafe(child_observed.set)
        return targets

    monkeypatch.setattr(claude_stream, "_owned_runtime_processes", observed_collect)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(claude_runtime, "_DISCONNECT_TIMEOUT", 0.8)
    wait = asyncio.wait

    async def wait_after_preparation(tasks, *, timeout=None, **kwargs):
        if timeout == 0.8:
            await asyncio.wait_for(disconnect_prepared.wait(), 15)
        return await wait(tasks, timeout=timeout, **kwargs)

    # 本物のasyncio.waitによる切断timeoutは維持し、fixtureの生成・観測・親終了だけを予算から分離する。
    monkeypatch.setattr(claude.asyncio, "wait", wait_after_preparation)
    targets: list[psutil.Process] = []
    with (
        process_tree.process_scope(
            [sys.executable, str(script), str(child_pid), str(finished), str(runtime_ready)],
            stdin=subprocess.PIPE,
            env=host_environ(),
        ) as process,
        late_descendant.held_process(tmp_path, env=host_environ()) as foreign,
    ):
        # Pythonの起動準備をSDK切断の猶予に含めず、負荷による準備の遅れと切断timeoutを分ける。
        async with asyncio.timeout(5):
            while not runtime_ready.exists():
                assert process.poll() is None, "runtimeが子生成の準備前に終了した"
                await asyncio.sleep(0.005)

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
                # POSIXでは観測前に親が終了した子を辿れないため、観測済みという試験の前提を確定する。
                await asyncio.wait_for(child_observed.wait(), 15)
                process.stdin.write(b"exit\n")
                process.stdin.flush()
                await asyncio.to_thread(process.wait, timeout=2)
                self.disconnected = True
                disconnect_prepared.set()
                if outcome == "timeout":
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        disconnect_cancelled.set()
                        raise

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
                        assert targets, "残存の判定前に子PIDの登録へ到達していない"
                        assert targets[0].is_running()
                        fail_reclaim = False
                        if outcome == "unresolved":
                            # 生成停止の未確認は再試行で解消しない。既知の子は回収しても、再開の停止を伝え続ける。
                            with pytest.raises(RuntimeError, match="生成の停止を確認できない"):
                                await backend.release_session(session)
                        else:
                            await backend.release_session(session)
                    else:
                        await asyncio.gather(release, return_exceptions=outcome == "cancel")
                        if outcome == "cancel":
                            assert release.cancelled()
                finally:
                    if not release.done():
                        release.cancel()
                    await asyncio.gather(release, return_exceptions=True)
            if outcome == "unresolved":
                with pytest.raises(RuntimeError, match="生成の停止を確認できない"):
                    await backend.close()
            else:
                await backend.close()
            assert finished.read_text(encoding="utf-8") == "finished"
            assert client.disconnected
            assert targets and all(
                not target.is_running() or target.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD) for target in targets
            )
            assert child_observed.is_set()
            if outcome == "timeout":
                assert disconnect_cancelled.is_set()
            assert foreign.poll() is None
        finally:
            fail_reclaim = False
            with contextlib.suppress(RuntimeError):
                await backend.close()
            await asyncio.to_thread(process_tree.reclaim_processes, targets, timeout=0.5)
