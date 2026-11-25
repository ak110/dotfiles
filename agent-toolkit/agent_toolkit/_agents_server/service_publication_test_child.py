"""公開試験専用の旧世代子プロセス。  pyfltr: allow-comment"""

PREVIOUS_GENERATION = """
import asyncio
import contextlib
import os
import pathlib
import signal
import sys

from mcp.types import CallToolRequestParams

from agent_toolkit._agents_server import mcp_tools, service, service_io, service_transport, state, turn_state
from agent_toolkit._agents_server.service_claude_io import SdkResource
from agent_toolkit._common import runtime_environment

directory, parent, session_id, turn, failure, finish, mode = sys.argv[1:]
directory, failure, finish = pathlib.Path(directory), pathlib.Path(failure), pathlib.Path(finish)
listen = service_transport.listen


@contextlib.contextmanager
def failing_listen(address):
    with listen(address) as listener:
        accept = listener.accept

        def accept_or_fail():
            if failure.exists():
                raise OSError("試験で注入した受付の失敗")
            return accept()

        listener.accept = accept_or_fail
        yield listener


service_transport.listen = failing_listen


async def main():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signal.signal(signal.SIGTERM, lambda *_args: loop.call_soon_threadsafe(stop.set))
    serving = asyncio.create_task(service.run(directory, "old", stop=stop))
    while (managers := mcp_tools._SERVICE_MANAGERS) is None or managers.before_controls is None:
        if serving.done():
            await serving
        await asyncio.sleep(0.01)
    coordinator = managers.before_controls.__self__
    if mode == "delay_response":
        commit = coordinator.commit_update

        async def delay_response(token, current):
            await commit(token, current)
            pathlib.Path("commit-accepted").touch()
            await asyncio.sleep(60)

        coordinator.commit_update = delay_response
    caller = runtime_environment.CallerContext(dict(os.environ, CODEX_THREAD_ID=parent), pathlib.Path.cwd())
    with service_io.bind(coordinator._runtime), runtime_environment.bind(caller):
        async with managers.scope(CallToolRequestParams(name="list", arguments={}), identifier_override=parent) as manager:
            session = manager.sessions[session_id] = state.SessionState(
                session_id,
                str(pathlib.Path.cwd()),
                engine="claude" if mode == "raw" else "codex",
                turn_end=turn_state.TurnEnd[turn],
                publish_registry=True,
            )
            if mode == "raw":
                async def close():
                    if not pathlib.Path("allow-close").exists():
                        pathlib.Path("close-attempted").touch()
                        raise OSError("試験で注入したcloseの失敗")
                    pathlib.Path("raw-closed").touch()

                runtime = coordinator._runtime
                identifier = runtime.broker.register({"close": close})
                await runtime.broker.emit(identifier, {"end": True})
                client = SdkResource(service_io.Resource(runtime, identifier, runtime.target))
                manager._backend("claude")._sdk_clients[session_id] = client
            await manager.save_control_state()
    while not finish.exists() and not serving.done():
        await asyncio.sleep(0.05)
    session.record_turn_completed()
    await serving


pathlib.Path(__file__).with_suffix(".ready").touch()
asyncio.run(main())
"""
