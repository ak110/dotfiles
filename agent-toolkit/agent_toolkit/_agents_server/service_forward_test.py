"""MCP中継が、接続先世代の停止中の要求を有限時間で配送未確定として返す契約と、管理サービスの起動・公開を確かめる。"""

import asyncio
import contextlib
import hashlib
import json
import os
import pathlib
import sys
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import anyio
import mcp.types
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agent_toolkit._agents_server import service_forward, service_rpc, service_transport
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._common import plugin_environment, runtime_environment, windows_job
from agent_toolkit._testing.codex_plugin_support import (
    _REAL_MCP_PREPARE_TIMEOUT,
    _real_mcp_environment,
    _stop_mcp_services,
)
from agent_toolkit._testing.plugin_environment_support import prepare_plugin_environment


def test_plugin_from_any_checkout_reaches_forwarding_with_shared_state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "dotfiles" / "agent-toolkit"
    root.mkdir(parents=True)
    environment = {"USERPROFILE" if os.name == "nt" else "HOME": str(home)}

    class EnteredForwarding(Exception):
        pass

    def enter(_caller):
        raise EnteredForwarding

    monkeypatch.setattr(service_forward, "_Forwarder", enter)
    monkeypatch.setattr(service_forward, "_parent_settings", lambda: None)
    with pytest.raises(EnteredForwarding):
        service_forward.forward(root=root, environment=environment)


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
    bridge, outputs = _bridge(monkeypatch, None)
    probed = threading.Event()
    monkeypatch.setattr(service_forward, "_probe", lambda _target: probed.set())
    bridge.watchdog.start()
    assert probed.wait(5)
    _stop(bridge)
    assert not outputs
    assert bridge.pending == {1: "tools/call"}


def test_service_launch_inherits_job_when_breakaway_is_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
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
    monkeypatch.setattr(service_forward, "code_identity", lambda _root, _directory: ("G1", "1.0.0"))
    GenerationRegistry(directory).reserve("G1", tmp_path, "1.0.0")
    return directory


def _launched_service(
    directory: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *, slow_first_ping: bool
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
@pytest.mark.parametrize("copy_missing", [False, True])
def test_restart_does_not_mistake_exited_generation_record_for_new_service(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, copy_missing: bool
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
    monkeypatch.setattr(service_forward, "code_identity", lambda _root, _directory: ("G1", "1.0.0"))
    monkeypatch.setattr(service_forward.plugin_environment, "prepared_python", lambda *_args: None)
    previous_root = tmp_path / "missing-copy" if copy_missing else root
    GenerationRegistry(directory).reserve("G1", previous_root, "1.0.0")
    exited = "999999999:1"
    endpoint = service_transport.endpoint(directory, "G1")
    (directory / "G1.json").write_text(
        json.dumps({"generation": "G1", "endpoint": endpoint, "process": exited}), encoding="utf-8"
    )
    polls = 0

    def paced(seconds: float) -> None:

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


_SETTLED = time.time() - 3600


def _settle(root: pathlib.Path, offset: int = 0) -> None:
    """書込から間もないファイルを、更新時刻の刻みの外の過去へ揃える。"""
    for path in [root, *root.rglob("*")]:
        os.utime(path, (_SETTLED + offset, _SETTLED + offset))


def _plugin_root(root: pathlib.Path, version: str, modules: dict[str, str]) -> pathlib.Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    for name, text in modules.items():
        path = root / "agent_toolkit" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _settle(root)
    return root


def _identity(root: pathlib.Path, tmp_path: pathlib.Path) -> str:
    return service_forward.code_identity(root, tmp_path / "services")[0]


def _distributed_identity(root: pathlib.Path) -> str:
    version = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    digest = hashlib.sha256(version.encode("utf-8"))
    for path in sorted(path for path in (root / "agent_toolkit").rglob("*.py") if not path.name.endswith("_test.py")):
        name = str(path.relative_to(root)).encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


_MODULES = {
    "__init__.py": "",
    "_agents_server/service.py": "VALUE = 1\n",
    "_agents_server/service_test.py": "def test_value() -> None: ...\n",
}


def test_code_identity_joins_same_code_and_separates_changed_code(tmp_path: pathlib.Path) -> None:
    claude = _plugin_root(tmp_path / "claude" / "1.0.0" / "agent-toolkit", "1.0.0", _MODULES)
    codex = _plugin_root(tmp_path / "codex" / "1.0.0" / "agent-toolkit", "1.0.0", _MODULES)
    joined = _identity(claude, tmp_path)
    assert _identity(codex, tmp_path) == joined
    (claude / "agent_toolkit" / "_agents_server" / "service_test.py").write_text(
        "def test_other() -> None: ...\n", encoding="utf-8"
    )
    (claude / "agent_toolkit" / "README.md").write_text("説明\n", encoding="utf-8")
    _settle(claude, 1)
    assert _identity(claude, tmp_path) == joined

    (claude / "agent_toolkit" / "_agents_server" / "service.py").write_text("VALUE = 2\n", encoding="utf-8")
    _settle(claude, 2)
    edited = _identity(claude, tmp_path)
    assert edited != joined
    assert edited == _identity(
        _plugin_root(tmp_path / "fresh" / "agent-toolkit", "1.0.0", {**_MODULES, "_agents_server/service.py": "VALUE = 2\n"}),
        tmp_path,
    )
    (claude / "agent_toolkit" / "added.py").write_text("", encoding="utf-8")
    _settle(claude, 3)
    assert _identity(claude, tmp_path) not in {joined, edited}
    assert _identity(_plugin_root(tmp_path / "next" / "agent-toolkit", "1.0.1", _MODULES), tmp_path) != joined


def test_code_identity_keeps_the_distributed_definition(tmp_path: pathlib.Path) -> None:
    synthetic = _plugin_root(
        tmp_path / "agent-toolkit",
        "2.0.0",
        {"a.py": "A\n", "a-b.py": "AB\n", "a/b.py": "B\n", "a/b_test.py": "T\n", ".hidden/x.py": "X\n", "z/__init__.py": ""},
    )
    real = pathlib.Path(service_forward.__file__).resolve().parents[2]
    for root in (synthetic, real):
        assert _identity(root, tmp_path) == _distributed_identity(root)


def _count_digests(monkeypatch: pytest.MonkeyPatch) -> list[pathlib.Path]:
    digested: list[pathlib.Path] = []
    digest = service_forward._code_digest

    def counting(root: pathlib.Path, version: str, paths: list[pathlib.Path]) -> str:
        digested.append(root)
        return digest(root, version, paths)

    monkeypatch.setattr(service_forward, "_code_digest", counting)
    return digested


def test_code_identity_reads_modules_only_when_one_changed(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _plugin_root(tmp_path / "agent-toolkit", "1.0.0", _MODULES)
    digested = _count_digests(monkeypatch)
    first = _identity(root, tmp_path)
    assert _identity(root, tmp_path) == first
    assert digested == [root]
    (root / "agent_toolkit" / "_agents_server" / "service.py").write_text("VALUE = 2\n", encoding="utf-8")
    _settle(root, 1)
    edited = _identity(root, tmp_path)
    assert _identity(root, tmp_path) == edited == _distributed_identity(root)
    assert edited != first
    assert digested == [root, root]


@pytest.mark.parametrize(
    "index",
    ["{", "[]", json.dumps({"version": 0, "entries": {}}), json.dumps({"version": 1, "entries": []}), "replace-values"],
)
def test_code_identity_ignores_a_broken_index(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, index: str) -> None:
    root = _plugin_root(tmp_path / "agent-toolkit", "1.0.0", _MODULES)
    _identity(root, tmp_path)
    path = tmp_path / "services" / "code-identity" / "index.json"
    if index == "replace-values":
        saved = json.loads(path.read_text(encoding="utf-8"))
        index = json.dumps({**saved, "entries": dict.fromkeys(saved["entries"], 1)})
    path.write_text(index, encoding="utf-8")
    digested = _count_digests(monkeypatch)
    assert _identity(root, tmp_path) == _distributed_identity(root)
    assert digested == [root]


def test_code_identity_recomputes_while_a_module_was_just_written(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _plugin_root(tmp_path / "agent-toolkit", "1.0.0", _MODULES)
    (root / "agent_toolkit" / "fresh.py").write_text("", encoding="utf-8")
    digested = _count_digests(monkeypatch)
    for _ in range(2):
        assert _identity(root, tmp_path) == _distributed_identity(root)
    assert digested == [root, root]
    _settle(root)
    for _ in range(2):
        assert _identity(root, tmp_path) == _distributed_identity(root)
    assert digested == [root, root, root]


def test_service_diagnostic_redacts_before_taking_tail(tmp_path: pathlib.Path) -> None:
    """末尾の開始位置がURLの認証部分に入っても、その断片を診断に残さない。"""
    token = "A" * 32 + "dummy-synthetic"
    tail = "z" * 65500
    log = tmp_path / "G1.stderr.log"
    log.write_text(f"https://{token}@example.org\n{tail}", encoding="utf-8")

    error = RuntimeError("起動失敗")
    service_forward._add_service_diagnostic(error, tmp_path, "G1")

    detail = getattr(error, "__notes__", [""])[0].split("\n", 1)[1]
    assert "先頭を省略" not in getattr(error, "__notes__", [""])[0]
    assert detail == f"https://[REDACTED]@example.org\n{tail}"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("", "診断本文は空です。"),
        ("z" * 2000, "全文は元の出力先で確認してください"),
    ],
)
def test_service_diagnostic_distinguishes_empty_log_from_empty_excerpt(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, content: str, expected: str
) -> None:
    from agent_toolkit._common import diagnostic_redaction  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(diagnostic_redaction, "RETAINED_BYTES", 1000)
    (tmp_path / "G1.stderr.log").write_text(content, encoding="utf-8")
    error = RuntimeError("起動失敗")
    service_forward._add_service_diagnostic(error, tmp_path, "G1")  # pylint: disable=protected-access

    note = getattr(error, "__notes__", [""])[0]
    assert expected in note
    assert note.startswith(f"管理サービスの診断: {tmp_path / 'G1.stderr.log'}")
    if content:
        assert "診断本文は空です。" not in note


@pytest.mark.skipif(os.name == "nt", reason="POSIXのプロセス群終了を代用する")
@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize("failure", [KeyboardInterrupt, ConnectionResetError])
def test_ensure_service_does_not_kill_candidate_when_publication_is_uncertain(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    accepted: bool,
    failure: type[BaseException],
) -> None:
    directory = _service_directory(tmp_path, monkeypatch)
    _launched_service(directory, monkeypatch, slow_first_ping=False)
    registry = GenerationRegistry(directory)
    killed: list[int] = []
    monkeypatch.setattr(service_forward.os, "killpg", lambda pid, _signal: killed.append(pid))

    def publish(target: dict[str, Any], operation: str, _arguments: dict, **_kwargs: Any) -> Any:
        assert operation == "publish"
        if accepted:
            registry.publish("G1", endpoint=target["endpoint"], process=target["process"])
        raise failure("公開応答を取得できない")

    monkeypatch.setattr(service_rpc, "request_sync", publish)
    caller = runtime_environment.CallerContext({}, tmp_path)
    with pytest.raises(failure, match="公開応答を取得できない"):
        service_forward.ensure_service(caller, tmp_path)
    assert not killed
    assert (registry.current() is not None) == accepted


@pytest.mark.skipif(sys.platform == "win32", reason="稼働中の配布物を一時的に改名して欠落を再現する")
@pytest.mark.timeout(180)
@pytest.mark.usefixtures("share_package_caches")
@pytest.mark.cpu_limit(3.0, reason="公開MCP入口から独立した管理サービスを起動し、欠落の応答をstdioで受け取る。")
def test_public_mcp_without_site_packages_reports_missing_service_copy(
    tmp_path: pathlib.Path, tmp_path_factory: pytest.TempPathFactory, host_environ: Callable[[], dict[str, str]]
) -> None:
    plugin = pathlib.Path(__file__).resolve().parents[2]
    environment = _real_mcp_environment(tmp_path, tmp_path_factory, host_environ)
    prepare_plugin_environment(plugin, environment, timeout=_REAL_MCP_PREPARE_TIMEOUT)
    caller = service_transport.plugin_caller(runtime_environment.CallerContext(environment, tmp_path), plugin)
    registry = GenerationRegistry(service_transport.namespace(caller))
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-S", str(plugin / "scripts/launch.py"), "agent_toolkit/agents_server_mcp.py"],
        env=environment,
        cwd=str(tmp_path),
    )

    async def probe() -> None:
        with (tmp_path / "public-mcp.stderr").open("w", encoding="utf-8") as stderr:
            async with (
                stdio_client(parameters, errlog=stderr) as (read, write),
                ClientSession(read, write) as client,
            ):
                with anyio.fail_after(45):
                    await client.initialize()
                    assert {"start", "send_to_user"} <= {tool.name for tool in (await client.list_tools()).tools}
                original = registry.current()
                assert original is not None
                root = pathlib.Path(original["root"])
                assert root != plugin and root.is_relative_to(pathlib.Path(environment["XDG_CACHE_HOME"]))
            async with (
                stdio_client(parameters, errlog=stderr) as (read, write),
                ClientSession(read, write) as client,
            ):
                with anyio.fail_after(45):
                    await client.initialize()
                    assert registry.current() == original
                    displaced = root.with_name("displaced")
                    root.rename(displaced)
                    try:
                        for name, arguments in (
                            ("start", {"mode": "explore", "prompt": "実行してはいけない"}),
                            ("send_to_user", {"message": "配送してはいけない"}),
                        ):
                            result = await client.call_tool(name, arguments)
                            assert result.is_error
                            message = "\n".join(item.text for item in result.content if isinstance(item, mcp.types.TextContent))
                            assert str(root) in message and f"{name}は実行していません" in message
                            assert "保全" in message and "原因" in message and "認可の範囲" in message
                            assert "停止を保留" in message
                    finally:
                        displaced.rename(root)

    try:
        asyncio.run(probe())
    finally:
        _stop_mcp_services(pathlib.Path(environment["XDG_STATE_HOME"]))
