"""受付予約の回収、3世代の逆行防止、重なった更新の取り消しを固定する。"""

import asyncio

import pytest

from agent_toolkit._agents_server.service_turns import MoveRequired, TurnGate


@pytest.mark.asyncio
async def test_freeze_drains_control_but_not_model_execution() -> None:
    gate = TurnGate("G1", {"generation": "G1", "epoch": 1})
    entered, release = asyncio.Event(), asyncio.Event()

    async def control() -> None:
        async with gate.admit(new_turn=True):
            entered.set()
            await release.wait()

    operation = asyncio.create_task(control())
    await entered.wait()
    freezing = asyncio.create_task(gate.freeze("update"))
    await asyncio.sleep(0)
    assert not freezing.done()
    release.set()
    await operation
    await freezing
    redirected = asyncio.create_task(_start(gate))
    await asyncio.sleep(0)
    assert not redirected.done()
    await gate.observe_current({"generation": "G3", "epoch": 3})
    with pytest.raises(MoveRequired) as moved:
        await redirected
    assert moved.value.target["generation"] == "G3"
    await gate.observe_current({"generation": "G2", "epoch": 2})
    assert gate.current["generation"] == "G3"
    # 旧資源の観測と終了操作は、移譲後も新turnを生成せずに完走できる。
    async with gate.admit(new_turn=False, session_id="parent"):
        assert gate.capture() is not None


async def _start(gate: TurnGate) -> None:
    async with gate.admit(new_turn=True):
        pytest.fail("旧世代で新しいturnを開始した")


@pytest.mark.asyncio
async def test_releasing_one_update_does_not_cancel_another() -> None:
    gate = TurnGate("G1", {"generation": "G1", "epoch": 1})
    await gate.freeze("prepare-G2")
    await gate.freeze("prepare-G3")
    await gate.thaw("prepare-G2")
    pending = asyncio.create_task(_start(gate))
    await asyncio.sleep(0)
    assert not pending.done()
    await gate.observe_current({"generation": "G3", "epoch": 3})
    with pytest.raises(MoveRequired):
        await pending


@pytest.mark.asyncio
async def test_sdk_owner_task_keeps_an_admitted_ticket_alive() -> None:
    gate = TurnGate("G1", {"generation": "G1", "epoch": 1})
    active, release = asyncio.Event(), asyncio.Event()

    async def sdk(ticket) -> None:
        async with gate.admit(new_turn=True, ticket=ticket):
            active.set()
            await release.wait()

    async with gate.admit(new_turn=True) as ticket:
        worker = asyncio.create_task(sdk(ticket))
        await active.wait()
    freezing = asyncio.create_task(gate.freeze("update"))
    await asyncio.sleep(0)
    assert not freezing.done()
    release.set()
    await worker
    await freezing
    assert gate.capture() is None
