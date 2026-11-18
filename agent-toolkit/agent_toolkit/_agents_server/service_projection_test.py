"""保存の待機中に始まった登録公開を待ってから、世代間の状態を写す。"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any, cast

import pytest

from agent_toolkit._agents_server import service_projection, service_state, shared_roots, state


class _Codec:
    def encode(self, value: Any) -> Any:
        return value


class _Hub:
    def __init__(self, state_root: pathlib.Path) -> None:
        self.state_root = state_root
        self._codec = _Codec()
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
