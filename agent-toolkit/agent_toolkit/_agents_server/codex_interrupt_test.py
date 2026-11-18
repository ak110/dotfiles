"""管理サービスの受信処理を経由するCodex turnへのkillが、受信処理の停止・停滞の下でも有限時間で確定する。

App Serverの模擬と、broker・受信処理・保存の組み立ては`codex_auto_reset_backend_test`のものを使う。
2.262.xの管理サービスでは受信処理が保存の例外で止まり、未確認イベントが上限の256件に達した。
最終回答の送出中だった委譲先は実行中のまま残り、killは`interrupt delivery is undetermined`で失敗した。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from agent_toolkit._agents_server import codex_auto_reset_backend_test as harness
from agent_toolkit._agents_server import state
from agent_toolkit._agents_server.session_errors import ActionableTimeoutError

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
    [("stalled_consumer", "送出確認待ち", "256/256"), ("silent_app_server", "応答待ち", "0/256")],
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
