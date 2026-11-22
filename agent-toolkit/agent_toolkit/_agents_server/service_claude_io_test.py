# SDKの所有taskと制御要求の内部境界を、実CLIの代わりに検証する。
# pylint: disable=protected-access

import asyncio

import anyio
import pytest
from claude_agent_sdk import SystemMessage

from agent_toolkit._agents_server import service_rpc
from agent_toolkit._agents_server.service_claude_io import SdkResource
from agent_toolkit._agents_server.service_io import Runtime
from agent_toolkit._agents_server.service_resources import ResourceBroker, sdk_event_codec


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_terminal_event_does_not_strand_released_resource(failure):
    """正常EOFと受信失敗の後にも、保存・ACKを終えて元世代を退出可能にする。"""
    broker = ResourceBroker("G1")
    saved = []

    async def save():
        saved.append(True)

    class Raw:
        def __init__(self, _options):
            pass

        async def connect(self, prompt=None):
            del prompt

        async def receive_messages(self):
            if failure:
                raise RuntimeError("connection failed")
            messages: tuple[SystemMessage, ...] = ()
            for message in messages:
                yield message

        async def disconnect(self):
            pass

    client = SdkResource.create(Runtime("G1", {"generation": "G1"}, broker, save), Raw, None)
    await client.connect()
    if failure:
        with pytest.raises(RuntimeError, match="connection failed"):
            await anext(client.receive_messages())
    else:
        assert [message async for message in client.receive_messages()] == []
    await client.disconnect()
    assert saved == [True]
    assert not broker._resources


@pytest.mark.asyncio
async def test_three_generations_keep_unacknowledged_event(monkeypatch):
    broker = ResourceBroker("G1")
    calls = []
    messages: asyncio.Queue[SystemMessage] = asyncio.Queue()

    class Raw:
        def __init__(self, _options):
            self.owner = None

        async def connect(self, prompt=None):
            del prompt
            self.owner = asyncio.current_task()
            calls.append("connect")

        async def query(self, prompt, session_id):
            del session_id
            calls.append(prompt)

        async def receive_messages(self):
            while True:
                yield await messages.get()

        async def disconnect(self):
            assert asyncio.current_task() is self.owner
            calls.append("disconnect")

    async def request(_origin, name, params):
        return await getattr(broker, name)(**params)

    monkeypatch.setattr(service_rpc, "request", request)
    first = SdkResource.create(Runtime("G1", {"generation": "G1"}, broker), Raw, None)
    await first.connect()
    await messages.put(SystemMessage(subtype="init", data={"all": [1, 2]}))
    stream = first.receive_messages()
    message = await anext(stream)
    assert message.data == {"all": [1, 2]}
    await stream.aclose()
    for old, generation in ((first, "G2"),):
        descriptor = await old.detach()
        assert descriptor["sequence"] == 0
        assert await old.resource.operation("transfer", expected_owner="G1", new_owner=generation)
        second = SdkResource.attach(Runtime(generation, {"generation": generation}, broker), descriptor)
    with pytest.raises(PermissionError):
        await first.disconnect()
    descriptor = await second.detach()
    assert await second.resource.operation("transfer", expected_owner="G2", new_owner="G3")
    third = SdkResource.attach(Runtime("G3", {"generation": "G3"}, broker), descriptor)
    assert descriptor["origin"]["generation"] == "G1"
    replay = third.receive_messages()
    assert await anext(replay) == message
    await third.query("next")
    await third.disconnect()
    await replay.aclose()
    await stream.aclose()
    assert calls == ["connect", "next", "disconnect"]


@pytest.mark.asyncio
async def test_pause_does_not_interrupt_save_and_detach_waits():
    broker = ResourceBroker("G1")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def save():
        entered.set()
        await finish.wait()

    async def close():
        pass

    identifier = broker.register({"close": close})
    await broker.emit(identifier, {"message": sdk_event_codec().encode(SystemMessage(subtype="init", data={}))})
    runtime = Runtime("G1", {"generation": "G1"}, broker, save)
    client = SdkResource.attach(runtime, {"identifier": identifier, "origin": runtime.target, "sequence": 0})
    stream = client.receive_messages()
    await anext(stream)
    finishing = asyncio.create_task(client.finish_message())
    await entered.wait()
    client.pause()
    assert client.transferred
    detach = asyncio.create_task(client.detach())
    await asyncio.sleep(0)
    assert not detach.done()
    finish.set()
    assert (await detach)["sequence"] == 1
    await finishing
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    await client.disconnect()


@pytest.mark.asyncio
async def test_owner_cancel_scope_cleanup_retry_and_cancelled_operations():
    broker = ResourceBroker("G1")
    sent, finish = asyncio.Event(), asyncio.Event()
    calls = []
    cleanup_calls = 0

    class Raw:
        def __init__(self, options):
            assert options == "options"
            self._query = self
            self.scope = None

        async def connect(self, prompt):
            assert prompt is None
            self.scope = anyio.CancelScope()
            # SDKと同じく、connectとdisconnectをまたいで同じtaskにcancel scopeを保持する。
            self.scope.__enter__()  # pylint: disable=unnecessary-dunder-call
            calls.append("connect")

        async def query(self, prompt, session_id):
            del session_id
            sent.set()
            await finish.wait()
            calls.append(prompt)

        async def receive_messages(self):
            await asyncio.Event().wait()
            yield

        async def disconnect(self):
            assert self.scope is not None
            self.scope.__exit__(None, None, None)
            calls.append("disconnect")

    async def cleanup():
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            raise OSError("cleanup")

    client = SdkResource.create(Runtime("G1", {"generation": "G1"}, broker), Raw, "options", cleanup)
    await client.connect()
    reading = asyncio.create_task(anext(client.receive_messages()))
    reading.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reading
    query = asyncio.create_task(client.query("body"))
    await sent.wait()
    query.cancel()
    with pytest.raises(asyncio.CancelledError):
        await query
    finish.set()
    with pytest.raises(ValueError):
        await client._call("__getattribute__")
    with pytest.raises(OSError, match="cleanup"):
        await client.disconnect()
    await client.disconnect()
    assert cleanup_calls == 2
    assert calls == ["connect", "body", "disconnect"]


@pytest.mark.asyncio
async def test_connect_failure_can_be_released():
    calls = []

    class Raw:
        def __init__(self, options):
            pass

        async def connect(self, prompt):
            raise OSError("connect")

        async def disconnect(self):
            calls.append("disconnect")

    broker = ResourceBroker("G1")
    client = SdkResource.create(Runtime("G1", {"generation": "G1"}, broker), Raw, None)
    with pytest.raises(OSError, match="connect"):
        await client.connect()
    await client.disconnect()
    assert calls == ["disconnect"]


@pytest.mark.asyncio
async def test_disconnect_discards_unread_events():
    broker = ResourceBroker("G1")

    async def close():
        pass

    identifier = broker.register({"close": close})
    await broker.emit(identifier, {"message": sdk_event_codec().encode(SystemMessage(subtype="init", data={}))})
    runtime = Runtime("G1", {"generation": "G1"}, broker)
    client = SdkResource.attach(runtime, {"identifier": identifier, "origin": runtime.target, "sequence": 0})

    await client.disconnect()

    assert identifier not in broker._resources
