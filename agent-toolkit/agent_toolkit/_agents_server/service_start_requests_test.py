import asyncio
import uuid

import pytest
from mcp.types import CallToolRequestParams, CallToolResult, TextContent

from agent_toolkit._agents_server import service_start_requests
from agent_toolkit._common.next_action import ActionableError


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [True, False])
async def test_lost_start_receipt_can_be_queried_without_second_start(tmp_path, monkeypatch, started):
    monkeypatch.setattr(service_start_requests, "process_alive", lambda process: process == "process")
    requests = service_start_requests.StartRequests(tmp_path, "process")
    release = asyncio.Event()
    entered = asyncio.Event()
    count = 0

    async def start(params):
        nonlocal count
        assert params.name == "start"
        assert params.arguments == {"cwd": str(tmp_path), "mode": "explore", "prompt": "調査"}
        count += 1
        entered.set()
        await release.wait()
        if started:
            return CallToolResult(content=[], structured_content={"session_id": "child", "root_session_id": "root"})
        return CallToolResult(content=[TextContent(type="text", text="起動しませんでした")], is_error=True)

    request_id = str(uuid.uuid4())
    query = CallToolRequestParams(name="start_status", arguments={"request_id": request_id})
    arguments = {"cwd": str(tmp_path), "mode": "explore", "prompt": "調査"}
    submit = CallToolRequestParams(name="start_submit", arguments={"request_id": request_id, "arguments": arguments})
    acknowledge = asyncio.Event()

    async def delayed_reserve(function, *args):
        if function.__name__ == "_reserve":
            await acknowledge.wait()
        return function(*args)

    monkeypatch.setattr(service_start_requests, "run_file_operation", delayed_reserve)
    receipt = asyncio.create_task(requests.call("owner", submit, start))
    await asyncio.sleep(0)
    receipt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await receipt
    try:
        acknowledge.set()
        await asyncio.wait_for(entered.wait(), timeout=1)
        reconnected = service_start_requests.StartRequests(tmp_path, "next-process")
        pending = (await reconnected.call("owner", query, start)).structured_content
        assert pending == {"request_id": request_id, "request_status": "pending", "cwd": str(tmp_path)}
        assert (await reconnected.call("owner", submit, start)).structured_content == pending
        other = (await reconnected.call("other", query, start)).structured_content
        assert other == {"request_id": request_id, "request_status": "not_found"}
        with pytest.raises(ActionableError, match="利用できません"):
            await reconnected.call("other", submit, start)
        modified = submit.model_copy(
            update={"arguments": {"request_id": request_id, "arguments": {**arguments, "prompt": "別の依頼"}}}
        )
        with pytest.raises(ActionableError, match="異なる入力"):
            await reconnected.call("owner", modified, start)
        release.set()
        await asyncio.gather(*requests.tasks)
        result = (await reconnected.call("owner", query, start)).structured_content
        assert result is not None
        assert count == 1
        if started:
            assert result["request_status"] == "started"
            assert result["session_id"] == "child"
            assert result["root_session_id"] == "root"
        else:
            assert result["request_status"] == "failed"
            assert result["diagnostics"] == ["起動しませんでした"]
    finally:
        await requests.close()
