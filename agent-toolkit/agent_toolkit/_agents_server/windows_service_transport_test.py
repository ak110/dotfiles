"""Windowsの実named pipeとOSが返す接続相手の世代を検証する。"""

import os
import threading
import uuid
from multiprocessing import connection

import pytest

from agent_toolkit._agents_server import windows_service_transport as transport

pytestmark = pytest.mark.skipif(os.name != "nt", reason="実Win32 APIが必要")


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
                with pytest.raises(PermissionError):
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
    """未接続のacceptもclose要求後に有限時間で解除する。"""
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
    """開けるプロセスの起動単位は、開かずにプロセス一覧で判定しても一致し、作成時刻が1つ違えば別の世代とする。"""
    identity = transport.process_identity(os.getpid())
    pid, created = identity.split(":")
    assert transport.listed_process_alive(identity)
    assert not transport.listed_process_alive(f"{pid}:{int(created) + 1}")
