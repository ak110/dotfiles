"""Codex 0.166の公開RPCを代用し、プロセス境界で起動管理の受入を確認する。

通常TUI・control proxy・daemonコマンドを別プロセスで実行する。
RPCの形式は生成schemaと隔離したnative TUIの観測に基づき、仕事は所有fixture内の成果物作成で代用する。
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import sys
import time
import uuid
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from agent_toolkit._common import process_tree
from filelock import FileLock
from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, CloseConnection, RejectConnection, Request, TextMessage

_ROOT = pathlib.Path(os.environ["CODEX_PEER_ROOT"])
_MODE = os.environ["CODEX_PEER_MODE"]
_PID_FILE = _ROOT / "app-server-daemon" / "daemon.pid"


def _read(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{os.getpid()}")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def _thread_file(identifier: str) -> pathlib.Path:
    return _ROOT / "threads" / f"{uuid.UUID(identifier)}.json"


def _control(action: str) -> None:
    with FileLock(str(_ROOT / "control.lock"), timeout=5):
        if action == "start" and _PID_FILE.exists():
            return
        if action == "restart":
            for path in (_ROOT / "threads").glob("*.json"):
                record = _read(path)
                if record["turns"] and record["turns"][-1]["status"] == "inProgress":
                    record["turns"][-1]["status"] = "interrupted"
                    _write(path, record)
            if _MODE == "partial":
                _PID_FILE.write_text("", encoding="utf-8")
                deadline = time.monotonic() + 5
                while not (_ROOT / "metadata-observed").exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("世代の公開途中を観測できなかった")
                    time.sleep(0.01)
            if _MODE == "corrupt":
                _PID_FILE.write_text("{invalid-json", encoding="utf-8")
                return
        # processIdentityの中身は代用値とし、読取側が解釈しない世代の標識だけを検証する。
        # Windowsのnativeは任意項目のprocessIdentityを書かず、processStartTimeだけを持つ。
        if os.environ.get("CODEX_PEER_PID_FORMAT") == "windows":
            _write(_PID_FILE, {"pid": os.getpid(), "processStartTime": str(uuid.uuid4())})
        else:
            _write(_PID_FILE, {"pid": os.getpid(), "processIdentity": {"fixtureGeneration": str(uuid.uuid4())}})


class _Wire:
    def __init__(self, reader: BinaryIO | socket.SocketIO, writer: BinaryIO | socket.SocketIO, *, client: bool) -> None:
        self.reader, self.writer = reader, writer
        self.codec = WSConnection(ConnectionType.CLIENT if client else ConnectionType.SERVER)
        self.pending: list[dict[str, Any]] = []
        self.fragments = ""

    def send(self, value: dict[str, Any]) -> None:
        data = json.dumps(value)
        middle = len(data) // 2
        self.writer.write(self.codec.send(TextMessage(data=data[:middle], message_finished=False)))
        self.writer.write(self.codec.send(TextMessage(data=data[middle:])))
        self.writer.flush()

    def receive(self) -> dict[str, Any]:
        while not self.pending:
            data = getattr(self.reader, "read1", self.reader.read)(65536)
            if not data:
                raise EOFError
            self.codec.receive_data(data)
            for event in self.codec.events():
                if isinstance(event, Request):
                    self.writer.write(self.codec.send(AcceptConnection()))
                    self.writer.flush()
                if isinstance(event, CloseConnection):
                    raise EOFError
                if isinstance(event, TextMessage):
                    self.fragments += event.data
                    if event.message_finished:
                        self.pending.append(json.loads(self.fragments))
                        self.fragments = ""
        return self.pending.pop(0)


def _view(record: dict[str, Any]) -> dict[str, Any]:
    active = record["turns"] and record["turns"][-1]["status"] == "inProgress"
    return {
        "id": record["id"],
        "cwd": record["cwd"],
        "model": "gpt-6.1-sol",
        "modelProvider": "peer",
        "status": {"type": "active" if active else "idle"},
        "turns": record["turns"],
    }


def _complete(wire: _Wire, record: dict[str, Any]) -> None:
    record["turns"][-1]["status"] = "completed"
    _write(_thread_file(record["id"]), record)
    _write(_ROOT / "results" / f"{record['id']}.json", {"id": record["id"], "cwd": record["cwd"], "effort": record["effort"]})
    wire.send({"method": "turn/completed", "params": {"threadId": record["id"], "turn": record["turns"][-1]}})


def _proxy() -> None:
    wire = _Wire(sys.stdin.buffer, sys.stdout.buffer, client=False)
    approval: dict[str, str] = {}
    elicitation: dict[str, Any] = {}
    while True:
        try:
            message = wire.receive()
        except EOFError:
            return
        method, params = message.get("method"), message.get("params", {})
        identifier = message.get("id")
        if method is None:
            if elicitation and identifier == 2:
                assert message["result"]["action"] == "accept"
                record = _read(_thread_file(elicitation["thread"]))
                record["children"] += 1
                _write(_thread_file(record["id"]), record)
                _write(_ROOT / "children" / f"{record['id']}.json", {"direct_parent": record["id"]})
                wire.send({"id": elicitation["request"], "result": {"content": [], "isError": False}})
                elicitation.clear()
            if approval and identifier == 1:
                record = _read(_thread_file(approval["thread"]))
                assert record["turns"][-1]["id"] == approval["turn"]
                assert message["result"]["decision"] == "accept"
                _complete(wire, record)
                approval.clear()
            continue
        if method == "initialized":
            continue
        if method == "initialize":
            result: Any = {"userAgent": "codex-test-peer"}
        elif method == "thread/start":
            thread = str(uuid.uuid4())
            record = {
                "id": thread,
                "cwd": params["cwd"],
                "effort": params["config"]["model_reasoning_effort"],
                "policy": "on-request" if _MODE == "approval" else "never",
                "sandbox": {"type": "readOnly", "networkAccess": False},
                "turns": [],
                "starts": 0,
                "children": 0,
                "message_id": None,
            }
            _write(_thread_file(thread), record)
            result = {
                "thread": _view(record),
                "approvalPolicy": record["policy"],
                "approvalsReviewer": "user",
                "sandbox": record["sandbox"],
            }
        elif method == "thread/resume":
            if _MODE == "error":
                wire.send({"id": identifier, "error": {"code": -32000, "message": "injected resume failure"}})
                continue
            record = _read(_thread_file(params["threadId"]))
            result = {
                "thread": _view(record),
                "approvalPolicy": record["policy"],
                "approvalsReviewer": "user",
                "sandbox": record["sandbox"],
            }
        elif method == "thread/turns/list":
            record = _read(_thread_file(params["threadId"]))
            result = {"data": list(reversed(record["turns"]))[:1], "nextCursor": None}
        elif method == "mcpServerStatus/list":
            result = (
                {"data": [], "nextCursor": "agents"}
                if params.get("cursor") is None
                else {
                    "data": [{"name": "agents_server", "tools": {"list": {"name": "list", "inputSchema": {"type": "object"}}}}],
                    "nextCursor": None,
                }
            )
        elif method == "mcpServer/tool/call":
            record = _read(_thread_file(params["threadId"]))
            if _MODE in {"elicitation", "elicitation-cancel"} and record["starts"]:
                elicitation = {"request": identifier, "thread": record["id"]}
                wire.send(
                    {
                        "id": 2,
                        "method": "mcpServer/elicitation/request",
                        "params": {
                            "threadId": record["id"],
                            "turnId": record["turns"][-1]["id"],
                            "serverName": "agents_server",
                        },
                    }
                )
                continue
            if _MODE == "mcp-error" and record["starts"]:
                wire.send({"id": identifier, "result": {"content": [], "isError": True}})
                continue
            record["children"] += 1
            _write(_thread_file(record["id"]), record)
            _write(_ROOT / "children" / f"{record['id']}.json", {"direct_parent": record["id"]})
            result = {"content": [], "structuredContent": {"root_session_id": record["id"]}, "isError": False}
        elif method == "turn/start":
            record = _read(_thread_file(params["threadId"]))
            if record["starts"]:
                assert params["cwd"] == record["cwd"] and params["effort"] == record["effort"]
                assert params["approvalPolicy"] == record["policy"] and params["sandboxPolicy"] == record["sandbox"]
                assert params["clientUserMessageId"] != record["message_id"]
            else:
                record["message_id"] = params["clientUserMessageId"]
            record["starts"] += 1
            record["turns"].append({"id": str(uuid.uuid4()), "status": "inProgress", "items": []})
            _write(_thread_file(record["id"]), record)
            wire.send({"id": identifier, "result": {"turn": record["turns"][-1]}})
            wire.send({"method": "turn/started", "params": {"threadId": record["id"], "turn": record["turns"][-1]}})
            if _MODE == "approval":
                approval = {"thread": record["id"], "turn": record["turns"][-1]["id"]}
                wire.send(
                    {
                        "id": 1,
                        "method": "item/commandExecution/requestApproval",
                        "params": {
                            "threadId": record["id"],
                            "turnId": record["turns"][-1]["id"],
                            "itemId": "owned-command",
                            "command": "create owned fixture output",
                            "cwd": record["cwd"],
                            "reason": None,
                        },
                    }
                )
            elif _MODE == "completed" or record["starts"] > 1 and not (_MODE == "late-error" and record["starts"] == 3):
                _complete(wire, record)
            continue
        elif method == "turn/interrupt":
            record = _read(_thread_file(params["threadId"]))
            record["turns"][-1]["status"] = "interrupted"
            _write(_thread_file(record["id"]), record)
            wire.send({"id": identifier, "result": {}})
            wire.send({"method": "turn/completed", "params": {"threadId": record["id"], "turn": record["turns"][-1]}})
            continue
        elif method == "config/value/write" and _MODE == "lost-response":
            effect = _ROOT / "config-write.json"
            count = _read(effect)["count"] if effect.exists() else 0
            _write(effect, {"count": count + 1, "params": params})
            # 公開RPCを適用した後だけ故障を起こし、応答喪失を未実行と取り違えないか確認する。
            deadline = time.monotonic() + 5
            while not (_ROOT / "restarted").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("適用済み要求の後に再起動しなかった")
                time.sleep(0.01)
            return
        else:
            result = {"config": {"model": "gpt-6.1-sol"}}
        wire.send({"id": identifier, "result": result})


def _actor(arguments: list[str]) -> int:
    endpoint = arguments[arguments.index("--remote") + 1]
    token_name = arguments[arguments.index("--remote-auth-token-env") + 1]
    url = urlsplit(endpoint)
    assert url.hostname == "127.0.0.1"
    cwd = arguments[arguments.index("--cd") + 1]
    effort = arguments[arguments.index("--config") + 1].split("=", 1)[1].strip('"')
    resumed = "resume" in arguments
    if "--worktree" in arguments and not resumed:
        cwd = str(pathlib.Path(cwd) / "derived-worktree")
        pathlib.Path(cwd).mkdir(parents=True, exist_ok=True)
    _write(_ROOT / "actors" / f"{os.getpid()}.json", arguments)
    with socket.create_connection((url.hostname, url.port), timeout=10) as unauthorized:
        rejected = WSConnection(ConnectionType.CLIENT)
        unauthorized.sendall(rejected.send(Request(host="localhost", target="/")))
        rejected.receive_data(unauthorized.recv(65536))
        assert any(isinstance(event, RejectConnection) and event.status_code == 401 for event in rejected.events())
    with (
        socket.create_connection((url.hostname, url.port), timeout=10) as connection,
        connection.makefile("rwb", buffering=0) as stream,
    ):
        wire = _Wire(stream, stream, client=True)
        stream.write(
            wire.codec.send(
                Request(
                    host="localhost",
                    target="/",
                    extra_headers=[(b"authorization", f"Bearer {os.environ[token_name]}".encode())],
                )
            )
        )
        stream.flush()
        accepted = False
        while not accepted:
            wire.codec.receive_data(stream.read(65536))
            accepted = any(isinstance(event, AcceptConnection) for event in wire.codec.events())
        sequence, notices = 0, []

        def call(method: str, params: dict[str, Any]) -> Any:
            nonlocal sequence
            sequence += 1
            wire.send({"id": sequence, "method": method, "params": params})
            while True:
                value = wire.receive()
                if value.get("id") == sequence and "method" not in value:
                    assert "error" not in value, value
                    return value.get("result")
                notices.append(value)

        def until(predicate: Any) -> dict[str, Any]:
            while True:
                for index, value in enumerate(notices):
                    if predicate(value):
                        return notices.pop(index)
                notices.append(wire.receive())

        call(
            "initialize", {"clientInfo": {"name": "codex-tui", "version": "0.166.0"}, "capabilities": {"experimentalApi": True}}
        )
        wire.send({"method": "initialized", "params": {}})
        if resumed:
            thread = arguments[arguments.index("resume") + 1]
            call("thread/resume", {"threadId": thread, "cwd": cwd})
        else:
            result = call(
                "thread/start",
                {
                    "threadSource": "user",
                    "ephemeral": False,
                    "cwd": cwd,
                    "model": "gpt-6.1-sol",
                    "modelProvider": "peer",
                    "sandbox": "read-only",
                    "config": {"model_reasoning_effort": effort},
                },
            )
            thread = result["thread"]["id"]
            turn = call(
                "turn/start",
                {
                    "threadId": thread,
                    "cwd": cwd,
                    "model": "gpt-6.1-sol",
                    "effort": effort,
                    "clientUserMessageId": str(uuid.uuid4()),
                    "input": [{"type": "text", "text": "original owned work"}],
                },
            )["turn"]["id"]
            until(lambda value: value.get("method") == "turn/started")
            lost_request = None
            if _MODE == "lost-response":
                sequence += 1
                lost_request = sequence
                wire.send(
                    {
                        "id": lost_request,
                        "method": "config/value/write",
                        "params": {"keyPath": "model_reasoning_effort", "value": "medium", "mergeStrategy": "replace"},
                    }
                )
                deadline = time.monotonic() + 5
                while not (_ROOT / "config-write.json").exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("再起動前に設定書込が適用されなかった")
                    time.sleep(0.01)
            old_approval = None
            if _MODE == "approval":
                old_approval = until(lambda value: value.get("method") == "item/commandExecution/requestApproval")["id"]
            if _MODE == "cancel":
                call("turn/interrupt", {"threadId": thread, "turnId": turn})
            if _MODE in {"completed", "cancel"}:
                until(lambda value: value.get("method") == "turn/completed")
            _write(_ROOT / "ready" / f"{os.getpid()}.json", {"thread": thread})
            deadline = time.monotonic() + 5
            expected = int(os.environ.get("CODEX_PEER_ACTORS", "1"))
            while len(tuple((_ROOT / "ready").glob("*.json"))) < expected:
                if time.monotonic() >= deadline:
                    raise TimeoutError("並行する会話が開始しなかった")
                time.sleep(0.01)
            with FileLock(str(_ROOT / "restart.lock"), timeout=5):
                if not (_ROOT / "restarted").exists():
                    with process_tree.process_scope(
                        [sys.executable, __file__, "app-server", "daemon", "restart"], stdout=-1, stderr=-1
                    ) as restarted:
                        _stdout, stderr = restarted.communicate()
                        assert restarted.returncode == 0, stderr
                    (_ROOT / "restarted").touch()
            if _MODE == "lost-response":
                error = until(lambda value: value.get("id") == lost_request and "error" in value)["error"]
                _write(_ROOT / "lost-request-error.json", error)
            if _MODE == "dead":
                return 1
            if _MODE in {"elicitation", "elicitation-cancel"}:
                request = until(lambda value: value.get("method") == "mcpServer/elicitation/request")
                assert not (_ROOT / "results" / f"{thread}.json").exists()
                if _MODE == "elicitation-cancel":
                    return 130
                wire.send({"id": request["id"], "result": {"action": "accept", "content": {"allow": True}}})
            if _MODE in {"completed", "cancel"}:
                deadline = time.monotonic() + 5
                while not (_ROOT / "children" / f"{thread}.json").exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("子の復旧が完了しなかった")
                    time.sleep(0.01)
                call("config/read", {"cwd": cwd})
                assert _read(_thread_file(thread))["starts"] == 1
                print(json.dumps({"id": thread, "status": _MODE, "effort": effort}), flush=True)
                return 130 if _MODE == "cancel" else 0
            if _MODE == "approval":
                approval = until(lambda value: value.get("method") == "item/commandExecution/requestApproval")["id"]
                assert approval != old_approval
                wire.send({"id": old_approval, "result": {"decision": "accept"}})
                call("config/read", {"cwd": cwd})
                assert not (_ROOT / "results" / f"{thread}.json").exists()
                wire.send({"id": approval, "result": {"decision": "accept"}})
        until(lambda value: value.get("method") == "turn/completed" and value["params"]["turn"]["status"] == "completed")
        if _MODE == "late-error":
            new_turn = call(
                "turn/start",
                {
                    "threadId": thread,
                    "cwd": cwd,
                    "model": "gpt-6.1-sol",
                    "effort": effort,
                    "clientUserMessageId": str(uuid.uuid4()),
                    "input": [{"type": "text", "text": "new owned work"}],
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                },
            )
            until(
                lambda value: value.get("method") == "turn/started" and value["params"]["turn"]["id"] == new_turn["turn"]["id"]
            )
            return 1
        output = _read(_ROOT / "results" / f"{thread}.json")
        assert output["id"] == thread and output["cwd"] == cwd and output["effort"] == effort
        print(json.dumps(output), flush=True)
        return 0


def main() -> int:
    """公開呼出を代用するpeerを起動する。実サービスと認証情報は使用しない。"""
    arguments = sys.argv[1:]
    if arguments[:2] == ["app-server", "proxy"]:
        _proxy()
        return 0
    if arguments[:2] == ["app-server", "daemon"]:
        _control(arguments[2])
        return 0
    if arguments[:1] == ["exec"]:
        print(json.dumps({"passthrough": arguments}), flush=True)
        return 7
    try:
        return _actor(arguments)
    except EOFError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
