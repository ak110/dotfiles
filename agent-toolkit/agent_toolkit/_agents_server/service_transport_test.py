"""OS由来の接続相手を、環境や操作本文の配送前に検収する。"""

import os
import socket
import sys
from multiprocessing import connection
from types import SimpleNamespace

import pytest

from agent_toolkit._agents_server import service_transport, windows_service_transport


@pytest.mark.parametrize("pid,code,missing", [(123, 87, True), (123, 5, False), (0, 87, False)])
def test_windows_process_identity_distinguishes_missing_process_from_api_failure(monkeypatch, pid, code, missing):
    """消滅PIDだけを再起動可能とし、拒否と不正PIDを消滅へ変換しない。"""
    error = OSError("injected OpenProcess failure")
    monkeypatch.setattr(windows_service_transport, "_api", lambda: (SimpleNamespace(OpenProcess=lambda *_: None), None))
    monkeypatch.setattr(
        windows_service_transport, "_windows", SimpleNamespace(WinError=lambda _: error, get_last_error=lambda: code)
    )
    with pytest.raises(ProcessLookupError if missing else OSError) as caught:
        windows_service_transport.process_identity(pid)
    if missing:
        assert caught.value.__cause__ is error
    else:
        assert caught.value is error


@pytest.mark.skipif(sys.platform != "linux", reason="実SO_PEERCREDが必要")
@pytest.mark.parametrize("mismatch", ["account", "process"])
def test_peer_identity_rejects_wrong_account_or_process_before_payload(
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    first, second = socket.socketpair()
    with first, second, connection.Connection(os.dup(first.fileno())) as peer:
        expected = service_transport.process_identity(os.getpid())
        with monkeypatch.context() as changed:
            if mismatch == "account":
                other_uid = os.geteuid() + 1
                changed.setattr(service_transport.os, "geteuid", lambda: other_uid)
            else:
                expected = "different-process-generation"
            with pytest.raises(PermissionError):
                service_transport.authenticate(peer, server=False, expected_process=expected)
        assert (
            service_transport.authenticate(
                peer,
                server=False,
                expected_process=service_transport.process_identity(os.getpid()),
            )
            == os.getpid()
        )
        assert not peer.poll()
