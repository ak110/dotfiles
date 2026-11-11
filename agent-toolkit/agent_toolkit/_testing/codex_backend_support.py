"""Codex試験の会話・接続スタブを共有する。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import pathlib
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import psutil
import pytest
import yaml

from agent_toolkit import atk
from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import shared_roots, state, status_file
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import lane
from agent_toolkit._common import state_paths
from agent_toolkit._testing import codex_app_server_support, git_repository
from agent_toolkit._testing.agents_server_support import FakeCodexClient, install_backend

# 試験が観測する内部境界を準備する。
# pylint: disable=protected-access


class _ThreadStartClient:
    """thread/startの入力を保持して固定threadを返すスタブ。"""

    def __init__(self) -> None:
        self.params: dict[str, Any] | None = None

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "thread/start"
        self.params = params
        return {"thread": {"id": "inner-thread"}}

    async def close(self) -> list[str]:
        return []


class _TierClient(FakeCodexClient):
    """実際のbackendが送る要求と送信通知を再現し、モデルの実行を伴わずtierを確かめる。"""

    closed = False
    reader_failure = None

    def __init__(self) -> None:
        super().__init__()
        self.parent_environment: dict[str, str] | None = None

    async def request(self, method: str, params: dict[str, Any] | None = None, *, on_sent: Any = None) -> dict[str, Any]:
        params = params or {}
        if method == "config/read":
            return {"config": {"mcp_servers": {"agents_server": {}, "external": {"enabled": True}}}}
        if method == "thread/read":
            return await super().request(method, params, on_sent=on_sent)
        self.requests.append((method, dict(params)))
        if on_sent is not None:
            on_sent()
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": params.get("threadId", "tier-thread")}, "modelProvider": "openai"}
        assert method == "turn/start"
        return {"turn": {"id": f"turn-{len(self.requests)}"}}

    async def close(self) -> list[str]:
        return await super().close()


class _ProcessTierClient(_TierClient):
    """App Serverの子プロセスを持つ偽クライアント。"""

    def __init__(self, pid: int) -> None:
        super().__init__()
        self.process = SimpleNamespace(pid=pid)


class _ResumeRejectionClient(FakeCodexClient):
    """再開の拒否を外部RPC境界で返し、他の要求は既存fakeで処理する。"""

    def __init__(self, method: str = "thread/resume", message: str | None = None) -> None:
        super().__init__()
        self.error_method = method
        self.error_message = message

    async def request(self, method: str, params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        if method == "thread/resume":
            assert params is not None
            self.requests.append((method, params))
            # CLI 0.166.0で確認済みの断片を使う分類試験。全文の実機観測を主張するfixtureではない。
            message = self.error_message or f"thread {params['threadId']} already has an active writer"
            raise subject.JsonRpcResponseError(self.error_method, -32600, message, {"codexErrorInfo": "unauthorized"})
        return await super().request(method, params, **kwargs)


def _assert_writer_conflict_action(action: str) -> None:
    """受入仕様の未配送・原因の可能性・解放後の再送条件が案内にある。"""
    for detail in (
        "未配送",
        "別のCodex実行プロセス",
        "可能性",
        "使用・購読",
        "解放を確認",
        "同じ`session_id`",
        "`send_message`を再送",
        "操作が分からない",
    ):
        assert detail in action
    assert "handoff_from" not in action


class _SilentClient:
    """要求を受理したまま応答を返さないApp Serverクライアントのスタブ。"""

    closed = False

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del method, params  # noqa
        await asyncio.Event().wait()
        return {}  # pragma: no cover - 待機が解けないことを表す到達不能の分岐

    async def close(self) -> list[str]:
        self.closed = True
        return []


class _HangingStream:
    """行を返さないまま待機し続けるストリームのスタブ。"""

    async def read(self, limit: int) -> bytes:
        assert limit > 0
        await asyncio.Event().wait()
        return b""  # pragma: no cover - 待機が解けないことを表す到達不能の分岐

    async def readline(self) -> bytes:
        await asyncio.Event().wait()
        return b""  # pragma: no cover - 待機が解けないことを表す到達不能の分岐


class _AcceptingStdin:
    """書き込みを受理するだけのstdinのスタブ。"""

    def write(self, data: bytes) -> None:
        del data  # noqa

    async def drain(self) -> None:
        return None


class _DisconnectedStdin:
    """書込時に接続断を返すstdinのスタブ。"""

    def write(self, data: bytes) -> None:
        del data  # noqa
        raise BrokenPipeError("peer disconnected")

    async def drain(self) -> None:
        return None


class _SilentProcess:
    """起動後にJSON-RPC応答を返さない子プロセスのスタブ。"""

    def __init__(self) -> None:
        self.stdin = _AcceptingStdin()
        self.stdout: Any = _HangingStream()
        self.stderr = _HangingStream()
        self.returncode: int | None = None
        # 終了処理は実PIDの子孫を一時停止・終了させるため、実在しないPIDを使い、無関係なプロセスへ作用させない。
        self.pid = next(pid for pid in range(2**22 - 1, 2**21, -1) if not psutil.pid_exists(pid))

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


class _ChunkStream:
    """有限byte列を指定長以下のchunkで返し、返却後は接続を保つ。"""

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.offset = 0

    async def read(self, limit: int) -> bytes:
        assert limit > 0
        if self.offset < len(self.content):
            end = min(self.offset + limit, len(self.content))
            chunk = self.content[self.offset : end]
            self.offset = end
            return chunk
        await asyncio.Event().wait()
        return b""  # pragma: no cover - 接続を保つための到達不能の分岐


class _ChunkProcess(_SilentProcess):
    """stdoutだけをchunk readerへ渡す子プロセスのスタブ。"""

    def __init__(self, content: bytes) -> None:
        super().__init__()
        self.stdout = _ChunkStream(content)


async def _ignore_message(message: dict[str, Any]) -> None:
    del message  # noqa


class _InspectableAppServerManager(subject.AppServerManager):  # pylint: disable=too-many-ancestors  # 責務別のmixinを合成した本体を継承するため。
    """通知入力をテストへ公開する。"""

    async def handle_notification(self, message: dict[str, Any]) -> None:
        await self._handle_notification(message)


def _completed_turn(session: state.SessionState) -> dict[str, Any]:
    return {
        "method": "turn/completed",
        "params": {
            "threadId": session.session_id,
            "turn": {
                "id": session.turn_id,
                "status": "completed",
                "error": None,
            },
        },
    }


def _item_started(session: state.SessionState, item_type: str) -> dict[str, Any]:
    return {
        "method": "item/started",
        "params": {
            "threadId": session.session_id,
            "turnId": session.turn_id,
            "item": {"id": f"item-{item_type}", "type": item_type},
        },
    }


async def _return(value: Any) -> Any:
    return value


async def _return_none() -> None:
    return None


def _overloaded_turn(session: state.SessionState, error_info: str = "serverOverloaded") -> dict[str, Any]:
    message = _completed_turn(session)
    message["params"]["turn"]["status"] = "failed"
    message["params"]["turn"]["error"] = {
        "message": "Selected model is at capacity. Please try a different model.",
        "codexErrorInfo": error_info,
    }
    return message


def _invoke_lane(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any], str]:
    """公開の`atk lane`を同じプロセスで実行し、終了コード・JSON出力・標準エラーを返す。"""
    with pytest.raises(SystemExit) as ended:
        atk.main(["lane", *args])
    output = capsys.readouterr()
    assert isinstance(ended.value.code, int)
    return ended.value.code, json.loads(output.out) if output.out.strip() else {}, output.err


def _create_integrated_lane(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> pathlib.Path:
    """統合済みとして選定したcleanなレーンを公開の`atk lane create`で作成し、専用worktreeを返す。"""
    monkeypatch.setenv(lane.SESSION_ENV, "lane-test-session")
    monkeypatch.setattr(lane, "get_wait_timeout", lambda _bucket: 5.0)
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="初期状態")
    selected = tmp_path / "selection.yaml"
    selected.write_text(
        yaml.safe_dump({"レーンの所要時間": [{"レーン": "lane-01", "統合状態": "統合済み"}]}, allow_unicode=True),
        encoding="utf-8",
    )
    code, record, stderr = _invoke_lane(
        capsys,
        "create",
        *("--repo", str(repo), "--base-branch", "main", "--lane", "lane-01", "--selection-file", str(selected)),
    )
    assert code == 0, stderr
    return pathlib.Path(record["worktree"])


@contextlib.asynccontextmanager
async def _fake_app_server_manager(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    candidates: list[tuple[str, str, str]] | None = None,
) -> AsyncGenerator[
    tuple[server_manager.AgentsServerManager, subject.AppServerManager, codex_app_server_support.FakeAppServer]
]:
    """模擬App Serverを実プロセスで起動する実Codex backendと、登録簿を公開する所有managerを用意する。"""
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.05)
    fake = codex_app_server_support.install(monkeypatch, tmp_path)
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda _model_type, **_kwargs: candidates or [("codex", "gpt-test", "high")],
    )
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("release-root", "root.json", None),
        state_root=state_paths.state_dir(),
        aggregate_seconds=0,
    )
    async with (
        server_manager.AgentsServerManager(writer) as manager,
        subject.AppServerManager(
            manager.sessions, manager._condition, publish_registry=True, root_session_id="release-root"
        ) as backend,
    ):
        install_backend(manager, "codex", backend)
        await manager.activate_async()
        try:
            yield manager, backend, fake
        finally:
            try:
                await manager.close()
            finally:
                fake.terminate_recorded()


async def _wait_terminal(
    manager: server_manager.AgentsServerManager, session_id: str, fake: codex_app_server_support.FakeAppServer
) -> state.SessionState:
    """開始を観測したturnだけを解放し、最終結果を待つ。"""
    assert session_id in manager.sessions and not manager.sessions[session_id].terminal
    await fake.complete_turn(session_id)
    async with asyncio.timeout(10):
        while not ((session := manager.sessions.get(session_id)) is not None and session.terminal):
            await asyncio.sleep(0.02)
    return session
