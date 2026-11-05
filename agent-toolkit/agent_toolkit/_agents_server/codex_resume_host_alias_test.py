"""保存済み会話の再開RPCより先に、子MCPが直接親を解決できることを検証する。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import codex, shared_roots, state
from agent_toolkit._testing.agents_server_support import FakeCodexClient

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


class _HostAliasClient(FakeCodexClient):
    def __init__(self, started_writers: list[str], resolved_parents: list[str]) -> None:
        super().__init__()
        self.started_writers = started_writers
        self.resolved_parents = resolved_parents

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method in {"thread/start", "thread/resume"}:
            assert params is not None
            environment = params["config"]["mcp_servers"]["agents_server"]["env"]
            writer = environment["AGENT_TOOLKIT_STATUS_HOST_SESSION"]
            if method == "thread/start":
                self.started_writers.append(writer)
            else:
                # 新しいbackendのwriterには開始時の索引が無い。後書込ではこの時点に間に合わない。
                assert writer not in self.started_writers
                path = shared_roots.hosts_directory(environment["AGENT_TOOLKIT_OWNER_SESSION"]) / f"{writer}.json"
                assert path.is_file(), "thread/resume受信時点で直接親の索引が必要"
                parent = json.loads(path.read_text(encoding="utf-8"))["host_session_id"]
                assert parent == params["threadId"]
                self.resolved_parents.append(parent)
        return await super().request(method, params, on_sent=on_sent)


@pytest.mark.asyncio
async def test_start_release_and_cold_resume_publish_host_alias_before_resume_rpc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開start・release_session・同IDresumeを通し、再開RPC受信時に新writerから親を解決する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_AUTO_RESET", "false")
    started_writers: list[str] = []
    resolved_parents: list[str] = []
    initial_client = _HostAliasClient(started_writers, resolved_parents)
    async with codex.AppServerManager(root_session_id="alias-root") as initial_backend:
        # 外部process生成だけを置換し、公開backend操作と実際の索引保存はそのまま実行する。
        monkeypatch.setattr(initial_backend, "_ensure_client", AsyncMock(return_value=initial_client))
        original = await initial_backend.start("作業", str(tmp_path))
        assert original.turn_id
        await initial_backend.release_session(original)
        assert initial_client.closed
    assert len(started_writers) == 1

    # 保存済みsessionのcold resumeでは、前backendのwriter IDキャッシュを引き継がない。
    resumed_client = _HostAliasClient(started_writers, resolved_parents)
    async with codex.AppServerManager(root_session_id="alias-root") as resumed_backend:
        monkeypatch.setattr(resumed_backend, "_ensure_client", AsyncMock(return_value=resumed_client))
        resumed = await resumed_backend.resume(
            original.session_id,
            state.ResumePrompt("再開"),
            str(tmp_path),
            state_provider=original.codex_state_provider,
            turn_seq=original.turn_seq,
        )
        assert resolved_parents == [original.session_id]
        assert resumed.session_id == original.session_id
        assert resumed.turn_seq == original.turn_seq + 1
        assert resumed.status == "running" and resumed.turn_id
    assert resumed_client.closed
