"""実SDKの代表シナリオと分け、予約の境界値・取消しをモデル呼出しなしで確認する。"""

from __future__ import annotations

import asyncio
import logging

import pytest

from agent_toolkit._agents_server import state, wait_compaction


@pytest.fixture(
    name="waiting",
    params=[(engine, kind) for engine in ("claude", "codex") for kind in ("delegate", "write", "explore", "shell")],
)
def _waiting(request: pytest.FixtureRequest) -> state.SessionState:
    engine, kind = request.param
    session = state.SessionState("parent", "/work", engine=engine, launch_kind=kind)
    session.awaiting_auto_resume = True
    session.pending_result = {"status": "completed", "agent_message": "待機中", "error": None}
    session.cli_turn_state = "idle"
    session.turn_completed = True
    if session.engine == "claude":
        session.live_tasks["task"] = state.LiveTask("local_bash", "wait", state.utc_now())
    session.live_child_session_ids.add("child")
    session.agents_wait_background_outputs.add("/work/output")
    return session


@pytest.mark.parametrize(
    "tokens,window,expected",
    [
        (399_999, 1_000_000, False),
        (400_000, 1_000_000, True),
        (400_000, 2_000_000, True),
        (79_999, 200_000, False),
        (80_000, 200_000, True),
        (None, 100, False),
        (50, 0, False),
    ],
)
@pytest.mark.asyncio
async def test_wait_reservation_observes_delay_usage_and_only_runs_once(
    waiting: state.SessionState, tokens: int | None, window: int, expected: bool
) -> None:
    """待機の同一性と使用量の境界を守り、同じ通知で予約を重ねない。"""
    entered = asyncio.Event()
    release = asyncio.Event()
    queued: list[str] = []
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)
        entered.set()
        await release.wait()

    async def usage() -> dict[str, int | None]:
        return {"totalTokens": tokens, "rawMaxTokens": window}

    async def enqueue(key: str) -> None:
        queued.append(key)

    observer = wait_compaction.WaitCompaction(usage, enqueue, sleep=sleep)
    try:
        observer.observe(waiting)
        await entered.wait()
        assert delays == [60]
        observer.observe(waiting)
        assert not queued
        release.set()
        await asyncio.sleep(0)
        observer.observe(waiting)
        await asyncio.sleep(0)
        assert bool(queued) is expected
        assert len(queued) <= 1
    finally:
        await observer.close()


@pytest.mark.parametrize("change", ["finished", "running", "no_wait", "interrupt"])
@pytest.mark.asyncio
async def test_changed_wait_does_not_deliver_a_compaction_turn(waiting: state.SessionState, change: str) -> None:
    """予約後の完了・追送と競合しても、新しいturnを起こさない。"""
    entered = asyncio.Event()
    release = asyncio.Event()
    queued: list[str] = []

    async def sleep(_seconds: float) -> None:
        entered.set()
        await release.wait()

    async def usage() -> dict[str, int]:
        return {"totalTokens": 50, "rawMaxTokens": 100}

    async def enqueue(key: str) -> None:
        queued.append(key)

    observer = wait_compaction.WaitCompaction(usage, enqueue, sleep=sleep)
    try:
        observer.observe(waiting)
        await entered.wait()
        if change == "finished":
            waiting.live_tasks.clear()
            waiting.live_child_session_ids.clear()
        elif change == "running":
            waiting.cli_turn_state = "running"
            waiting.turn_completed = False
        elif change == "no_wait":
            waiting.pending_result = None
        elif change == "interrupt":
            waiting.interrupt_requested = True
        release.set()
        await asyncio.sleep(0)
        assert not queued
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_same_wait_rechecks_usage_after_crossing_threshold(waiting: state.SessionState) -> None:
    """同じ待機中に閾値へ上がったとき、最初の低使用率判定で予約を失わない。"""
    checked = asyncio.Event()
    queued: list[str] = []
    tokens = 39

    async def sleep(_seconds: float) -> None:
        return

    async def usage() -> dict[str, int]:
        checked.set()
        return {"totalTokens": tokens, "rawMaxTokens": 100}

    async def enqueue(key: str) -> None:
        queued.append(key)

    async with wait_compaction.WaitCompaction(usage, enqueue, sleep=sleep) as observer:
        observer.observe(waiting)
        await asyncio.wait_for(checked.wait(), 2)
        assert not queued
        tokens = 40
        checked.clear()
        observer.observe(waiting)
        await asyncio.wait_for(checked.wait(), 2)
        assert len(queued) == 1
        observer.observe(waiting)
        await asyncio.sleep(0)
        assert len(queued) == 1


@pytest.mark.parametrize("replaced", [False, True])
@pytest.mark.asyncio
async def test_close_collects_the_pending_reservation(waiting: state.SessionState, replaced: bool) -> None:
    """所有タスクの終了後にSDKへ遅れた入力を送らない。"""
    entered = asyncio.Event()
    cancelled: set[int] = set()
    reservations: list[int] = []

    async def sleep(_seconds: float) -> None:
        number = len(reservations)
        reservations.append(number)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.add(number)

    async def enqueue(_key: str) -> None:
        pytest.fail("終了後に圧縮を配送した")

    async def usage() -> dict[str, int]:
        pytest.fail("終了後に使用量を照会した")

    observer = wait_compaction.WaitCompaction(usage, enqueue, sleep=sleep)
    observer.observe(waiting)
    await entered.wait()
    if replaced:
        entered.clear()
        waiting.turn_seq += 1
        waiting.live_child_session_ids.add("replacement")
        observer.observe(waiting)
        await asyncio.wait_for(entered.wait(), 2)
    await observer.close()
    assert cancelled == set(range(2 if replaced else 1))


@pytest.mark.asyncio
async def test_high_usage_new_wait_is_paused_until_usage_falls(waiting: state.SessionState) -> None:
    """待機対象の変化だけでは再圧縮せず、低使用率の観測後に再開する。"""
    settled = asyncio.Event()
    queued: list[str] = []

    async def sleep(_seconds: float) -> None:
        return

    async def usage() -> dict[str, int]:
        settled.set()
        return {"totalTokens": 50, "rawMaxTokens": 100}

    async def enqueue(key: str) -> None:
        queued.append(key)

    async with wait_compaction.WaitCompaction(usage, enqueue, sleep=sleep) as observer:
        for number in range(3):
            settled.clear()
            waiting.turn_seq += 1
            waiting.agents_wait_background_outputs = {f"/work/output-{number}"}
            if number == 2:
                waiting.context_tokens, waiting.context_window = 10, 100
            observer.observe(waiting)
            await asyncio.wait_for(settled.wait(), 2)
            assert len(queued) == (1 if number < 2 else 2)


@pytest.mark.parametrize("failure", ["usage", "enqueue"])
@pytest.mark.asyncio
async def test_optional_compaction_failure_keeps_waiting_without_warning(
    waiting: state.SessionState, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    """付随する最適化の失敗は内部診断に残し、担当結果と通常の待機を保つ。"""
    settled = asyncio.Event()
    calls: list[str] = []
    pending = waiting.pending_result

    async def sleep(_seconds: float) -> None:
        return

    async def usage() -> dict[str, int]:
        calls.append("usage")
        if failure == "usage":
            settled.set()
            raise RuntimeError("使用量の照会失敗")
        return {"totalTokens": 50, "rawMaxTokens": 100}

    async def enqueue(_key: str) -> None:
        calls.append("enqueue")
        settled.set()
        raise RuntimeError("圧縮の配送失敗")

    with caplog.at_level(logging.DEBUG, logger="agent-toolkit.agents-server.compaction"):
        async with wait_compaction.WaitCompaction(usage, enqueue, sleep=sleep) as observer:
            observer.observe(waiting)
            await asyncio.wait_for(settled.wait(), 2)
            observer.observe(waiting)
            await asyncio.sleep(0)
    assert waiting.pending_result is pending
    assert waiting.awaiting_auto_resume
    assert not waiting.terminal
    assert calls == (["usage"] if failure == "usage" else ["usage", "enqueue"])
    assert any(record.levelno == logging.DEBUG and "wait_compaction_failed" in record.message for record in caplog.records)
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)
