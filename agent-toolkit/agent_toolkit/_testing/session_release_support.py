"""レーン回収試験の実cwd保持子と所有managerの寿命、結果の受領と再開、別プロセスによる登録簿の書込の滞りを扱う。"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import pathlib
import subprocess
import sys
import threading
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest

from agent_toolkit._agents_server import agents_wait, shared_roots, status_file
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._common import process_tree, session_registry, state_paths
from agent_toolkit._testing.agents_server_support import FakeBackend, _complete, install_backend


@dataclasses.dataclass
class RetainedExecutor:
    """回収試験が検収する識別・実プロセス・解放記録と、公開結果操作をまとめる。"""

    session_id: str
    state_root: pathlib.Path
    executor: subprocess.Popen[str]
    release_calls: list[str]
    _manager: server_manager.AgentsServerManager = dataclasses.field(repr=False)

    def wait_result(self) -> int:
        """元の所有rootから公開待機操作で未回収結果を受領する。"""
        return agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "release-root"},
            state_root=self.state_root,
        )

    async def resume(self, prompt: str) -> dict[str, Any]:
        """同じsessionへの公開send_messageで会話を再開する。"""
        return await self._manager.send_message(self.session_id, prompt)


@contextlib.asynccontextmanager
async def retained_executor(
    worktree: pathlib.Path,
    environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    keep_executor: bool = False,
) -> AsyncGenerator[RetainedExecutor]:
    """終了時の結果を持つ実cwd保持子を準備し、所有監視と全資源の終了まで引き受ける。"""
    root = state_paths.state_dir()
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("release-root", "root.json", None),
        state_root=root,
        aggregate_seconds=0,
    )
    with process_tree.process_scope(
        [sys.executable, "-c", "import sys;print('ready',flush=True);sys.stdin.read()"],
        cwd=worktree,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as executor:
        async with server_manager.AgentsServerManager(writer) as manager:
            backend = FakeBackend(manager.sessions, "claude")
            install_backend(manager, "claude", backend)
            assert executor.stdout is not None and executor.stdout.readline().strip() == "ready"
            session = await backend.start("担当", str(worktree), None, None)
            session.publish_registry = True
            session.announced = True
            session.executor_process = session_registry.ProcessIdentity.of(executor.pid)
            _complete(session, message="回収後も受領する結果")
            await session.drain_registration()

            async def release(owned_session: SessionState, **_kwargs: Any) -> None:
                assert executor is not None
                if not keep_executor:
                    await asyncio.to_thread(executor.communicate, input="", timeout=5)
                backend.release_calls.append(owned_session.session_id)

            monkeypatch.setattr(backend, "release_session", release)
            await manager.activate_async()
            yield RetainedExecutor(session.session_id, root, executor, backend.release_calls, manager)


@contextlib.contextmanager
def held_registry_lock(session_id: str, *, state_root: pathlib.Path | None = None) -> Generator[None]:
    """別プロセスが登録簿を書いている間のように、ブロックの間だけsessionの登録簿ロックを別threadで保持する。

    ロックを待つ処理が試験を止めたままにしないよう、自動終了の10秒の上限を越える15秒で手放し、その場合は不合格にする。
    """
    acquired = threading.Event()
    finished = threading.Event()
    expired = threading.Event()

    def hold() -> None:
        with session_registry.registry_lock(session_id, state_root=state_root):
            acquired.set()
            if not finished.wait(timeout=15):
                expired.set()

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    try:
        assert acquired.wait(timeout=5)
        yield
    finally:
        finished.set()
        holder.join(timeout=5)
    assert not expired.is_set(), "ブロックが15秒以内に終わらず、登録簿ロックの解放を待った"
