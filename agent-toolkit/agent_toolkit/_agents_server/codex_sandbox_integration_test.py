"""導入済みCodex App Serverによる探索の事前確認要求の受理を検証する。"""

import pathlib
import shutil
import subprocess
import sys
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
@pytest.mark.skipif(sys.platform == "win32", reason="Windowsでは厳格なprobe成功試験を実行する")
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


def _prepare_windows_app_server(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    restore_host_environment(monkeypatch)
    executable = shutil.which("codex")
    assert executable is not None, "Windows integrationにはCodex CLI 0.163.0の導入が必要です"
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, check=True, timeout=15)
    assert result.stdout.strip() == "codex-cli 0.163.0", result.stdout + result.stderr
    print(result.stdout.strip())
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    # 試験専用の設定だけでWindows native sandboxを選び、管理者によるセットアップを要しない。
    (codex_home / "config.toml").write_text('[windows]\nsandbox = "unelevated"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(codex, "APP_SERVER_WORKING_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(codex, "APP_SERVER_COMMAND", (*codex.APP_SERVER_COMMAND, "--strict-config"))


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows実機のreadOnly sandbox成功を検査する")
async def test_windows_explore_probe_reaches_thread_start(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """実probeの成功後にthread/startへ到達し、モデル用通信の開始前に終了する。"""
    _prepare_windows_app_server(tmp_path, monkeypatch)
    backend = codex.AppServerManager()
    ensure_client = AsyncMock(return_value=_ThreadStartBoundary())
    monkeypatch.setattr(backend, "_ensure_client", ensure_client)
    try:
        with pytest.raises(_ProbeAccepted):
            await backend.start("確認後のモデル入力は配送しない", str(tmp_path), launch_kind="explore")
        ensure_client.assert_awaited_once()
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows native sandboxの出力上限指定の拒否を検査する")
async def test_windows_explore_probe_rejects_custom_output_cap(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """0.163.0のWindows sandboxがdisableOutputCap=Trueを実RPCエラーで拒否する。"""
    _prepare_windows_app_server(tmp_path, monkeypatch)
    original_request = codex.JsonRpcProcess.request

    async def request(
        client: codex.JsonRpcProcess, method: str, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        if method == "command/exec":
            assert params is not None
            params = {**params, "disableOutputCap": True}
        return await original_request(client, method, params, **kwargs)

    monkeypatch.setattr(codex.JsonRpcProcess, "request", request)
    backend = codex.AppServerManager()
    ensure_client = AsyncMock(return_value=_ThreadStartBoundary())
    monkeypatch.setattr(backend, "_ensure_client", ensure_client)
    try:
        with pytest.raises(codex.StartCandidateUnavailable) as raised:
            await backend.start("確認後のモデル入力は配送しない", str(tmp_path), launch_kind="explore")
        cause = raised.value.__cause__
        assert raised.value.reason.startswith("exploreSandboxRpcError: ")
        assert isinstance(cause, codex.JsonRpcResponseError)
        assert cause.method == "command/exec"
        assert "custom outputBytesCap is not supported with windows sandbox" in str(cause)
        ensure_client.assert_not_awaited()
    finally:
        await backend.close()
