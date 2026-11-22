from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import pathlib
import threading
import time
import types
from typing import Any

import pytest

from agent_toolkit._common import atomic_file, registry_requests, session_registry, session_release
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
        request = paths[0]
        result = request.with_suffix(".release-request-result")
        unanswered = condition in {"wrong-owner", "not-owned"}
        retained = request if unanswered else result
        assert list(request.parent.glob("*.release-*")) == [retained]
        packet = json.loads(retained.read_text(encoding="utf-8"))
        assert packet["request_id"] == request.stem
        if unanswered:
            assert "result" not in packet
        else:
            assert packet["result"]["session_id"] == "child"
            assert packet["result"]["record_digest"] == packet["record_digest"]
    finally:
        await task


@pytest.mark.asyncio
async def test_response_racing_legacy_requester_cleanup_is_not_left_behind(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = session_registry.owner_process()
    assert owner is not None
    session_registry.publish(
        "child",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        cwd=str(tmp_path),
        processes=(owner,),
        state_root=tmp_path,
    )
    directory = session_registry.registry_directory(tmp_path)
    request = directory / "legacy.release-request"
    response = request.with_suffix(".release-response")
    request.write_text(
        json.dumps(
            {
                "version": 1,
                "session_id": "child",
                "worktree": str(tmp_path),
                "record_digest": hashlib.sha256((directory / "child.json").read_bytes()).hexdigest(),
                "owner": owner.payload(legacy=True),
                "owner_identity": owner.payload(),
                "deadline": time.monotonic() + 30,
            }
        ),
        encoding="utf-8",
    )
    publishing, cleaned = threading.Event(), threading.Event()
    original_write = atomic_file.atomic_write

    def write(path: pathlib.Path, content: str | bytes, **kwargs: Any) -> None:
        if path == response:
            publishing.set()
            assert cleaned.wait(timeout=10), "要求側が後始末を終えない"
        original_write(path, content, **kwargs)

    def cleanup() -> None:
        assert publishing.wait(timeout=10), "所有側が応答の公開に入らない"
        request.unlink()
        response.unlink(missing_ok=True)
        cleaned.set()

    async def stop(session_id: str, *, retain_result: bool, **_kwargs: Any) -> dict[str, Any]:
        assert retain_result
        session_registry.release(session_id, reason="stopped", state_root=tmp_path)
        return {}

    monkeypatch.setattr(atomic_file, "atomic_write", write)
    task = asyncio.get_running_loop().run_in_executor(None, cleanup)
    try:
        await session_release.process_requests({"child": _Session(True, str(tmp_path))}, stop, state_root=tmp_path)
        await task
        assert not list(directory.glob("*.release-*"))
    finally:
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("failing", [False, True])
@pytest.mark.asyncio
async def test_transient_sharing_denial_does_not_override_release_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failing: bool
) -> None:
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
    attempts: list[pathlib.Path] = []
    polling, answered = threading.Event(), threading.Event()
    original_read = pathlib.Path.read_text

    def read_text(self: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        if self.name.endswith(".release-request-result"):
            attempts.append(self)
            polling.set()
            assert answered.wait(timeout=10), "所有側が応答を公開しない"
            if not denied:
                denied.append(self.name)
                raise PermissionError(13, "modeled WinError 32: sharing violation")
        return original_read(self, *args, **kwargs)

    async def stop(session_id: str, **_kwargs: Any) -> dict[str, Any]:
        if failing:
            raise OSError("解放に失敗")
        session_registry.release(session_id, reason="stopped", state_root=tmp_path)
        return {}

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
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
            assert len(attempts) >= 2
        else:
            assert reason is None
        (result,) = session_registry.registry_directory(tmp_path).glob("*.release-*")
        assert denied == [result.name]
        assert result.suffix == ".release-request-result"
    finally:
        answered.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_persistent_read_denial_is_reported_with_path_and_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    original_read, original_write = pathlib.Path.read_text, registry_requests.write_request

    def read_text(self: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        if self.name.endswith(".release-request-result"):
            raise PermissionError(13, "modeled permanent EACCES", str(self))
        return original_read(self, *args, **kwargs)

    async def stop(_session_id: str, **_kwargs: Any) -> dict[str, Any]:
        raise OSError("解放に失敗")

    def write(path: pathlib.Path, packet: dict[str, Any]) -> None:
        original_write(path, packet)
        sessions = {"child": _Session(True, str(tmp_path))}
        asyncio.run_coroutine_threadsafe(session_release.process_requests(sessions, stop, state_root=tmp_path), loop).result(
            timeout=10
        )
        published.append(path.with_suffix(".release-request-result"))

    started = time.monotonic()
    ticks = iter(range(10_000))
    clock = types.SimpleNamespace(monotonic=lambda: started + next(ticks), sleep=lambda _seconds: None)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    monkeypatch.setattr(registry_requests, "write_request", write)
    monkeypatch.setattr(session_release, "time", clock)
    reason = await asyncio.to_thread(
        session_release.request_release, "child", tmp_path, deadline=started + 30, state_root=tmp_path
    )
    assert published, "所有側が応答を公開しない"
    assert reason is not None and "期限までに" in reason
    assert str(published[0]) in reason and "modeled permanent EACCES" in reason
    assert list(session_registry.registry_directory(tmp_path).glob("*.release-*")) == published
    assert json.loads(original_read(published[0], encoding="utf-8"))["result"]["error"] == "解放に失敗"


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
    files = list(session_registry.registry_directory(tmp_path).glob("*.release-*"))
    assert len(files) == 2 and all(path.suffix == ".release-request-result" for path in files)
    results = [json.loads(path.read_text(encoding="utf-8"))["result"] for path in files]
    assert {result["session_id"]: result.get("error") for result in results} == {"busy": "解放に失敗", "idle": None}


@pytest.mark.parametrize("registered_paths", [False, True])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.asyncio
async def test_process_requests_rejects_nested_worktree_cwd(
    tmp_path: pathlib.Path, registered_paths: bool, nested: bool
) -> None:
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
            worktree_paths=(tmp_path, tmp_path / "nested") if registered_paths else None,
        )
    )
    calls: list[str] = []

    async def stop(session_id: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(session_id)
        session_registry.release(session_id, reason="stopped", state_root=tmp_path)
        return {}

    try:
        async with asyncio.timeout(3):
            while next(session_registry.registry_directory(tmp_path).glob("*.release-request"), None) is None:
                await asyncio.sleep(0.001)
        await session_release.process_requests({"child": _Session(True, str(cwd))}, stop, state_root=tmp_path)
        reason = await task
        if registered_paths and nested:
            assert reason and "要求後に所有または終了状態が変わった" in reason
            assert not calls
        else:
            assert reason is None and calls == ["child"]
    finally:
        await task
