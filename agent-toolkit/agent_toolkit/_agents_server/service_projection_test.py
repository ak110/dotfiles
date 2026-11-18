"""保存の待機中に始まった登録公開を待ってから、世代間の状態を写す。"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any, cast

import pytest

from agent_toolkit._agents_server import service_projection, service_state, shared_roots, state
from agent_toolkit._agents_server.manager import AgentsServerManager


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
