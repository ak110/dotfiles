"""MCPのstdioを同一アカウントの管理サービスへ中継する軽量な起動側。"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import service_rpc, service_transport
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._common import file_lock, plugin_environment, runtime_environment, windows_job
from agent_toolkit._common.diagnostic_redaction import redact_secrets
from agent_toolkit._common.next_action import report, with_next_action


def _add_service_diagnostic(error: Exception, directory: pathlib.Path, generation: str) -> None:
    """起動・ready・接続の失敗に、同じ世代の診断末尾を一度だけ添える。"""
    path = directory / f"{generation}.stderr.log"
    label = f"管理サービスの診断: {path}"
    if any(note.startswith(label) for note in getattr(error, "__notes__", ())):
        return
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, size - 65536))
            detail = stream.read(65536).decode("utf-8", errors="replace")
        limit = "（末尾65536バイト。先頭を省略）" if size > 65536 else ""
        error.add_note(f"{label}{limit}\n{redact_secrets(detail) if detail else '診断本文は空です。'}")
    except OSError as failure:
        error.add_note(f"{label}（取得失敗: {failure}）")


def code_identity(root: pathlib.Path) -> tuple[str, str]:
    """同じ配布版を合流し、開発checkoutの同版編集も別世代として識別する。"""
    manifest = root / ".claude-plugin" / "plugin.json"
    version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    digest = hashlib.sha256(version.encode("utf-8"))
    paths = sorted(path for path in (root / "agent_toolkit").rglob("*.py") if not path.name.endswith("_test.py"))
    for path in paths:
        name = str(path.relative_to(root)).encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest(), version


def ensure_service(
    caller: runtime_environment.CallerContext, root: pathlib.Path, *, python: pathlib.Path | None = None
) -> dict[str, Any]:
    """起動を世代ごとに合流し、MCPの通常導入経路だけでreadyへ到達させる。"""
    directory = service_transport.namespace(caller)
    generation, version = code_identity(root)
    registry = GenerationRegistry(directory)
    target = registry.reserve(generation, root, version)
    generation = target["generation"]
    root = pathlib.Path(target["root"])
    ready = directory / f"{generation}.json"
    with (directory / "startup.lock").open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle)
        try:
            current = registry.current()
            if current is not None and current["epoch"] > target["epoch"]:
                return current
            if ready.exists():
                record = json.loads(ready.read_text(encoding="utf-8"))
                pid = int(record["process"].split(":", 1)[0])
                try:
                    if service_transport.process_identity(pid) == record["process"]:
                        return service_rpc.request_sync(record, "publish", {})
                except FileNotFoundError, ProcessLookupError:
                    pass
            python = plugin_environment.prepared_python(root, caller.environment) or python or pathlib.Path(sys.executable)
            env = {
                key: value
                for key, value in caller.environment.items()
                if key
                in {
                    "PATH",
                    "PATHEXT",
                    "HOME",
                    "USERPROFILE",
                    "LOCALAPPDATA",
                    "APPDATA",
                    "SYSTEMROOT",
                    "WINDIR",
                    "TEMP",
                    "TMP",
                    "TMPDIR",
                    "XDG_STATE_HOME",
                    "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_DATA_HOME",
                    plugin_environment.RUNTIME_DATA_ENV,
                    "CLAUDE_PLUGIN_DATA",
                    "PLUGIN_DATA",
                }
            }
            command = [
                str(python or sys.executable),
                str(root / "scripts" / "launch.py"),
                "--project",
                str(root),
                "--agents-service",
                "agent_toolkit/_agents_server/service.py",
                str(directory),
                generation,
            ]
            flags: dict[str, Any] = {"start_new_session": True}
            with contextlib.ExitStack() as startup:
                job = None
                host_job_warning = ""
                if os.name == "nt":
                    # 成功後のserviceは転送側から独立する。準備失敗時だけ全所属を回収する。
                    job = startup.enter_context(windows_job.WindowsJob(kill_on_close=False))
                    creation_flags = windows_job.HIDDEN_DETACHED_CREATIONFLAGS | windows_job.CREATE_SUSPENDED
                    parent_limits = job.current_job_limit_flags()
                    if parent_limits is not None:
                        host_job_warning = (
                            f"起動元のWindows即時JobのLimitFlags={parent_limits:#x}。上位Jobの制限は変更しません。"
                            "離脱を許可しない上位Jobがある場合、そのJob終了後のサービス生存は保証しません。"
                        )
                    if parent_limits is None or parent_limits & 0x00000800:
                        creation_flags |= 0x01000000
                    elif not parent_limits & 0x00001000:
                        host_job_warning = (
                            "起動元のWindows Jobは離脱を許可していません。外側Jobの制限を継承して管理サービスを起動します。"
                            "そのJob終了後のサービス生存は保証しません。"
                        )
                    flags = {"creationflags": creation_flags}
                stderr = startup.enter_context((directory / f"{generation}.stderr.log").open("wb"))
                if host_job_warning:
                    host_job_warning = with_next_action(
                        host_job_warning,
                        next_action=(
                            "Claude/Codexを起動した端末・IDE・サービスのJob離脱可否を、その起動元の管理者へ確認してください。"
                            "確認できない場合は、起動元とこの診断を添えてagent-toolkitの管理者へ問い合わせてください。"
                        ),
                    )
                    print(host_job_warning, file=sys.stderr)
                    stderr.write((host_job_warning + "\n").encode("utf-8"))
                    stderr.flush()
                # サービスの寿命を転送側へ結び付けない。終了診断はサービスの起動単位が保存する。
                try:
                    process = subprocess.Popen(  # pylint: disable=consider-using-with
                        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr, cwd=root, env=env, **flags
                    )
                except OSError as error:
                    error.add_note(f"管理サービス起動: executable={command[0]} cwd={root} flags={flags}")
                    raise
                try:
                    if job is not None:
                        native_process: Any = process
                        # Windows版Popenが生成時に保持する元handleを使う。POSIX側の型推論には存在しない。
                        job.assign_suspended(
                            int(native_process._handle),  # pylint: disable=no-member,protected-access
                            process.pid,
                        )
                    deadline = time.monotonic() + 30.0
                    while True:
                        if ready.exists():
                            record = json.loads(ready.read_text(encoding="utf-8"))
                            try:
                                # Windowsのvenv launcherのPIDと実行本体のPIDは別になる。
                                # readyは自己申告だけで受理せず、OSのpeer identityで確かめる。
                                with service_transport.connect(record["endpoint"], record["process"]) as peer:
                                    service_transport.send(peer, {"kind": "rpc", "operation": "ping", "arguments": {}})
                                    if not peer.poll(1):
                                        raise TimeoutError("管理サービスのpingが未完了")
                                    response = service_transport.receive(peer)
                                if response != {"result": {"generation": generation}}:
                                    raise ValueError("管理サービスの世代が不正")
                            except FileNotFoundError, ConnectionRefusedError:
                                pass
                            else:
                                published = service_rpc.request_sync(record, "publish", {})
                                # stdio終了から独立した子を保持し、退出した旧supervisorは親の生存中にもreapする。
                                threading.Thread(target=process.wait, name="agents-service-reaper", daemon=True).start()
                                return published
                        if process.poll() is not None:
                            raise RuntimeError(f"管理サービスの起動に失敗: exit={process.returncode}")
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"管理サービスのreadyを確認できません。診断: {stderr.name}")
                        time.sleep(0.05)
                except BaseException:
                    if job is not None:
                        try:
                            job.terminate()
                        finally:
                            # Job所属の前に失敗した停止中のrootも、保持している元handleで回収する。
                            if process.poll() is None:
                                process.kill()
                            process.wait(timeout=5)
                    else:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            with contextlib.suppress(ProcessLookupError):
                                os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    raise
        except Exception as error:
            _add_service_diagnostic(error, directory, generation)
            raise
        finally:
            file_lock.release_lock(handle)


class _Forwarder:
    """送信を静止してから接続を移し、未確定の要求は再送しない。"""

    def __init__(self, caller: runtime_environment.CallerContext) -> None:
        self.caller = caller
        self.condition = threading.Condition()
        self.peer: Any = None
        self.pending: dict[str | int, str] = {}
        self.initialize: dict | None = None
        self.initialized = False
        self.input_closed = False
        self.closed = False
        self.failure: Exception | None = None
        self.progress = 0
        self._stdin = os.dup(0)
        self.reader = threading.Thread(target=self._read_input, daemon=True)

    @staticmethod
    def output(message: dict) -> None:
        sys.stdout.buffer.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()

    def _read_input(self) -> None:
        buffered = b""
        try:
            while chunk := os.read(self._stdin, 65536):
                buffered += chunk
                while b"\n" in buffered:
                    line, buffered = buffered.split(b"\n", 1)
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("MCPメッセージはJSON objectで指定する")
                    with self.condition:
                        self.condition.wait_for(lambda: self.peer is not None or self.closed)
                        if self.closed:
                            return
                        method = message.get("method")
                        if method == "initialize":
                            self.initialize = message
                        elif method == "notifications/initialized":
                            self.initialized = True
                        if isinstance(method, str) and "id" in message:
                            self.pending[message["id"]] = method
                        peer = self.peer
                        assert peer is not None  # wait_forで接続済みを確かめた
                        try:
                            service_transport.send(peer, message)
                        except OSError:
                            # 接続先の世代が終了した。入力の終了とは扱わず、送れなかった要求は未確定として
                            # 接続の切替後に応答し、以後の入力は切替先へ送る。
                            self.peer = None
            if buffered.strip():
                raise ValueError("MCPの最終メッセージが改行で終了していません")
        except Exception as error:
            self.failure = error
        finally:
            with self.condition:
                self.input_closed = True
                peer = self.peer
                self.peer = None
                self.condition.notify_all()
            if peer is not None:
                service_transport.disconnect(peer)

    def attach(self, peer: Any) -> bool:
        service_transport.send(
            peer,
            {
                "environment": dict(self.caller.environment),
                "cwd": str(self.caller.cwd),
                "connection_id": self.caller.connection_id,
                "parent_settings": self.caller.parent_settings,
                "forwarder_protocol": 1,
            },
        )
        if not peer.poll(30) or service_transport.receive(peer) != {"ready": True, "protocol": 1}:
            raise RuntimeError("管理サービスの接続準備が不正です")
        reinitialized = self.initialize is not None
        if self.initialize is not None:
            # initializeだけを新しいwireへ再構成する。tools/callやqueryは再送しない。
            message = {**self.initialize, "id": "reconnect-" + uuid.uuid4().hex}
            service_transport.send(peer, message)
            if not peer.poll(30):
                raise TimeoutError("接続切替先のMCP初期化が未完了です")
            response = service_transport.receive(peer)
            if response.get("id") != message["id"] or "error" in response:
                raise RuntimeError("接続切替先のMCP初期化に失敗しました")
            if self.initialized:
                service_transport.send(peer, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        with self.condition:
            if self.input_closed:
                return False
            self.peer = peer
            self.condition.notify_all()
        if reinitialized:
            self.output({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        return True

    def receive(self, peer: Any) -> None:
        while True:
            message = service_transport.receive(peer)
            if message.get("kind") == "redirect":
                with self.condition:
                    # ACKより後の入力は新しいwireがreadyになるまで未送信のまま保持する。
                    self.peer = None
                    service_transport.send(peer, {"kind": "redirect_ack"})
                continue
            if "method" not in message and "id" in message:
                with self.condition:
                    self.pending.pop(message["id"], None)
                    self.progress += 1
            self.output(message)

    def detach(self, peer: Any) -> None:
        service_transport.disconnect(peer)
        with self.condition:
            self.peer = None

    def close(self) -> None:
        with self.condition:
            if self.closed:
                return
            self.closed = True
            peer = self.peer
            self.peer = None
            self.condition.notify_all()
        if peer is not None:
            service_transport.disconnect(peer)
        os.close(self._stdin)
        if self.input_closed:
            self.reader.join(timeout=5)


def forward(
    *,
    root: pathlib.Path | None = None,
    python: pathlib.Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    """schemaを定義せず、現行受付への切替と認証済みJSONの中継だけを行う。"""
    caller = runtime_environment.CallerContext(
        dict(os.environ if environment is None else environment),
        pathlib.Path.cwd(),
        connection_id="mcp-" + uuid.uuid4().hex,
        parent_settings=_parent_settings(),
    )
    root = root or pathlib.Path(__file__).resolve().parents[2]
    bridge = _Forwarder(caller)
    registry = GenerationRegistry(service_transport.namespace(caller))
    previous_reconnect = None
    previous_progress = -1
    received_signal = 0
    target = None

    def stop_forwarder(signum: int, _frame: Any) -> None:
        nonlocal received_signal
        received_signal = signum
        raise KeyboardInterrupt

    signals = (signal.SIGINT,) if os.name == "nt" else (signal.SIGINT, signal.SIGTERM)
    previous_handlers = {signum: signal.signal(signum, stop_forwarder) for signum in signals}
    try:
        target = ensure_service(caller, root, python=python)
        bridge.reader.start()
        while True:
            try:
                with service_transport.connect(target["endpoint"], target["process"]) as peer:
                    try:
                        if not bridge.attach(peer):
                            return 0 if bridge.failure is None else 1
                        with contextlib.suppress(EOFError, OSError):
                            bridge.receive(peer)
                    finally:
                        bridge.detach(peer)
            except (EOFError, ConnectionError, FileNotFoundError) as error:
                # publishと初回readyの間にも旧世代は正常退出する。送信済み操作の判定は共通処理へ渡す。
                current = registry.current()
                if current is None or current["process"] == target["process"]:
                    error.add_note(
                        f"接続準備失敗時の世代: target={target['generation']} process={target['process']} "
                        f"current={None if current is None else current['generation']}"
                    )
                    raise
            if bridge.input_closed:
                if bridge.failure is not None:
                    raise bridge.failure
                return 0
            if bridge.pending:
                for identifier, method in tuple(bridge.pending.items()):
                    bridge.output(
                        {
                            "jsonrpc": "2.0",
                            "id": identifier,
                            "error": {
                                "code": -32000,
                                "message": (
                                    f"{method}の成否を確定できません。再送せず、showと既存記録で状態を確認してください。"
                                ),
                            },
                        }
                    )
                # 未確定の要求は再送せずエラーで返し、接続自体は現行世代へ切り替えて続ける。
                # ここで終了するとMCP接続が失われ、委譲元が以後の照会・回収をできなくなる（2026-11-17）。
                bridge.pending.clear()
            current = registry.current()
            if current is None or (previous_reconnect == current["process"] and previous_progress == bridge.progress):
                raise RuntimeError("管理サービスの接続が応答の無いまま繰り返し終了しました")
            previous_reconnect = current["process"]
            previous_progress = bridge.progress
            target = ensure_service(caller, pathlib.Path(current.get("root", root)), python=python)
    except KeyboardInterrupt:
        return 128 + (received_signal or signal.SIGINT)
    except (EOFError, OSError, ValueError, RuntimeError) as error:
        if target is not None:
            _add_service_diagnostic(error, registry.directory, target["generation"])
        traceback.print_exc()
        report(
            str(error),
            next_action="状態と起動診断を確認し、未確定の操作は再送せず既存登録簿・物理記録で確認してください。",
        )
        return 1
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
        bridge.close()


def _parent_settings() -> str | None:
    """転送側の親CLIのsettings層だけを取得し、共有serviceの親から継承しない。"""
    try:
        arguments = pathlib.Path(f"/proc/{os.getppid()}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return None
    settings = None
    for index, argument in enumerate(arguments):
        if argument.startswith(b"--settings="):
            settings = os.fsdecode(argument.removeprefix(b"--settings=")) or None
        elif argument == b"--settings":
            settings = os.fsdecode(arguments[index + 1]) if index + 1 < len(arguments) else None
    return settings
