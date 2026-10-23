"""Codex App Serverの資源の持ち方を模すstdioのJSON-RPCサーバー。

試験から`python <本ファイル> <イベント記録のパス>`で起動する。agent_toolkitを読み込まず、標準ライブラリと`psutil`だけを使う。

実Codex（CLI 0.163.0）で観測した次の挙動を再現する。

- `thread/start`と未ロードのthreadの`thread/resume`は、thread用のstdio MCPサーバー役の子プロセスを`cwd`で起動する
- `thread/unsubscribe`は購読解除の応答と`thread/closed`の通知を返すが、MCPサーバー役の子プロセスを終了させない
- MCPサーバー役の子プロセスは標準入力を読まず、親のApp Serverが終了しても自ら終了しない

`turn/start`は応答の直後に`turn/started`を、短い間隔の後に`turn/completed`を通知し、モデルを呼ばずにturnを終える。
`model`が`fake-unavailable`のturnは、間隔を置かずに接続先の可用性を理由とする失敗として終わる。
起動したプロセスのPIDと起動時刻、受けた要求をイベント記録へJSON Linesで追記する。
試験はこの記録のPIDと起動時刻の組から、自身が起動させたプロセスだけを特定して生存を確かめる。
"""

import json
import os
import pathlib
import subprocess
import sys
import threading

import psutil

UNAVAILABLE_MODEL = "fake-unavailable"
"""このモデルを指定したturnを、接続先の可用性を理由とする失敗として終える。"""

_MCP_SCRIPT = "import time\nwhile True:\n    time.sleep(60)\n"

TURN_DURATION_SECONDS = 0.3
"""`turn/started`から`turn/completed`までの間隔。委譲元が開始の応答を受けてから終端を観測する順序を再現する。"""

_WRITE_LOCK = threading.Lock()


class _Server:
    def __init__(self, log_path: pathlib.Path) -> None:
        self.log_path = log_path
        self.loaded: dict[str, int] = {}
        self.models: dict[str, str | None] = {}
        self.thread_count = 0
        self.turn_count = 0

    def record(self, event: dict[str, object]) -> None:
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({**event, "server": os.getpid()}, ensure_ascii=False) + "\n")

    @staticmethod
    def send(message: dict[str, object]) -> None:
        with _WRITE_LOCK:
            sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def load(self, thread_id: str, cwd: str) -> None:
        if thread_id in self.loaded:
            return
        # MCPサーバー役はApp Serverより長く生存し得る実MCPを模すため、withで寿命を閉じない。
        child = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with  # 試験用の固定した引数だけで起動する
            [sys.executable, "-c", _MCP_SCRIPT],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.loaded[thread_id] = child.pid
        self.record({"event": "mcp", "thread": thread_id, "pid": child.pid, "created": _created(child.pid), "cwd": cwd})

    def handle(self, request_id: object, method: str, params: dict[str, object]) -> list[dict[str, object]]:
        """応答を送り、続けて送る通知を返す。"""
        if method == "initialize":
            self.send({"id": request_id, "result": {"userAgent": "fake-codex-app-server"}})
            return []
        if method == "thread/start":
            self.thread_count += 1
            thread_id = f"thread-{os.getpid()}-{self.thread_count}"
            self.models[thread_id] = _text(params.get("model"))
            self.load(thread_id, str(params["cwd"]))
            self.record({"event": "thread/start", "thread": thread_id})
            self.send({"id": request_id, "result": {"thread": {"id": thread_id}, "modelProvider": "openai"}})
            return [{"method": "thread/started", "params": {"thread": {"id": thread_id}}}]
        if method == "thread/resume":
            thread_id = str(params["threadId"])
            self.models.setdefault(thread_id, _text(params.get("model")))
            self.load(thread_id, str(params["cwd"]))
            self.record({"event": "thread/resume", "thread": thread_id})
            self.send(
                {
                    "id": request_id,
                    "result": {"thread": {"id": thread_id}, "modelProvider": params.get("modelProvider", "openai")},
                }
            )
            return []
        if method == "thread/unsubscribe":
            thread_id = str(params["threadId"])
            self.record({"event": "thread/unsubscribe", "thread": thread_id})
            self.send({"id": request_id, "result": {"status": "unsubscribed"}})
            return [{"method": "thread/closed", "params": {"threadId": thread_id}}]
        if method == "turn/start":
            return self.start_turn(request_id, params)
        if method == "thread/read":
            self.send({"id": request_id, "result": {"thread": {"id": params["threadId"], "turns": []}}})
            return []
        if method == "config/read":
            self.send({"id": request_id, "result": {"config": {"mcp_servers": {}}}})
            return []
        self.send({"id": request_id, "error": {"code": -32601, "message": f"unsupported method: {method}"}})
        return []

    def start_turn(self, request_id: object, params: dict[str, object]) -> list[dict[str, object]]:
        thread_id = str(params["threadId"])
        self.turn_count += 1
        turn_id = f"turn-{os.getpid()}-{self.turn_count}"
        text = ""
        items = params.get("input")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            text = str(items[0].get("text", ""))
        self.record({"event": "turn/start", "thread": thread_id, "turn": turn_id, "text": text})
        self.send({"id": request_id, "result": {"turn": {"id": turn_id}}})
        started: dict[str, object] = {"method": "turn/started", "params": {"threadId": thread_id, "turn": {"id": turn_id}}}
        model = _text(params.get("model")) or self.models.get(thread_id)
        if model == UNAVAILABLE_MODEL:
            # 可用性の失敗はモデル出力より前に起き、起動直後の可用性確認の間に終端を観測できる。
            failed = {
                "id": turn_id,
                "status": "failed",
                "error": {"codexErrorInfo": "rateLimitExceeded", "message": "fake rate limit"},
                "items": [],
            }
            return [started, {"method": "turn/completed", "params": {"threadId": thread_id, "turn": failed}}]
        turn = {
            "id": turn_id,
            "status": "completed",
            "error": None,
            "items": [{"type": "agentMessage", "id": f"message-{turn_id}", "text": f"完了: {text}"}],
        }
        completion = threading.Timer(
            TURN_DURATION_SECONDS,
            self.send,
            args=({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}},),
        )
        completion.daemon = True
        completion.start()
        return [started]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _created(pid: int) -> float:
    return psutil.Process(pid).create_time()


def main() -> None:
    server = _Server(pathlib.Path(sys.argv[1]))
    server.record({"event": "server", "pid": os.getpid(), "created": _created(os.getpid())})
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        method = message.get("method")
        if "id" not in message or not isinstance(method, str):
            continue
        params = message.get("params")
        for notification in server.handle(message["id"], method, params if isinstance(params, dict) else {}):
            server.send(notification)


if __name__ == "__main__":
    main()
