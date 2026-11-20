"""保存の待機中に始まった登録公開を待ってから、世代間の状態を写す。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import pathlib
from typing import Any, cast

import pytest

from agent_toolkit._agents_server import service_projection, service_state, service_transport, shared_roots, state
from agent_toolkit._agents_server.manager import AgentsServerManager
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._common import session_layout


class _Codec:
    def encode(self, value: Any) -> Any:
        return value


class _Hub:
    def __init__(self, state_root: pathlib.Path, codec: Any = None) -> None:
        self.state_root = state_root
        self._codec = codec or _Codec()
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def request(self, _identity: Any, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((operation, payload))
        return {"published": [], "delivered": []}


@pytest.mark.asyncio
async def test_flush_waits_registration_started_while_draining_other_session(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = state.SessionState("first", str(tmp_path), engine="codex")
    second = state.SessionState("second", str(tmp_path), engine="codex")
    hub = _Hub(tmp_path)
    writer = service_projection.ServiceStatusWriter(
        {"first": first, "second": second},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        cast(service_projection.ProjectionHub, hub),
    )
    writer._active = True  # pylint: disable=protected-access
    published: list[str] = []

    async def publish_first() -> None:
        await asyncio.sleep(0)
        published.append("first")

    async def publish_second(**_kwargs: Any) -> None:
        # 後続sessionの公開を待つ間に届いた通知が、公開済みの先行sessionで新しい登録公開を始める。
        first._registration_task = asyncio.create_task(publish_first())  # pylint: disable=protected-access

    monkeypatch.setattr(second, "publish_registration_async", publish_second)
    await writer.flush_async()
    assert published == ["first"]
    assert not first.registration_pending
    assert [operation for operation, _payload in hub.requests] == ["snapshot"]
    assert [service_state.restore(item).session_id for item in hub.requests[0][1]["sessions"]] == ["first", "second"]


def _writer(hub: _Hub, sessions: dict[str, state.SessionState], **kwargs: Any) -> service_projection.ServiceStatusWriter:
    writer = service_projection.ServiceStatusWriter(
        sessions,
        shared_roots.StatusFileIdentity("root", "root.json", None),
        cast(service_projection.ProjectionHub, hub),
        **kwargs,
    )
    writer._active = True  # pylint: disable=protected-access
    return writer


@pytest.mark.asyncio
async def test_retain_result_waits_registration_started_as_previous_one_finished(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登録公開の完了を待つ側が再開する前に始まった次の公開も、写す前に完了させる。"""
    session = state.SessionState("first", str(tmp_path), engine="codex")
    hub = _Hub(tmp_path)
    writer = _writer(hub, {"first": session})
    published: list[str] = []

    async def publish(name: str) -> None:
        await asyncio.sleep(0)
        published.append(name)

    def start_next() -> None:
        session._registration_task = asyncio.create_task(publish("next"))  # pylint: disable=protected-access

    async def previous() -> None:
        await publish("previous")
        # 完了を待つ側が再開する前に、届いた通知が次の登録公開を始める。
        asyncio.get_running_loop().call_soon(start_next)

    session._registration_task = asyncio.create_task(previous())  # pylint: disable=protected-access
    pending_at_request: list[bool] = []
    forward = hub.request

    async def request(identity: Any, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        pending_at_request.append(session.registration_pending)
        return await forward(identity, operation, payload)

    monkeypatch.setattr(hub, "request", request)
    await writer.retain_result_async(session)
    assert [operation for operation, _payload in hub.requests] == ["retain_result_async"]
    assert pending_at_request == [False]


@pytest.mark.asyncio
async def test_startup_publishes_registry_derived_resume_state_kept_as_expired(tmp_path: pathlib.Path) -> None:
    """登録簿から復元した再開状態を期限切れに持つ会話でも、起動時の状態公開が型を保って進む。

    2026-11-18、復旧に失敗した子のこの状態を符号化できず、管理サービスの起動が毎回失敗してlist・show・send_messageが使えなかった。
    """
    resume = state.RegistryResumeState(
        "child",
        str(tmp_path / "removed"),
        None,
        None,
        "claude",
        turn_seq=3,
        status="interrupted",
        result_root_session_id="root",
    )
    codec = service_state._StateCodec()  # pylint: disable=protected-access
    hub = _Hub(tmp_path, codec)
    manager = AgentsServerManager(_writer(hub, {}, expired_sessions={"child": resume}))
    await manager.initialize_delegation_recovery()
    assert [operation for operation, _payload in hub.requests] == ["snapshot"]
    expired = codec.decode(json.loads(json.dumps(hub.requests[0][1]["expired"])))
    assert expired == {"child": resume}
    assert type(expired["child"]) is state.RegistryResumeState  # pylint: disable=unidiomatic-typecheck


_PARENTS = ("first-parent", "second-parent")


def _identity(parent: str) -> dict[str, Any]:
    return dataclasses.asdict(shared_roots.StatusFileIdentity(parent, "root.json", None))


async def _observed(hub: service_projection.ProjectionHub, parent: str, progress: str, effort: str | None) -> dict[str, Any]:
    """世代が親会話ごとに保存する観測（状態表示の保存要求の本文）を作る。"""
    session = state.SessionState(f"{parent}-child", "/work", engine="codex", effort=effort, announced=True)
    session.set_progress(progress)
    return {
        "sessions": [await service_state.snapshot(session)],
        "expired": hub._codec.encode({}),  # pylint: disable=protected-access
        "published": [],
    }


async def _published_hub(tmp_path: pathlib.Path, groups: dict[str, tuple[str, str | None]]) -> service_projection.ProjectionHub:
    """旧世代から2つの親会話の観測を受け取り、受付を公開した直後の新世代の状態表示を作る。"""
    directory = tmp_path / "agent-toolkit" / "services" / "agents-server"
    registry = GenerationRegistry(directory)
    process = service_transport.process_identity(os.getpid())
    for generation, version in (("old", "1.0.0"), ("new", "1.0.1")):
        registry.reserve(generation, tmp_path / generation, version)
    registry.publish("old", endpoint="old", process=process)
    hub = service_projection.ProjectionHub("new", directory)
    inventory = {
        "writers": [
            {
                "identity": _identity(parent),
                "groups": {source: await _observed(hub, parent, *value) for source, value in groups.items()},
                "published": [],
            }
            for parent in _PARENTS
        ]
    }
    await hub.seed(inventory)
    registry.publish("new", endpoint="new", process=process)
    return hub


def _displayed(tmp_path: pathlib.Path, parent: str) -> tuple[str, str | None]:
    """ユーザーのstatuslineが読む状態ファイルから、親会話の子sessionの進捗とeffortを読む。"""
    path = session_layout.status_directory(parent, tmp_path / "agent-toolkit") / "root.json"
    [session] = json.loads(path.read_text(encoding="utf-8"))["sessions"]
    return session["progress"], session["effort"]


async def _hold_first_parent(
    hub: service_projection.ProjectionHub, monkeypatch: pytest.MonkeyPatch, release: asyncio.Event
) -> tuple[asyncio.Task, asyncio.Lock]:
    """1件目の親会話で通知の読み取りに書込主体のロックを持たせ、後続の保存と再描画をロックの順に並ばせる。"""
    key = (_PARENTS[0], "root.json")

    async def take_notices_async(session_id: str) -> list:
        del session_id
        await release.wait()
        return []

    monkeypatch.setattr(hub.writers[key], "take_notices_async", take_notices_async)
    reading = asyncio.create_task(
        hub.apply(
            {
                "identity": _identity(_PARENTS[0]),
                "source": "old",
                "operation": "take_notices_async",
                "arguments": {"session_id": f"{_PARENTS[0]}-child"},
            }
        )
    )
    lock = hub._locks[key]  # pylint: disable=protected-access
    await _queued(lock, 0)
    return reading, lock


async def _queued(lock: asyncio.Lock, waiters: int) -> None:
    """ロックが保持され、指定数の要求が待つまで進める。保存と再描画の順序を待ち時間に頼らずに固定する。"""
    while not lock.locked() or len(lock._waiters or ()) < waiters:  # pylint: disable=protected-access
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_publication_keeps_progress_saved_by_previous_generation_while_activating(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開が状態表示を再適用するまでに旧世代が保存した進捗とeffortを、公開の後も表示に残す。

    2026-11-20の独立検証で、公開がロックを取る前に選んだ観測を再適用し、保存済みの進捗とeffortを空へ戻した。
    """
    hub = await _published_hub(tmp_path, {"old": ("", None)})
    release = asyncio.Event()
    async with asyncio.timeout(10):
        reading, lock = await _hold_first_parent(hub, monkeypatch, release)
        saved = await _observed(hub, _PARENTS[0], "probe-checkpoint-2", "high")
        saving = asyncio.create_task(
            hub.apply({"identity": _identity(_PARENTS[0]), "source": "old", "operation": "snapshot", "arguments": saved})
        )
        await _queued(lock, 1)
        activating = asyncio.create_task(hub.activate())
        await _queued(lock, 2)
        release.set()
        await asyncio.gather(reading, saving, activating)
    assert _displayed(tmp_path, _PARENTS[0]) == ("probe-checkpoint-2", "high")
    assert _displayed(tmp_path, _PARENTS[1]) == ("", None)


@pytest.mark.asyncio
async def test_progress_saved_while_other_generation_leaves_parent_stays_displayed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じ親会話から旧世代の観測が外れる間に新世代が保存した進捗とeffortを、外れた後の再描画で古い値へ戻さない。"""
    hub = await _published_hub(tmp_path, {"old": ("old-progress", "low"), "new": ("", None)})
    release = asyncio.Event()
    async with asyncio.timeout(10):
        reading, lock = await _hold_first_parent(hub, monkeypatch, release)
        leaving = asyncio.create_task(
            hub.apply({"identity": _identity(_PARENTS[0]), "source": "old", "operation": "deactivate", "arguments": {}})
        )
        await _queued(lock, 1)
        saved = await _observed(hub, _PARENTS[0], "probe-checkpoint-2", "high")
        saving = asyncio.create_task(
            hub.apply({"identity": _identity(_PARENTS[0]), "source": "new", "operation": "snapshot", "arguments": saved})
        )
        await _queued(lock, 2)
        release.set()
        await asyncio.gather(reading, leaving, saving)
    assert _displayed(tmp_path, _PARENTS[0]) == ("probe-checkpoint-2", "high")
