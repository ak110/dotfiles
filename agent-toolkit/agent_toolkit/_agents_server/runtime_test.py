"""共有状態の非同期操作で、待機中のloop進行とworker回収を確かめる。"""

import asyncio
import pathlib
import threading
from typing import Any

import pytest

from agent_toolkit._agents_server import manager, status_file, unavailable_candidates
from agent_toolkit._agents_server.shared_roots import StatusFileIdentity
from agent_toolkit._atk import config
from agent_toolkit._common import session_registry


@pytest.mark.parametrize("operation", ["activate", "claim", "notices", "candidates", "registry"])
@pytest.mark.parametrize("wait_kind", ["lock", "event", "join", "io"])
@pytest.mark.asyncio
async def test_blocking_waits_do_not_stall_event_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, operation: str, wait_kind: str
) -> None:
    """解放前に独立taskが進み、同期待機とloop状態操作のthreadが分かれる。"""
    entered, progressed, release = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    # 別threadが解放する試験用ロック。
    lock.acquire()  # pylint: disable=consider-using-with
    worker = threading.Thread(target=release.wait)
    worker.start()
    observed: list[bool] = []
    loop_thread = threading.get_ident()

    def blocking(*_args: Any, **_kwargs: Any) -> Any:
        entered.set()
        if wait_kind == "lock":
            with lock:
                pass
        elif wait_kind == "join":
            worker.join()
        else:
            release.wait()
        observed.append(progressed.is_set() and threading.get_ident() != loop_thread)
        return (None, None) if operation == "claim" else ([] if operation == "notices" else {})

    def controller() -> None:
        if entered.wait(5):
            progressed.wait(2)
        release.set()
        lock.release()

    controller_thread = threading.Thread(target=controller)
    controller_thread.start()

    async def heartbeat() -> None:
        await asyncio.to_thread(entered.wait, 5)
        progressed.set()

    heartbeat_task = asyncio.create_task(heartbeat())
    writer = status_file.StatusFileWriter({}, StatusFileIdentity("root", "writer.json", None), state_root=tmp_path)
    server = manager.AgentsServerManager(status_writer=None)
    try:
        if operation == "activate":
            monkeypatch.setattr(status_file, "sweep_stale_shared_state", blocking)
            await writer.activate_async()
        elif operation == "claim":
            monkeypatch.setattr(status_file, "take_result", blocking)
            await writer.take_result_async("child", collector="test")
        elif operation == "notices":
            monkeypatch.setattr(status_file, "take_notices", blocking)
            await writer.take_notices_async("child")
        elif operation == "candidates":
            monkeypatch.setattr(config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [("claude", None, None)])
            monkeypatch.setattr(unavailable_candidates, "load_unavailable_candidates", blocking)
            await server._resolve_start_candidates("plan", launch_kind="delegate")  # noqa: SLF001  # pylint: disable=protected-access
        else:
            monkeypatch.setattr(session_registry, "resolve", blocking)
            with pytest.raises(AttributeError):
                await server.show_session("child")
        await heartbeat_task
        assert observed == [True]
    finally:
        release.set()
        await asyncio.to_thread(worker.join)
        await asyncio.to_thread(controller_thread.join)
        await server.close()
        await writer.deactivate_async()
