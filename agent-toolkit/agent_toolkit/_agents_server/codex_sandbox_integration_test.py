"""導入済みCodex App Serverによる探索の事前確認要求の受理を検証する。"""

import pathlib
import shutil
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import codex
from agent_toolkit._testing.isolation import restore_host_environment


class _ProbeAccepted(Exception):
    """モデル用接続を開始する前に結合テストを終了する。"""


class _ThreadStartBoundary:
    """実際の確認後にthread/startまで到達したことを観測する。"""

    process = None

    async def request(self, method: str, _params: dict[str, Any]) -> dict[str, Any]:
        if method == "config/read":
            return {"config": {}}
        assert method == "thread/start"
        raise _ProbeAccepted

    async def close(self) -> list[str]:
        return []


@pytest.mark.asyncio
async def test_explore_probe_request_is_accepted_by_installed_app_server(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """実App Serverが要求を受理し、成功かsandbox実行後の環境エラーを返す。"""
    restore_host_environment(monkeypatch)
    if shutil.which("codex") is None:
        pytest.skip("codexコマンドが導入されていません")
    # 認証とユーザー設定を参照せず、Codex自身の状態書込先をこのテストが所有する。
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(codex, "APP_SERVER_WORKING_DIRECTORY", str(tmp_path))
    backend = codex.AppServerManager()
    monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=_ThreadStartBoundary()))
    try:
        with pytest.raises((_ProbeAccepted, codex.StartCandidateUnavailable)) as raised:
            await backend.start("確認後のモデル入力は配送しない", str(tmp_path), launch_kind="explore")
        if isinstance(raised.value, codex.StartCandidateUnavailable):
            reason = raised.value.reason
            assert reason == "exploreSandboxNamespaceDenied" or reason.startswith("exploreSandboxCommandFailed:"), reason
    finally:
        await backend.close()
