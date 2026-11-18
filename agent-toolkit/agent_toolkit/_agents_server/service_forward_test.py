"""MCP中継が、接続先世代の停止中の要求を有限時間で配送未確定として返す契約と、管理サービスの起動・公開を確かめる。"""

import contextlib
import json
import os
import pathlib
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from agent_toolkit._agents_server import service_forward, service_rpc, service_transport
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._common import plugin_environment, runtime_environment, windows_job

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


def test_service_launch_inherits_job_when_breakaway_is_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """離脱を許さないJobの中では、離脱を指定した起動の拒否後に所属を継承して起動し、生存を保証しない旨を診断へ残す。"""
    detached = windows_job.HIDDEN_DETACHED_CREATIONFLAGS | windows_job.CREATE_SUSPENDED
    calls: list[int] = []

    service: Any = object()

    def spawn(_command: list[str], **kwargs: Any) -> Any:
        calls.append(kwargs["creationflags"])
        if kwargs["creationflags"] & windows_job.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "アクセスが拒否されました。")
        return service

    monkeypatch.setattr(service_forward.subprocess, "Popen", spawn)
    attempts = [{"creationflags": detached | windows_job.CREATE_BREAKAWAY_FROM_JOB}, {"creationflags": detached}]
    with (tmp_path / "stderr.log").open("wb") as stderr:
        assert service_forward._launch_service(["python"], attempts, stderr=stderr, cwd=tmp_path, env={}) is service
    assert calls == [detached | windows_job.CREATE_BREAKAWAY_FROM_JOB, detached]
    assert "離脱を許可していません" in (tmp_path / "stderr.log").read_text(encoding="utf-8")


_DETACHED = windows_job.HIDDEN_DETACHED_CREATIONFLAGS | windows_job.CREATE_SUSPENDED
_WINDOWS_ATTEMPTS = [{"creationflags": _DETACHED | windows_job.CREATE_BREAKAWAY_FROM_JOB}, {"creationflags": _DETACHED}]


@pytest.mark.parametrize("attempts", [_WINDOWS_ATTEMPTS, [{"start_new_session": True}]], ids=["windows", "posix"])
def test_service_launch_failure_carries_launch_diagnostic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, attempts: list[dict[str, Any]]
) -> None:
    """起動の失敗には、どの試行で失敗しても実行ファイル・作業ディレクトリ・指定を診断として添える。"""

    def spawn(_command: list[str], **_kwargs: Any) -> Any:
        raise FileNotFoundError(2, "指定されたファイルが見つかりません。")

    monkeypatch.setattr(service_forward.subprocess, "Popen", spawn)
    with (tmp_path / "stderr.log").open("wb") as stderr, pytest.raises(FileNotFoundError) as caught:
        service_forward._launch_service(["python"], attempts, stderr=stderr, cwd=tmp_path, env={})
    assert any(note.startswith("管理サービス起動: executable=python") for note in getattr(caught.value, "__notes__", ()))


def test_service_launch_denied_without_breakaway_is_not_reported_as_job_denial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """離脱の指定によらず拒否される起動（実行ファイルへの権限不足など）を、Jobによる離脱の拒否と案内しない。"""

    def spawn(_command: list[str], **_kwargs: Any) -> Any:
        raise PermissionError(5, "アクセスが拒否されました。")

    monkeypatch.setattr(service_forward.subprocess, "Popen", spawn)
    with (tmp_path / "stderr.log").open("wb") as stderr, pytest.raises(PermissionError) as caught:
        service_forward._launch_service(["python"], _WINDOWS_ATTEMPTS, stderr=stderr, cwd=tmp_path, env={})
    assert "離脱を許可していません" not in (tmp_path / "stderr.log").read_text(encoding="utf-8")
    assert any(note.startswith("管理サービス起動: executable=python") for note in getattr(caught.value, "__notes__", ()))


def _service_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    directory = tmp_path / "services" / "agents-server"
    monkeypatch.setattr(service_transport, "namespace", lambda _caller: directory)
    monkeypatch.setattr(service_forward, "code_identity", lambda _root: ("G1", "1.0.0"))
    GenerationRegistry(directory).reserve("G1", tmp_path, "1.0.0")
    return directory


def _launched_service(
    directory: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *, slow_first_ping: bool
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """起動するとreadyを書く管理サービスを代用し、ready記録と送ったpingを返す。指定すれば最初のpingへ1秒以内に応答しない。"""
    record = {"generation": "G1", "endpoint": "G1", "process": service_transport.process_identity(os.getpid())}
    service = SimpleNamespace(pid=0, returncode=None, poll=lambda: None, wait=lambda timeout=None: 0)

    def launch(*_args: Any, **_kwargs: Any) -> Any:
        (directory / "G1.json").write_text(json.dumps(record), encoding="utf-8")
        return service

    pings: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def connect(_address: str, _expected: str):
        yield SimpleNamespace(poll=lambda _timeout: not (slow_first_ping and len(pings) == 1))

    monkeypatch.setattr(service_forward, "_launch_service", launch)
    monkeypatch.setattr(plugin_environment, "prepared_python", lambda *_args: pathlib.Path(sys.executable))
    monkeypatch.setattr(service_transport, "connect", connect)
    monkeypatch.setattr(service_transport, "send", lambda _peer, payload: pings.append(payload))
    monkeypatch.setattr(service_transport, "receive", lambda _peer: {"result": {"generation": "G1"}})
    # 起動の失敗時の回収が、代用したプロセスのPIDで実在のプロセスグループへ届かないようにする。
    monkeypatch.setattr(service_forward.os, "killpg", lambda *_args: None)
    return record, pings


@pytest.mark.skipif(os.name == "nt", reason="POSIXの起動処理を代用する")
@pytest.mark.parametrize("running", [True, False], ids=["running", "launched"])
@pytest.mark.parametrize("defer", [True, False], ids=["deferred", "published"])
def test_ensure_service_publishes_only_without_deferral(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, running: bool, defer: bool
) -> None:
    """公開の保留を指定した起動確認には、起動済みでも新しく起動しても候補のまま返し、受付先として公開しない。"""
    directory = _service_directory(tmp_path, monkeypatch)
    record, _pings = _launched_service(directory, monkeypatch, slow_first_ping=False)
    if running:
        (directory / "G1.json").write_text(json.dumps(record), encoding="utf-8")
    published: list[str] = []

    def request_sync(target: dict[str, Any], operation: str, _arguments: dict, **_kwargs: Any) -> dict[str, Any]:
        published.append(operation)
        return dict(target, published=True)

    monkeypatch.setattr(service_rpc, "request_sync", request_sync)
    environment = {service_forward.DEFER_PUBLISH_ENV: "1"} if defer else {}
    returned = service_forward.ensure_service(runtime_environment.CallerContext(environment, tmp_path), tmp_path)
    assert published == ([] if defer else ["publish"])
    assert returned == (record if defer else dict(record, published=True))


@pytest.mark.skipif(os.name == "nt", reason="POSIXの起動処理を代用する")
def test_ensure_service_waits_past_slow_first_ping_until_ready_deadline(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """受付を始めた直後の管理サービスが最初のpingへ1秒以内に応答しなくても、起動の失敗とせずreadyの期限まで確かめ直す。"""
    directory = _service_directory(tmp_path, monkeypatch)
    record, pings = _launched_service(directory, monkeypatch, slow_first_ping=True)
    environment = {service_forward.DEFER_PUBLISH_ENV: "1"}
    assert service_forward.ensure_service(runtime_environment.CallerContext(environment, tmp_path), tmp_path) == record
    assert len(pings) == 2


# 受付を始めてから自分のready記録を置くまでの区間を、試験側の合図まで開けておく管理サービスの代役。
_SERVICE_STAND_IN = """
import json, os, pathlib, sys, time
sys.path.insert(0, {code!r})
from agent_toolkit._agents_server import service_transport
from agent_toolkit._common.atomic_file import atomic_write

directory, generation = pathlib.Path(sys.argv[-2]), sys.argv[-1]
signals = pathlib.Path({signals!r})
address = service_transport.endpoint(directory, generation)
with service_transport.listen(address) as listener:
    (signals / "listening").touch()
    deadline = time.monotonic() + 30
    while not (signals / "go").exists():
        if time.monotonic() > deadline:
            raise SystemExit("ready記録を置く合図が届かない")
        time.sleep(0.01)
    record = {{
        "generation": generation,
        "endpoint": address,
        "process": service_transport.process_identity(os.getpid()),
    }}
    atomic_write(directory / f"{{generation}}.json", json.dumps(record))
    while True:
        try:
            peer = listener.accept()
        except TimeoutError:
            continue
        with peer:
            operation = service_transport.receive(peer)["operation"]
            result = {{"generation": generation}} if operation == "ping" else record
            service_transport.send(peer, {{"result": result}})
        if operation == "publish":
            break
"""


@pytest.mark.windows
def test_restart_does_not_mistake_exited_generation_record_for_new_service(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じ世代の前回の記録が残る間に受付を始めた新しいサービスへ、前回の起動単位を期待して接続しない。

    2026-11-18、更新後処理の起動確認が、新しいサービスの受付へ終了した前回のサービスの起動単位を期待して接続し、
    「接続相手の起動世代が一致しません」で失敗して起動中のサービスを止めた（CI run 44400715958、2026-11-17のstheno）。
    """
    directory = tmp_path / "services" / "agents-server"
    root = tmp_path / "root"
    signals = tmp_path / "signals"
    signals.mkdir()
    (root / "scripts").mkdir(parents=True)
    code = pathlib.Path(service_forward.__file__).resolve().parents[2]
    (root / "scripts" / "launch.py").write_text(
        _SERVICE_STAND_IN.format(code=str(code), signals=str(signals)), encoding="utf-8"
    )
    monkeypatch.setattr(service_transport, "namespace", lambda _caller: directory)
    monkeypatch.setattr(service_forward, "code_identity", lambda _root: ("G1", "1.0.0"))
    monkeypatch.setattr(service_forward.plugin_environment, "prepared_python", lambda *_args: None)
    GenerationRegistry(directory).reserve("G1", root, "1.0.0")
    exited = "999999999:1"
    endpoint = service_transport.endpoint(directory, "G1")
    (directory / "G1.json").write_text(
        json.dumps({"generation": "G1", "endpoint": endpoint, "process": exited}), encoding="utf-8"
    )
    polls = 0

    def paced(seconds: float) -> None:
        # 新しいサービスの受付中に転送側が3回確かめ直してから、サービスへready記録を置かせる。
        nonlocal polls
        if (signals / "listening").exists():
            polls += 1
            if polls >= 3:
                (signals / "go").touch()
        time.sleep(seconds)

    monkeypatch.setattr(service_forward, "time", SimpleNamespace(monotonic=time.monotonic, sleep=paced))
    caller = runtime_environment.CallerContext(dict(os.environ), tmp_path)
    published = service_forward.ensure_service(caller, root, python=pathlib.Path(sys.executable))
    assert published["process"] != exited
    assert published == json.loads((directory / "G1.json").read_text(encoding="utf-8"))
