"""解放要求の配送と所有・終端の再検収を確かめる。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import pathlib
import time
from typing import Any

import pytest

from agent_toolkit._common import session_registry, session_release


@dataclasses.dataclass
class _Session:
    terminal: bool
    cwd: str


@pytest.mark.parametrize(
    "condition",
    [
        "running",
        "changed-turn",
        "changed-cwd",
        "wrong-owner",
        "not-owned",
        "already-released",
        "failed",
        "residual",
        "released",
    ],
)
@pytest.mark.asyncio
async def test_release_request_rechecks_owner_and_turn_and_preserves_failures(tmp_path: pathlib.Path, condition: str) -> None:
    """要求後の再開・所有変更を解放せず、解放失敗・残存資源と成功を配送側が区別する。"""
    owner = session_registry.owner_process()
    assert owner is not None
    session_registry.publish("child", terminal=True, cwd=str(tmp_path), processes=(owner,), state_root=tmp_path)
    session = _Session(terminal=True, cwd=str(tmp_path))
    task = asyncio.create_task(
        asyncio.to_thread(
            session_release.request_release,
            "child",
            tmp_path,
            deadline=time.monotonic() + 1,
            state_root=tmp_path,
        )
    )
    calls: list[bool] = []
    try:
        async with asyncio.timeout(2):
            while not (paths := list(session_registry.registry_directory(tmp_path).glob("*.release-request"))):
                await asyncio.sleep(0.001)
        if condition == "running":
            session.terminal = False
        elif condition == "changed-cwd":
            session.cwd = str(tmp_path.parent)
        elif condition == "changed-turn":
            session_registry.publish(
                "child",
                terminal=True,
                cwd=str(tmp_path),
                turn_seq=2,
                processes=(owner,),
                state_root=tmp_path,
            )
        elif condition == "wrong-owner":
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            payload["owner"]["create_time"] += 1
            paths[0].write_text(json.dumps(payload), encoding="utf-8")
        elif condition == "already-released":
            session_registry.release("child", reason="retention_expired", state_root=tmp_path)

        async def stop(_session_id: str, *, retain_result: bool, **_kwargs: Any) -> dict[str, Any]:
            calls.append(retain_result)
            if condition == "failed":
                raise OSError("解放に失敗")
            if condition != "residual":
                session_registry.release("child", reason="stopped", state_root=tmp_path)
            return {}

        await session_release.process_requests(
            {} if condition in {"not-owned", "already-released"} else {"child": session},
            stop,
            state_root=tmp_path,
        )
        reason = await task
        if condition in {"released", "already-released"}:
            assert reason is None and calls == ([True] if condition == "released" else [])
        else:
            assert reason
            assert session_registry.resolve("child", state_root=tmp_path).state is session_registry.Resolution.TERMINAL
            assert calls == ([True] if condition in {"failed", "residual"} else [])
        assert not list(session_registry.registry_directory(tmp_path).glob("*.release-*"))
    finally:
        await task


@pytest.mark.parametrize("registered_paths", [False, True])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.asyncio
async def test_process_requests_rejects_nested_worktree_cwd(
    tmp_path: pathlib.Path, registered_paths: bool, nested: bool
) -> None:
    """新要求は最内側の所属で判定し、登録集合を持たない旧要求も受理できる。"""
    owner = session_registry.owner_process()
    assert owner is not None
    cwd = tmp_path / "nested" if nested else tmp_path
    session_registry.publish("child", terminal=True, cwd=str(cwd), processes=(owner,), state_root=tmp_path)
    task = asyncio.create_task(
        asyncio.to_thread(
            session_release.request_release,
            "child",
            tmp_path,
            deadline=time.monotonic() + 2,
            state_root=tmp_path,
        )
    )
    calls: list[str] = []

    async def stop(session_id: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(session_id)
        session_registry.release(session_id, reason="stopped", state_root=tmp_path)
        return {}

    try:
        async with asyncio.timeout(3):
            while not (requests := list(session_registry.registry_directory(tmp_path).glob("*.release-request"))):
                await asyncio.sleep(0.001)
        if registered_paths:
            payload = json.loads(requests[0].read_text(encoding="utf-8"))
            payload["worktree_paths"] = [str(tmp_path), str(tmp_path / "nested")]
            requests[0].write_text(json.dumps(payload), encoding="utf-8")
        await session_release.process_requests({"child": _Session(True, str(cwd))}, stop, state_root=tmp_path)
        reason = await task
        if registered_paths and nested:
            assert reason and "要求後に所有または終端状態が変わった" in reason
            assert not calls
        else:
            assert reason is None and calls == ["child"]
    finally:
        await task
