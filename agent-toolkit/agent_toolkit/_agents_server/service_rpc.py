"""認証済みの世代間接続で、1回の内部操作とその確定結果を交換する。"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from agent_toolkit._agents_server import service_transport
from agent_toolkit._agents_server.session_errors import DelegateBackendError


def _result(response: dict[str, Any]) -> Any:
    if "error" in response:
        kind = DelegateBackendError if response.get("category") == "backend" else RuntimeError
        raise kind(response["error"])
    return response["result"]


def request_sync(target: dict[str, Any], operation: str, arguments: dict[str, Any], *, timeout: float = 270) -> Any:
    """同期の登録処理を現行writerへ渡し、応答喪失後の変更を再送しない。"""
    with service_transport.connect(target["endpoint"], target["process"]) as peer:
        service_transport.send(peer, {"kind": "rpc", "operation": operation, "arguments": arguments})
        if not peer.poll(timeout):
            raise TimeoutError(f"管理サービスの内部操作が未確定: {operation}")
        response = service_transport.receive(peer)
    return _result(response)


async def request(target: dict[str, Any], operation: str, arguments: dict[str, Any], *, timeout: float = 270) -> Any:
    with service_transport.connect(target["endpoint"], target["process"]) as peer:
        async with service_transport.AsyncPeer(peer) as owned:
            await owned.call(service_transport.send, {"kind": "rpc", "operation": operation, "arguments": arguments})

            def receive(connection) -> dict[str, Any]:
                if not connection.poll(timeout):
                    raise TimeoutError(f"管理サービスの内部操作が未確定: {operation}")
                return service_transport.receive(connection)

            response = await owned.call(receive)
    return _result(response)


def discovery(directory: pathlib.Path, generation: str) -> dict[str, Any]:
    """元世代の接続記録を読む。requestの認証で、実際の相手が記録の世代と一致するか確かめる。"""
    return json.loads((directory / f"{generation}.json").read_text(encoding="utf-8"))
