"""管理サービスの受信処理を経由するCodex turnへのkillが、受信処理の停止・停滞の下でも有限時間で確定する。

App Serverの模擬と、broker・受信処理・保存の組み立ては`codex_auto_reset_backend_test`のものを使う。
2.262.xの管理サービスでは受信処理が保存の例外で止まり、未確認イベントが上限の256件に達した。
最終回答の送出中だった委譲先は実行中のまま残り、killは`interrupt delivery is undetermined`で失敗した。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

import pytest

from agent_toolkit._agents_server import codex, codex_clients, state
from agent_toolkit._agents_server import codex_auto_reset_backend_test as harness
from agent_toolkit._agents_server.session_errors import KILL_NOT_DELIVERED_NEXT_ACTION, ActionableTimeoutError
from agent_toolkit._common.next_action import ActionableError

# 模擬環境の組み立てと接続の診断を、試験から内部境界のまま使う。
# pylint: disable=protected-access
pytestmark = pytest.mark.usefixtures("agents_server_isolation")

_SAVE_FAILURE = "session登録の保存完了前には移譲できません"


def _stream_final_answer(client: harness._MemoryAppServer, session: state.SessionState) -> None:
    # 最終回答のdeltaだけで、元I/O側の未確認イベント上限（256件）を超える。
    item = {"type": "agentMessage", "id": "final", "text": "", "phase": "final_answer"}
    client.emit("item/started", {"threadId": session.session_id, "turnId": session.turn_id, "item": item})
    for index in range(300):
        params = {"threadId": session.session_id, "turnId": session.turn_id, "itemId": "final", "delta": f"{index} "}
        client.emit("item/agentMessage/delta", params)


@contextlib.contextmanager
def _stopped_when_published(clients: list[harness._MemoryAppServer]) -> Generator[dict[str, bool]]:
    """終了時の結果を公開した時点で、そのsessionのApp Serverが止まっていたかをsessionごとに記録する。"""
    stopped: dict[str, bool] = {}

    def record(session: state.SessionState) -> None:
        [client] = [client for client in clients if client.thread_id == session.session_id]
        stopped[session.session_id] = client.stopped

    state.add_terminal_listener(record)
    try:
        yield stopped
    finally:
        state.remove_terminal_listener(record)


async def _start_other(backend: codex.AppServerManager, monkeypatch: pytest.MonkeyPatch, cwd: Path) -> state.SessionState:
    """別のApp Serverを所有する2つ目の委譲先を起動する。"""
    create = codex_clients.JsonRpcProcess

    def factory(*args: Any, **kwargs: Any) -> harness._MemoryAppServer:
        client = cast(harness._MemoryAppServer, create(*args, **kwargs))
        client.thread_id = "other-thread"
        return client

    monkeypatch.setattr(codex_clients, "JsonRpcProcess", factory)
    return await backend.start("別の作業", str(cwd))


async def _until_idle(backend: codex.AppServerManager, *sessions: state.SessionState) -> None:
    """turnの開始通知を解釈して保存し終え、受信処理が次のイベントを待つ状態まで待つ。"""
    clients = [backend._clients[session.session_id] for session in sessions]
    async with asyncio.timeout(5):
        while any(session.status != "running" for session in sessions) or any(
            client.diagnostic()["backlog"] != "0/256" for client in clients
        ):
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_consumer_stop_stops_own_app_server_before_failure_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """受信処理が止まった委譲先は、自身のApp Serverを止めてから失敗として公開し、別の委譲先のApp Serverとturnは続く。"""
    failing = asyncio.Event()

    async def save() -> None:
        if failing.is_set():
            raise RuntimeError(_SAVE_FAILURE)

    async with harness._service_environment(tmp_path, monkeypatch, save) as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        other = await _start_other(backend, monkeypatch, tmp_path)
        await _until_idle(backend, session, other)
        with _stopped_when_published(clients) as stopped:
            failing.set()
            _stream_final_answer(clients[0], session)
            await harness._until(backend, lambda: session.terminal)
            failing.clear()
        assert session.status == "failed"
        assert isinstance(session.error, dict) and _SAVE_FAILURE in session.error["message"]
        assert stopped == {session.session_id: True}
        response = await owner.kill(session.session_id, timeout=5)
        assert (response["status"], response["kill_requested"]) == ("failed", False)

        answer = {"type": "agentMessage", "id": "answer", "text": "別の作業の成果"}
        turn = {"id": other.turn_id, "status": "completed", "error": None, "items": [answer]}
        clients[1].emit("turn/completed", {"threadId": other.session_id, "turn": turn})
        await harness._until(backend, lambda: other.result_available)
        assert (other.status, other.agent_message) == ("completed", "別の作業の成果")
        assert not clients[1].stopped


@pytest.mark.parametrize("service", [False, True])
@pytest.mark.asyncio
async def test_reader_failure_stops_app_server_before_failure_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, service: bool
) -> None:
    """App Serverの出力を読めなくなった委譲先は、App Serverを止めてから失敗として公開する。"""
    environment = (
        harness._service_environment(tmp_path, monkeypatch, harness._no_save)
        if service
        else harness._environment(tmp_path, monkeypatch)
    )
    async with environment as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        with _stopped_when_published(clients) as stopped:
            clients[0].stream.feed_data(b"{not json\n")
            await harness._until(backend, lambda: session.terminal)
        assert session.status == "failed"
        assert stopped == {session.session_id: True}
        response = await owner.kill(session.session_id, timeout=5)
    assert (response["status"], response["kill_requested"]) == ("failed", False)


@pytest.mark.asyncio
async def test_kill_while_app_server_stops_waits_for_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """受信処理の停止からApp Serverを止めている間に届いたkillは、中断要求を送らずに失敗の公開を待って返す。"""
    failing = asyncio.Event()

    async def save() -> None:
        if failing.is_set():
            raise RuntimeError(_SAVE_FAILURE)

    async with harness._service_environment(tmp_path, monkeypatch, save) as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        await _until_idle(backend, session)
        release = clients[0].close_release = asyncio.Event()
        try:
            failing.set()
            _stream_final_answer(clients[0], session)
            async with asyncio.timeout(5):
                while not clients[0].closed:
                    await asyncio.sleep(0.01)
            kill = asyncio.create_task(owner.kill(session.session_id, timeout=5))
            async with asyncio.timeout(5):
                while not session.interrupt_requested and not kill.done():
                    await asyncio.sleep(0.01)
            # killの受付後もApp Serverの停止が終わるまでは、失敗を公開せず、killも返さない。
            await asyncio.sleep(0.1)
            assert not kill.done()
            assert not session.terminal
        finally:
            release.set()
        response = await kill
    assert response["status"] == "failed"
    assert "turn/interrupt" not in [method for method, _ in clients[0].requests]


@pytest.mark.asyncio
async def test_kill_reports_next_action_without_an_owned_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """中断要求を配送できなければ、導入の案内ではなく次の操作を返す。"""
    async with harness._service_environment(tmp_path, monkeypatch, harness._no_save) as (owner, backend, _clients):
        session = await backend.start("作業", str(tmp_path))
        with monkeypatch.context() as patch:
            patch.setattr(backend, "_owned_client", lambda _session: None)
            with pytest.raises(ActionableError) as error:
                await owner.kill(session.session_id, timeout=5)
        assert error.value.next_action == KILL_NOT_DELIVERED_NEXT_ACTION


@pytest.mark.parametrize("service", [False, True])
@pytest.mark.asyncio
async def test_kill_awaiting_interrupt_response_survives_reader_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, service: bool
) -> None:
    """中断要求の応答待ちの間に出力を読めなくなったkillは、次の操作の無い接続エラーで返らない。

    接続が失敗を先に記録していれば失敗の公開を待って返し、記録より先に要求が失敗した場合は次の操作を添えて返す。
    """
    environment = (
        harness._service_environment(tmp_path, monkeypatch, harness._no_save)
        if service
        else harness._environment(tmp_path, monkeypatch)
    )
    kill_timeout = 20.0
    async with environment as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        clients[0].silent.add("turn/interrupt")
        kill = asyncio.create_task(owner.kill(session.session_id, timeout=kill_timeout))
        async with asyncio.timeout(5):
            while "turn/interrupt" not in [method for method, _ in clients[0].requests]:
                await asyncio.sleep(0.01)
        started = time.monotonic()
        clients[0].stream.feed_data(b"{not json\n")
        outcome = (await asyncio.gather(kill, return_exceptions=True))[0]
        elapsed = time.monotonic() - started
        # 配送期限まで止まった場合は期限切れの`ActionableTimeoutError`になる。接続の失敗を受けたら期限よりずっと早く返る。
        assert elapsed < kill_timeout / 2
        assert not isinstance(outcome, TimeoutError)
        if isinstance(outcome, BaseException):
            assert service
            assert isinstance(outcome, ActionableError)
            assert outcome.next_action == KILL_NOT_DELIVERED_NEXT_ACTION
        else:
            assert outcome["status"] == "failed"
        await harness._until(backend, lambda: session.terminal)
        assert session.status == "failed"
        again = await owner.kill(session.session_id, timeout=5)
    assert (again["status"], again["kill_requested"]) == ("failed", False)


@pytest.mark.parametrize(
    ("consumer_fails", "expected"),
    [(False, ("interrupted", True)), (True, ("failed", False))],
)
@pytest.mark.asyncio
async def test_kill_during_final_answer_is_determined_even_after_consumer_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer_fails: bool, expected: tuple[str, bool]
) -> None:
    """受信処理が止まった委譲先は実行中のまま残らず、killは中断要求の配送を待たずに終了を返す。"""
    streaming = asyncio.Event()

    async def save() -> None:
        if consumer_fails and streaming.is_set():
            raise RuntimeError(_SAVE_FAILURE)

    async with harness._service_environment(tmp_path, monkeypatch, save) as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        streaming.set()
        _stream_final_answer(clients[0], session)
        if consumer_fails:
            await harness._until(backend, lambda: session.terminal)
            assert isinstance(session.error, dict) and _SAVE_FAILURE in session.error["message"]
        response = await owner.kill(session.session_id, timeout=5)
    assert (response["status"], response["kill_requested"]) == expected


@pytest.mark.parametrize(
    ("cause", "stage", "backlog"),
    [("stalled_consumer", "応答待ち", "256/256"), ("silent_app_server", "応答待ち", "0/256")],
)
@pytest.mark.asyncio
async def test_undetermined_kill_records_stage_and_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, cause: str, stage: str, backlog: str
) -> None:
    """中断要求の配送を確定できないkillは、止まった段階と受信処理の状態を所有サーバーのログへ残す。"""
    hold = asyncio.Event()
    release = asyncio.Event()

    async def save() -> None:
        if hold.is_set():
            await release.wait()

    async with harness._service_environment(tmp_path, monkeypatch, save) as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = backend._clients[session.session_id]
        try:
            if cause == "stalled_consumer":
                hold.set()
                _stream_final_answer(clients[0], session)
                async with asyncio.timeout(5):
                    while client.diagnostic()["backlog"] != "256/256":
                        await asyncio.sleep(0.01)
            else:
                clients[0].silent.add("turn/interrupt")
            with (
                caplog.at_level(logging.WARNING, logger="agent-toolkit.agents-server.codex"),
                pytest.raises(ActionableTimeoutError, match="interrupt delivery is undetermined"),
            ):
                await owner.kill(session.session_id, timeout=1)
        finally:
            release.set()
    [record] = [record for record in caplog.records if "turn/interrupt" in record.getMessage()]
    message = record.getMessage()
    assert f"session_id={session.session_id}" in message
    assert f"stage={stage}" in message
    assert "'consumer': '稼働中'" in message
    assert f"'backlog': '{backlog}'" in message
