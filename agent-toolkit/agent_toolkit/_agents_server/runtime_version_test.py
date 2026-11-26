import asyncio

import pytest

from agent_toolkit._agents_server import runtime_version, service, service_rpc
from agent_toolkit._agents_server.service_generations import GenerationRegistry


@pytest.mark.asyncio
@pytest.mark.windows
async def test_agents_version_comes_from_live_accepting_generation(tmp_path):
    directory = tmp_path / "services" / "agents-server"
    registry = GenerationRegistry(directory)
    generation = "a" * 64
    registry.reserve(generation, tmp_path, "1.2.3")
    stop = asyncio.Event()
    server = asyncio.create_task(service.run(directory, generation, stop=stop))
    try:
        async with asyncio.timeout(10):
            while not (directory / f"{generation}.json").is_file():
                if server.done():
                    await server
                    pytest.fail("受付世代が起動前に終了した")
                await asyncio.sleep(0.01)
        target = service_rpc.discovery(directory, generation)
        assert await asyncio.to_thread(runtime_version.agents_running, directory) is None
        await asyncio.to_thread(service_rpc.request_sync, target, "publish", {}, timeout=5)
        assert await asyncio.to_thread(runtime_version.agents_running, directory) == f"1.2.3/{generation}"
    finally:
        stop.set()
        await asyncio.wait_for(server, timeout=10)
    assert runtime_version.agents_running(directory) is None
