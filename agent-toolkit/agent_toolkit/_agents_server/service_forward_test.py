"""MCP中継が、接続先世代の停止中の要求を有限時間で配送未確定として返す契約を確かめる。"""

import os
import pathlib
import threading
import time
from typing import Any

import pytest

from agent_toolkit._agents_server import service_forward
from agent_toolkit._common import runtime_environment

# 中継の監視と受信の内部状態を、公開のstdio経路を起動せずに確かめる。
# pylint: disable=protected-access


def _bridge(monkeypatch: pytest.MonkeyPatch, probe: str | None) -> tuple[service_forward._Forwarder, list[dict]]:
    monkeypatch.setattr(service_forward, "_STALL_SECONDS", 0.05)
    monkeypatch.setattr(service_forward, "_probe", lambda _target: probe)
    bridge = service_forward._Forwarder(runtime_environment.CallerContext({}, pathlib.Path.cwd()))
    outputs: list[dict] = []
    monkeypatch.setattr(bridge, "output", outputs.append)
    bridge.target = {"generation": "G1", "process": "123:owner", "endpoint": "unused"}
    with bridge.condition:
        bridge.pending[1] = "tools/call"
        bridge.sent_at[1] = time.monotonic() - 1
    return bridge, outputs


def _stop(bridge: service_forward._Forwarder) -> None:
    with bridge.condition:
        bridge.closed = True
        bridge.condition.notify_all()
    bridge.watchdog.join(5)
    os.close(bridge._stdin)


def test_stalled_request_returns_one_actionable_error_and_drops_late_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """接続先が生存確認に応答しない間の要求は1回だけエラーで返り、後から届いた応答は同じidへ重ねない。"""
    bridge, outputs = _bridge(monkeypatch, "TimeoutError: 応答なし")
    bridge.watchdog.start()
    deadline = time.monotonic() + 5
    while not outputs and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    replies = iter([{"jsonrpc": "2.0", "id": 1, "result": {}}])

    def receive(_peer: Any) -> dict:
        try:
            return next(replies)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(service_forward.service_transport, "receive", receive)
    with pytest.raises(EOFError):
        bridge.receive(object())
    _stop(bridge)
    assert len(outputs) == 1
    error = outputs[0]["error"]
    assert outputs[0]["id"] == 1
    assert "未確定" in error["message"] and "server_generations" in error["data"]["next_action"]
    assert not bridge.pending and not bridge.abandoned


def test_request_to_live_generation_is_not_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    """生存確認に応答する世代への長い要求は打ち切らず、応答をそのまま返す。"""
    bridge, outputs = _bridge(monkeypatch, None)
    probed = threading.Event()
    monkeypatch.setattr(service_forward, "_probe", lambda _target: probed.set())
    bridge.watchdog.start()
    assert probed.wait(5)
    _stop(bridge)
    assert not outputs
    assert bridge.pending == {1: "tools/call"}
