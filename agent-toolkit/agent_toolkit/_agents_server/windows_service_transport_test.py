"""Windowsの実named pipeとOSが返す接続相手の世代を検証する。"""

import os
import subprocess
import sys
import threading
import uuid
from multiprocessing import connection

import pytest

from agent_toolkit._agents_server import service_transport
from agent_toolkit._agents_server import windows_service_transport as transport
from agent_toolkit._agents_server.session_errors import ProcessIdentityMismatch

pytestmark = [pytest.mark.windows, pytest.mark.skipif(os.name != "nt", reason="実Win32 APIが必要")]


def test_pipe_authentication_and_bytes() -> None:
    """同SIDの双方向接続を認証し、byte messageだけを配送する。"""
    address = rf"\\.\pipe\agent-toolkit-test-{uuid.uuid4()}"
    errors = []
    with transport.listen(address) as listener:

        def serve() -> None:
            try:
                with listener.accept() as peer:
                    transport.authenticate(peer, server=True)
                    assert peer.recv_bytes() == b'{"test":true}'
                    peer.send_bytes(b'{"ok":true}')
            except BaseException as exc:
                errors.append(exc)

        reader = threading.Thread(target=serve)
        reader.start()
        try:
            with connection.Client(address, family="AF_PIPE", authkey=None) as peer:
                transport.authenticate(peer, server=False, expected_process=transport.process_identity(os.getpid()))
                with pytest.raises(ProcessIdentityMismatch):
                    transport.authenticate(peer, server=False, expected_process="wrong-generation")
                peer.send_bytes(b'{"test":true}')
                assert peer.poll(5)
                assert peer.recv_bytes() == b'{"ok":true}'
        finally:
            listener.close()
            reader.join(5)
        assert not reader.is_alive()
        assert not errors


def test_listener_close_releases_pending_accept() -> None:
    address = rf"\\.\pipe\agent-toolkit-test-{uuid.uuid4()}"
    with transport.listen(address) as listener:
        finished = threading.Event()

        def accept() -> None:
            try:
                listener.accept().close()
            except OSError:
                pass
            finally:
                finished.set()

        reader = threading.Thread(target=accept)
        reader.start()
        listener.close()
        reader.join(5)
        assert finished.is_set()
        assert not reader.is_alive()


def test_accept_timeout_keeps_listening_instance() -> None:
    """受理待ちの時間切れで呼出側へ戻り、その間も接続側が待受instanceへ到達できる。

    時間切れが無いと、旧世代は次の接続を受理するまで退出条件を再確認できず残り続けた
    （2026-11-17のWindows観測）。
    """
    address = rf"\\.\pipe\agent-toolkit-test-{uuid.uuid4()}"
    with transport.listen(address) as listener:
        with pytest.raises(TimeoutError):
            listener.accept(timeout=0.2)
        with connection.Client(address, family="AF_PIPE", authkey=None) as client, listener.accept(timeout=5) as peer:
            client.send_bytes(b"ping")
            assert peer.poll(5)
            assert peer.recv_bytes() == b"ping"


def test_listed_process_alive_matches_open_identity_of_running_process() -> None:
    identity = transport.process_identity(os.getpid())
    pid, created = identity.split(":")
    assert transport.listed_process_alive(identity)
    assert not transport.listed_process_alive(f"{pid}:{int(created) + 1}")


def test_process_alive_rejects_exited_process_with_open_handle() -> None:
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) as child:
        try:
            identity = transport.process_identity(child.pid)
            assert service_transport.process_alive(identity)
        finally:
            child.terminate()
            child.wait(timeout=10)
        assert not service_transport.process_alive(identity)
