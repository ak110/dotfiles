"""Codexのrelease_processを検証する。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import sys
from collections.abc import Iterable
from typing import Any

import psutil
import pytest

from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import codex_rpc
from agent_toolkit._testing import codex_app_server_support, late_descendant
from agent_toolkit._testing.codex_backend_support import _fake_app_server_manager, _wait_terminal

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


@pytest.mark.skipif(os.name == "nt", reason="POSIXのSIGTERM無視と実子孫回収を観測する")
@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_close_reclaims_app_server_and_mcp_when_reader_and_term_stall(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """readerが取消しを無視し、App ServerがTERMを無視しても所有MCPの回収まで終了する。"""
    wrapper = tmp_path / "stubborn_app_server.py"
    wrapper.write_text(
        "import runpy, signal, sys\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n",
        encoding="utf-8",
    )
    async with _fake_app_server_manager(tmp_path, monkeypatch) as (manager, backend, fake):
        monkeypatch.setattr(
            codex_rpc,
            "APP_SERVER_COMMAND",
            (
                sys.executable,
                str(wrapper),
                str(codex_app_server_support.FAKE_APP_SERVER),
                str(fake.log_path),
            ),
        )
        session_id = (await manager.start("plan", "停止試験", str(tmp_path)))["session_id"]
        await _wait_terminal(manager, session_id, fake)
        server_pid, mcp_pids = fake.server_pid(session_id), fake.mcp_pids(session_id)
        assert fake.alive(server_pid) and mcp_pids
        client = backend._clients[session_id]
        original_reader = client._reader_task
        assert original_reader is not None
        original_reader.cancel()
        await asyncio.gather(original_reader, return_exceptions=True)
        release = asyncio.Event()

        async def stuck_reader() -> None:
            while not release.is_set():
                with contextlib.suppress(asyncio.CancelledError):
                    await release.wait()

        reader = asyncio.create_task(stuck_reader())
        client._reader_task = reader
        wait = subject.lifetime.wait_for_shutdown

        async def bounded_wait(tasks: Iterable[asyncio.Task[Any]], *, timeout: float = 15) -> list[Any]:
            return await wait(tasks, timeout=0.05 if timeout == 15 else timeout)

        monkeypatch.setattr(subject.lifetime, "wait_for_shutdown", bounded_wait)
        try:
            await asyncio.wait_for(manager.stop(session_id), 20)
            assert not fake.alive(server_pid)
            assert not any(fake.alive(pid) for pid in mcp_pids)
        finally:
            release.set()
            await reader


@pytest.mark.windows
@pytest.mark.parametrize("snapshot_has_child", [False, True] if sys.platform == "win32" else [False])
@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_stop_reclaims_late_descendant_with_short_lived_parent(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, snapshot_has_child: bool
) -> None:
    """短命な親から生まれた子を、親の終了と列挙時機によらず回収する。"""
    work = tmp_path / "late"
    work.mkdir()
    async with _fake_app_server_manager(tmp_path, monkeypatch) as (manager, backend, fake):
        monkeypatch.setattr(
            codex_rpc,
            "APP_SERVER_COMMAND",
            tuple(late_descendant.cli_command(work, str(codex_app_server_support.FAKE_APP_SERVER), str(fake.log_path))),
        )
        session_id = (await manager.start("plan", "停止試験", str(tmp_path)))["session_id"]
        await _wait_terminal(manager, session_id, fake)
        await asyncio.to_thread(late_descendant.wait_spawner, work)
        if sys.platform == "win32":
            # 実snapshotへ親子を含めた後、handle保持より先に親を終了させる。
            # 対照版の列挙だけへ同じsnapshotを渡し、Job版では列挙なしの所属回収を検収する。
            spawner = psutil.Process(int((work / "spawner.pid").read_text(encoding="utf-8")))
            client_process = backend._clients[session_id].process
            assert client_process is not None
            ancestors = spawner.parents()
            root_index = next(index for index, process in enumerate(ancestors) if process.pid == client_process.pid)
            pins = subject.process_tree.ProcessIdentityPins()
            request.addfinalizer(pins.close)
            # venvの中継親で先に保持失敗しないよう、制御対象以外の既存世代だけを固定する。
            for process in ancestors[:root_index]:
                assert pins.retain(process)
            (work / "retain-spawner").touch()
            if snapshot_has_child:
                await asyncio.to_thread(late_descendant.trigger, work)
            snapshot = list(psutil.process_iter(["ppid"]))
            parent = next(process for process in snapshot if process.pid == spawner.pid)
            parent_created = parent.create_time()
            # Windowsのvenvランチャーがholderの中間に入る場合も、snapshot上の直接の子を確認する。
            snapshot_children = [process.pid for process in snapshot if process.info["ppid"] == parent.pid]
            assert bool(snapshot_children) is snapshot_has_child
            if not snapshot_has_child:
                await asyncio.to_thread(late_descendant.trigger, work)
            holder_pid = int((work / "holder.pid").read_text(encoding="utf-8").split()[0])
            (work / "exit-spawner").touch()
            await asyncio.to_thread(spawner.wait, timeout=10)
            assert not spawner.is_running()
            process_iter = psutil.process_iter
            replayed = False

            def snapshot_before_retention(attrs=None, **kwargs):
                nonlocal replayed
                if attrs == ["ppid"] and not replayed:
                    replayed = True
                    return iter(snapshot)
                return process_iter(attrs, **kwargs)

            monkeypatch.setattr(psutil, "process_iter", snapshot_before_retention)
            print(
                f"retention_boundary: parent={parent.pid} created={parent_created} "
                f"snapshot_children={snapshot_children} holder={holder_pid} parent_gone=true"
            )
        else:
            late_descendant.arm(work, monkeypatch.setattr)
        try:
            await asyncio.wait_for(manager.stop(session_id), 60)
        finally:
            # 対照版が失敗した場合も、試験自身のholderを確認後に回収する。
            await asyncio.to_thread(late_descendant.assert_released, work)
