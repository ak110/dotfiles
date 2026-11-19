"""解放要求の配送と所有・終了の再検収を確かめる。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import pathlib
import threading
import time
import types
from typing import Any

import pytest

from agent_toolkit._common import atomic_file, session_registry, session_release
from agent_toolkit._testing.session_release_support import held_registry_lock


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
    session_registry.publish(
        "child",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        cwd=str(tmp_path),
        processes=(owner,),
        state_root=tmp_path,
    )
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
                state=session_registry.PublicationState.RESULT_COMPLETED,
                cwd=str(tmp_path),
                turn_seq=2,
                processes=(owner,),
                state_root=tmp_path,
            )
        elif condition == "wrong-owner":
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            payload["owner"]["create_time"] += 1
            payload["owner"]["start_id"] = "other-generation"
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


@pytest.mark.asyncio
async def test_response_racing_requester_cleanup_is_not_left_behind(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """所有側が応答の公開に入ってから書き終えるまでの間に要求側が後始末を終えても、両者の終了後に要求と応答が残らない。

    2026-11-19のCI（run 44582845525）で、レーン再利用の解放後に応答だけが残った競合を固定する。
    要求の削除は所有側が応答の公開に入るまで待ち、応答の公開は要求側が応答の削除を終えるまで待つ。
    """
    owner = session_registry.owner_process()
    assert owner is not None
    session_registry.publish(
        "child",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        cwd=str(tmp_path),
        processes=(owner,),
        state_root=tmp_path,
    )
    requested, publishing, cleaned = threading.Event(), threading.Event(), threading.Event()
    original_write, original_unlink = atomic_file.atomic_write, pathlib.Path.unlink

    def write(path: pathlib.Path, content: str | bytes, **kwargs: Any) -> None:
        if path.name.endswith(".release-response"):
            publishing.set()
            assert cleaned.wait(timeout=10), "要求側が後始末を終えない"
        original_write(path, content, **kwargs)
        if path.name.endswith(".release-request"):
            requested.set()

    def unlink(self: pathlib.Path, *args: Any, **kwargs: Any) -> None:
        if self.name.endswith(".release-request"):
            assert publishing.wait(timeout=10), "所有側が応答の公開に入らない"
        original_unlink(self, *args, **kwargs)
        if self.name.endswith(".release-response"):
            cleaned.set()

    async def stop(session_id: str, *, retain_result: bool, **_kwargs: Any) -> dict[str, Any]:
        assert retain_result
        session_registry.release(session_id, reason="stopped", state_root=tmp_path)
        return {}

    monkeypatch.setattr(atomic_file, "atomic_write", write)
    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    task = asyncio.create_task(
        asyncio.to_thread(
            session_release.request_release, "child", tmp_path, deadline=time.monotonic() + 30, state_root=tmp_path
        )
    )
    try:
        assert await asyncio.to_thread(requested.wait, 10), "要求側が要求を公開しない"
        await session_release.process_requests({"child": _Session(True, str(tmp_path))}, stop, state_root=tmp_path)
        assert await task is None
        assert not list(session_registry.registry_directory(tmp_path).glob("*.release-*"))
    finally:
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("fault", ["read", "cleanup-request", "cleanup-response"])
@pytest.mark.parametrize("failing", [False, True])
@pytest.mark.asyncio
async def test_transient_sharing_denial_does_not_override_release_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, fault: str, failing: bool
) -> None:
    """Windowsの一時的な共有拒否が応答の最初の読取や要求側の後始末で一度起きても、所有側の結果（成功と具体的な失敗理由）を返す。

    要求側を応答の最初の読取の直前で止め、所有側が解放と応答の公開を終えてから読ませる。
    """
    owner = session_registry.owner_process()
    assert owner is not None
    session_registry.publish(
        "child",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        cwd=str(tmp_path),
        processes=(owner,),
        state_root=tmp_path,
    )
    denied: list[str] = []
    polling, answered = threading.Event(), threading.Event()
    original_read, original_unlink = pathlib.Path.read_text, pathlib.Path.unlink

    def read_text(self: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        if self.name.endswith(".release-response"):
            polling.set()
            assert answered.wait(timeout=10), "所有側が応答を公開しない"
            if fault == "read" and not denied:
                denied.append(self.name)
                raise PermissionError(13, "modeled WinError 32: sharing violation")
        return original_read(self, *args, **kwargs)

    def unlink(self: pathlib.Path, *args: Any, **kwargs: Any) -> None:
        suffix = ".release-request" if fault == "cleanup-request" else ".release-response"
        if fault.startswith("cleanup") and self.name.endswith(suffix) and not denied:
            denied.append(self.name)
            raise PermissionError(13, "modeled WinError 5: access denied")
        original_unlink(self, *args, **kwargs)

    async def stop(session_id: str, **_kwargs: Any) -> dict[str, Any]:
        if failing:
            raise OSError("解放に失敗")
        session_registry.release(session_id, reason="stopped", state_root=tmp_path)
        return {}

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    task = asyncio.create_task(
        asyncio.to_thread(
            session_release.request_release, "child", tmp_path, deadline=time.monotonic() + 30, state_root=tmp_path
        )
    )
    try:
        assert await asyncio.to_thread(polling.wait, 10), "要求側が応答を読まない"
        await session_release.process_requests({"child": _Session(True, str(tmp_path))}, stop, state_root=tmp_path)
        answered.set()
        reason = await task
        if failing:
            assert reason is not None and "解放に失敗" in reason
        else:
            assert reason is None
        assert denied
        assert not list(session_registry.registry_directory(tmp_path).glob("*.release-*"))
    finally:
        answered.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_persistent_read_denial_is_reported_with_path_and_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """応答の読取拒否が期限まで続けば、期限切れだけでなく応答のパスと最後のOSエラーを報告し、要求と応答を残さない。

    所有側は要求の公開の直後に応答を公開し終え、要求側の期限は模擬した時計で進めて実時間に依らせない。
    """
    owner = session_registry.owner_process()
    assert owner is not None
    session_registry.publish(
        "child",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        cwd=str(tmp_path),
        processes=(owner,),
        state_root=tmp_path,
    )
    published: list[pathlib.Path] = []
    loop = asyncio.get_running_loop()
    original_read, original_write = pathlib.Path.read_text, atomic_file.atomic_write

    def read_text(self: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        if self.name.endswith(".release-response"):
            raise PermissionError(13, "modeled permanent EACCES", str(self))
        return original_read(self, *args, **kwargs)

    async def stop(_session_id: str, **_kwargs: Any) -> dict[str, Any]:
        raise OSError("解放に失敗")

    def write(path: pathlib.Path, content: str | bytes, **kwargs: Any) -> None:
        original_write(path, content, **kwargs)
        if path.name.endswith(".release-response"):
            published.append(path)
        elif path.name.endswith(".release-request"):
            sessions = {"child": _Session(True, str(tmp_path))}
            asyncio.run_coroutine_threadsafe(
                session_release.process_requests(sessions, stop, state_root=tmp_path), loop
            ).result(timeout=10)

    ticks = iter(range(10_000))
    clock = types.SimpleNamespace(monotonic=lambda: float(next(ticks)), sleep=lambda _seconds: None)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    monkeypatch.setattr(atomic_file, "atomic_write", write)
    monkeypatch.setattr(session_release, "time", clock)
    reason = await asyncio.to_thread(session_release.request_release, "child", tmp_path, deadline=30.0, state_root=tmp_path)
    assert published, "所有側が応答を公開しない"
    assert reason is not None and "期限までに" in reason
    assert str(published[0]) in reason and "modeled permanent EACCES" in reason
    assert not list(session_registry.registry_directory(tmp_path).glob("*.release-*"))


@pytest.mark.asyncio
async def test_response_during_registry_write_does_not_delay_release_of_other_sessions(tmp_path: pathlib.Path) -> None:
    """別プロセスが1件のsessionの登録簿を書き込み中でも、所有側はその応答を返し、同じ監視が扱う別sessionをロックの保持中に解放する。"""
    owner = session_registry.owner_process()
    assert owner is not None
    for session_id in ("busy", "idle"):
        session_registry.publish(
            session_id,
            state=session_registry.PublicationState.RESULT_COMPLETED,
            cwd=str(tmp_path),
            processes=(owner,),
            state_root=tmp_path,
        )
    handled = asyncio.Event()

    async def stop(session_id: str, *, retain_result: bool, **_kwargs: Any) -> dict[str, Any]:
        assert retain_result
        if session_id == "busy":
            handled.set()
            raise OSError("解放に失敗")
        session_registry.release(session_id, reason="stopped", state_root=tmp_path)
        return {}

    sessions = {session_id: _Session(True, str(tmp_path)) for session_id in ("busy", "idle")}
    stopping = asyncio.Event()

    async def monitor() -> None:
        while not stopping.is_set():
            await session_release.process_requests(sessions, stop, state_root=tmp_path)
            await asyncio.sleep(0.01)

    monitoring = asyncio.create_task(monitor())
    busy: asyncio.Task[str | None] | None = None
    try:
        with held_registry_lock("busy", state_root=tmp_path):
            busy = asyncio.create_task(
                asyncio.to_thread(
                    session_release.request_release, "busy", tmp_path, deadline=time.monotonic() + 10, state_root=tmp_path
                )
            )
            async with asyncio.timeout(10):
                await handled.wait()
            idle = await asyncio.to_thread(
                session_release.request_release, "idle", tmp_path, deadline=time.monotonic() + 10, state_root=tmp_path
            )
        assert idle is None
        assert "解放に失敗" in (await busy or "")
    finally:
        stopping.set()
        await monitoring
        if busy is not None:
            await asyncio.gather(busy, return_exceptions=True)
    assert not list(session_registry.registry_directory(tmp_path).glob("*.release-*"))


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
    session_registry.publish(
        "child", state=session_registry.PublicationState.RESULT_COMPLETED, cwd=str(cwd), processes=(owner,), state_root=tmp_path
    )
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
            assert reason and "要求後に所有または終了状態が変わった" in reason
            assert not calls
        else:
            assert reason is None and calls == ["child"]
    finally:
        await task
