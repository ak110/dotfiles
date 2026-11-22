"""資源の世代移譲と、保存確認前の生イベント保持を検証する。"""

# 所有制御とcodecの厳密な型、ACK後の内部保持を検証する。
# pylint: disable=protected-access,unidiomatic-typecheck

import asyncio
import dataclasses
import json

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock

from agent_toolkit._agents_server.service_resources import ResourceBroker, sdk_event_codec


@pytest.mark.asyncio
async def test_emit_wakes_an_already_waiting_consumer() -> None:
    broker = ResourceBroker("G1")

    async def close() -> None:
        pass

    resource = broker.register({"close": close})
    reading = asyncio.create_task(broker.events(resource, "G1", 0, wait=30))
    await asyncio.sleep(0)
    assert not reading.done()
    await broker.emit(resource, {"new": "message"})
    assert await asyncio.wait_for(reading, 1) == [{"sequence": 1, "payload": {"new": "message"}}]
    await broker.release(resource, "G1")


@pytest.mark.asyncio
async def test_transfer_waits_for_io_and_rejects_old_owner() -> None:
    broker = ResourceBroker("G1")
    started, finish = asyncio.Event(), asyncio.Event()
    closed = []

    async def io() -> dict:
        started.set()
        await finish.wait()
        return {"raw": 1}

    async def close() -> None:
        closed.append(True)

    resource = broker.register({"read": io, "close": close})
    call = asyncio.create_task(broker.call(resource, "G1", "read", {}))
    await started.wait()
    transfer = asyncio.create_task(broker.transfer(resource, "G1", "G2"))
    await asyncio.sleep(0)
    assert not transfer.done()
    finish.set()
    assert await call == {"raw": 1}
    assert await transfer
    assert await broker.transfer(resource, "G2", "G3")
    assert not await broker.transfer(resource, "G1", "G2")
    assert broker.origin_generation == "G1"
    for owner in ("G1", "G2"):
        with pytest.raises(PermissionError):
            await broker.call(resource, owner, "read", {})
        with pytest.raises(PermissionError):
            await broker.release(resource, owner)
        with pytest.raises(PermissionError):
            await broker.acknowledge(resource, owner, 1)
    assert not closed
    await broker.release(resource, "G3")
    assert closed == [True]


@pytest.mark.asyncio
async def test_events_replay_ack_and_backpressure() -> None:
    broker = ResourceBroker("G1")

    async def close() -> None:
        pass

    resource = broker.register({"close": close})
    for number in range(256):
        await broker.emit(resource, {"number": number})
    blocked = asyncio.create_task(broker.emit(resource, {"number": 256}))
    await asyncio.sleep(0)
    assert not blocked.done()
    assert await broker.transfer(resource, "G1", "G2")
    first = await broker.events(resource, "G2", 0)
    assert first == await broker.events(resource, "G2", 0)
    assert first[0] == {"sequence": 1, "payload": {"number": 0}}
    first[0]["payload"]["number"] = -1
    assert (await broker.events(resource, "G2", 0))[0]["payload"] == {"number": 0}
    await broker.acknowledge(resource, "G2", 1)
    await asyncio.wait_for(blocked, 1)
    assert [event["sequence"] for event in await broker.events(resource, "G2", 0)] == list(range(2, 258))
    await broker.release(resource, "G2")
    assert len(await broker.events(resource, "G2", 0)) == 256
    await broker.acknowledge(resource, "G2", 257)
    assert resource not in broker._resources


def test_sdk_codec_preserves_types_and_all_fields() -> None:
    codec = sdk_event_codec()
    original = AssistantMessage(content=[TextBlock(text="生の内容")], model="model", parent_tool_use_id=None)
    restored = codec.decode(codec.encode(original))
    assert type(restored) is type(original)
    assert restored == original
    assert type(restored.content[0]) is TextBlock
    assert set(codec.encode(original)["fields"]) == {field.name for field in dataclasses.fields(original)}
    with pytest.raises(TypeError):
        codec.encode(object())
    with pytest.raises(KeyError):
        codec.decode({"kind": "dataclass", "type": "untrusted", "fields": {}})


def test_sdk_codec_reads_events_saved_before_defaulted_fields_were_added() -> None:
    """SDKの更新前に保存したイベントには、後から増えた省略できるフィールドが無い。"""
    codec = sdk_event_codec()
    original = AssistantMessage(content=[TextBlock(text="旧版")], model="model", parent_tool_use_id=None)
    encoded = codec.encode(original)
    defaulted = next(
        field.name
        for field in dataclasses.fields(original)
        if field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING
    )
    del encoded["fields"][defaulted]
    assert codec.decode(encoded) == original
    unknown = codec.encode(original)
    unknown["fields"]["unknown_field"] = {"kind": "value", "value": None}
    with pytest.raises(ValueError):
        codec.decode(unknown)
    missing_required = codec.encode(original)
    del missing_required["fields"]["model"]
    with pytest.raises(ValueError):
        codec.decode(missing_required)


@pytest.mark.asyncio
async def test_read_does_not_block_write_or_ack() -> None:
    """raw readの待機中にもwriteを実行でき、移譲は両方の完了を待つ。"""
    broker = ResourceBroker("G1")
    started, written = asyncio.Event(), asyncio.Event()

    async def read() -> str:
        started.set()
        await written.wait()
        return "raw"

    async def write() -> None:
        written.set()

    resource = broker.register({"read": read, "write": write, "close": write})
    reading = asyncio.create_task(broker.call(resource, "G1", "read", {}))
    await started.wait()
    await asyncio.wait_for(broker.call(resource, "G1", "write", {}), 1)
    assert await asyncio.wait_for(reading, 1) == "raw"
    assert await broker.transfer(resource, "G1", "G2")
    await broker.release(resource, "G2")


@pytest.mark.asyncio
async def test_release_ends_call_waiting_for_event_space_without_consumer() -> None:
    """読む主体が止まった資源の解放は、イベントの空きを待つ実行中の操作を失敗させて完了する。

    空きは生じないため、解放は実行中の操作の終了を、操作はイベントの空きを待ち合って終わらなかった（2026-11-19の実測）。
    """
    broker = ResourceBroker("G1")
    closed: list[bool] = []
    resource = ""

    async def request() -> None:
        while True:
            await broker.emit(resource, {"kind": "sent"})

    async def close() -> None:
        closed.append(True)

    resource = broker.register({"request": request, "close": close})
    call = asyncio.create_task(broker.call(resource, "G1", "request", {}))
    async with asyncio.timeout(2):
        while broker.backlog(resource) != (256, 256):
            await asyncio.sleep(0)
        await broker.release(resource, "G1")
    with pytest.raises(PermissionError):
        await call
    assert closed == [True]


def test_sdk_codec_reads_previous_public_release_events() -> None:
    """旧公開版のイベントを復号し、保持した値と現行版で補う値を確認する。"""
    # 再生成: 2.287.0 を展開し、claude-agent-sdk==0.2.182で次の値を作る。
    # sdk_event_codec().encode() の結果をそのまま保存する。
    raw = [
        """{"kind":"dataclass","type":"AssistantMessage","fields":{
  "content": {"kind":"list","items":[
    {"kind":"dataclass","type":"TextBlock","fields":{"text":{"kind":"value","value":"旧版"}}}]},
  "model": {"kind":"value","value":"model"},
  "parent_tool_use_id": {"kind":"value","value":null},
  "error": {"kind":"value","value":null},
  "usage": {"kind":"value","value":null},
  "message_id": {"kind":"value","value":null},
  "stop_reason": {"kind":"value","value":null},
  "session_id": {"kind":"value","value":null},
  "uuid": {"kind":"value","value":null}
}}""",
        """{"kind":"dataclass","type":"ResultMessage","fields":{
  "subtype": {"kind":"value","value":"success"},
  "duration_ms": {"kind":"value","value":100},
  "duration_api_ms": {"kind":"value","value":50},
  "is_error": {"kind":"value","value":false},
  "num_turns": {"kind":"value","value":1},
  "session_id": {"kind":"value","value":"old-session"},
  "stop_reason": {"kind":"value","value":null},
  "total_cost_usd": {"kind":"value","value":null},
  "usage": {"kind":"value","value":null},
  "result": {"kind":"value","value":"完了"},
  "structured_output": {"kind":"value","value":null},
  "model_usage": {"kind":"value","value":null},
  "permission_denials": {"kind":"value","value":null},
  "deferred_tool_use": {"kind":"value","value":null},
  "errors": {"kind":"value","value":null},
  "api_error_status": {"kind":"value","value":null},
  "uuid": {"kind":"value","value":null},
  "terminal_reason": {"kind":"value","value":null},
  "origin": {"kind":"value","value":null}
}}""",
        """{"kind":"dataclass","type":"StreamEvent","fields":{
  "uuid": {"kind":"value","value":"event-1"},
  "session_id": {"kind":"value","value":"old-session"},
  "event": {"kind":"dict","items":{"type":{"kind":"value","value":"message_start"}}},
  "parent_tool_use_id": {"kind":"value","value":null}
}}""",
    ]
    assistant, result, stream = [sdk_event_codec().decode(json.loads(event)) for event in raw]
    assert assistant == AssistantMessage(content=[TextBlock(text="旧版")], model="model")
    assert result == ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="old-session",
        result="完了",
    )
    assert stream == StreamEvent(uuid="event-1", session_id="old-session", event={"type": "message_start"})
