"""OS由来の接続相手を、環境や操作本文の配送前に検収する。"""

import os
import socket
import sys
from multiprocessing import connection

import pytest

from agent_toolkit._agents_server import service_transport


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
