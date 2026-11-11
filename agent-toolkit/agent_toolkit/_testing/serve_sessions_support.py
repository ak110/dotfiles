"""serveのsessions試験が共有する入力と補助処理。"""

# pylint: disable=protected-access

import asyncio
import dataclasses
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time
import types
import typing

import pytest

from agent_toolkit._atk.serve import sessions
from agent_toolkit._common import process_tree


def _write(path: pathlib.Path, records: typing.Iterable[typing.Any]) -> pathlib.Path:
    """JSON Linesの記録を出力する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    return path


def _context(tmp_path: pathlib.Path, **kwargs: typing.Any) -> sessions.SessionsContext:
    """外部へ接続しないセッション画面のコンテキストを生成する。"""
    return sessions.create_context(
        hostname="local-host",
        claude_home=tmp_path / "claude",
        codex_home=tmp_path / "codex",
        **kwargs,
    )


def _claude_record(tmp_path: pathlib.Path, project: str = "-home-aki-proj") -> pathlib.Path:
    """Claude Codeのセッション記録1件を作成する。"""
    return _write(
        tmp_path / "claude" / "projects" / project / "11111111-2222-3333-4444-555555555555.jsonl",
        [
            {"type": "user", "timestamp": "2026-09-01T00:00:00Z", "cwd": "/home/aki/proj", "message": {"content": "やあ"}},
            {
                "type": "assistant",
                "timestamp": "2026-09-01T00:00:01Z",
                "message": {
                    "usage": {"input_tokens": 10, "output_tokens": 3},
                    "content": [
                        {"type": "thinking", "thinking": "考える"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                        {"type": "text", "text": "できました"},
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-01T00:00:02Z",
                "message": {"content": [{"type": "tool_result", "content": "a.txt", "is_error": False}]},
            },
            {
                "type": "system",
                "subtype": "compact_boundary",
                "timestamp": "2026-09-01T00:00:03Z",
                "compactMetadata": {"trigger": "auto"},
            },
        ],
    )


def _codex_record(tmp_path: pathlib.Path) -> pathlib.Path:
    """Codexのロールアウト記録1件を作成する。"""
    return _write(
        tmp_path
        / "codex"
        / "sessions"
        / "2026"
        / "09"
        / "01"
        / "rollout-2026-09-01T00-00-00-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-09-01T00:00:00Z",
                "payload": {"cwd": "/home/aki/other", "timestamp": "2026-09-01T00:00:00Z"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-01T00:00:01Z",
                "payload": {"type": "message", "role": "user", "content": [{"text": "やあ"}]},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-01T00:00:02Z",
                "payload": {"type": "reasoning", "summary": [{"text": "考える"}]},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-01T00:00:03Z",
                "payload": {"type": "function_call", "name": "shell", "arguments": '{"cmd":"ls"}'},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-09-01T00:00:04Z",
                "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 7, "output_tokens": 2}}},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-09-01T00:00:05Z",
                "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 21, "output_tokens": 5}}},
            },
        ],
    )


class _FakeRpcClient:
    """常駐RPCの接続状態と応答を差し替えるスタブ。"""

    def __init__(self, *, connected: bool, response: typing.Any) -> None:
        self._connected = connected
        self._response = response
        self.calls: list[tuple[str, dict[str, typing.Any]]] = []

    def is_connected(self) -> bool:
        """接続状態を返す。"""
        return self._connected

    async def request(self, op: str, args: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """RPC応答を返すか、設定された例外を送出する。"""
        self.calls.append((op, args))
        if isinstance(self._response, Exception):
            raise self._response
        assert isinstance(self._response, dict)
        return self._response


def _runner_returning(payload: typing.Any) -> tuple[typing.Any, list[tuple[str, str, list[str]]]]:
    """単発SSHの呼び出しを記録するrunnerと、その記録先を返す。"""
    calls: list[tuple[str, str, list[str]]] = []

    async def runner(host: str, op: str, args: list[str]) -> str:
        calls.append((host, op, args))
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload)

    return runner, calls


def _load_remote_helper() -> typing.Any:
    """リモート補助を同じプロセスへ読み込む。件数上限を差し替えて`_list_payload`を呼ぶテストが使う。"""
    helper_path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "atk_serve_sessions_remote_helper.py"
    spec = importlib.util.spec_from_file_location("_atk_serve_sessions_remote_helper_tree", helper_path)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def _helper_runner(environment: dict[str, str], *, only_op: str | None = None) -> sessions.SshRunner:
    """実際のリモートヘルパーを`environment`の環境で子プロセスとして起動する単発SSHの代替を返す。

    `only_op`を渡すと、それ以外の操作の呼び出しをテストの失敗にする。
    """
    helper = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "atk_serve_sessions_remote_helper.py"

    async def runner(_host: str, op: str, args: list[str]) -> str:
        assert only_op is None or op == only_op
        completed = await asyncio.to_thread(
            process_tree.run_with_timeout,
            [sys.executable, str(helper), op, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
            env=environment,
        )
        return completed.stdout

    return runner


def _failed_ssh(returncode: int, stderr: bytes) -> typing.Callable[..., typing.Awaitable[tuple[int, bytes, bytes]]]:
    """指定した終了コードと標準エラー出力を返す単発SSH（`remote.run_ssh`）の代用を組み立てる。"""

    async def run(cmd: list[str], timeout: float) -> tuple[int, bytes, bytes]:
        del cmd, timeout
        return returncode, b"", stderr

    return run


class _RecordingStdin:
    """常駐接続の標準入力を差し替え、送られた要求を記録する。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, typing.Any]] = []

    def write(self, data: bytes) -> None:
        """要求の行を記録する。"""
        self.requests.append(json.loads(data))

    async def drain(self) -> None:
        """書き込みの完了を待つ。記録だけなので待たない。"""

    def is_closing(self) -> bool:
        """接続中として扱う。"""
        return False


def _connected_client(context: sessions.SessionsContext, host: str) -> tuple[sessions.RemoteSessionClient, _RecordingStdin]:
    """要求を記録する標準入力を持つ、接続済みの常駐接続を登録する。"""
    client = sessions.RemoteSessionClient(host, context.state)
    stdin = _RecordingStdin()
    client._proc = typing.cast(typing.Any, types.SimpleNamespace(stdin=stdin))
    client._connected = True
    context.state.clients[host] = client
    return client, stdin


def _no_user_corpus(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """発話を持つ記録と持たない記録（Claude Code本体・サブエージェント・Codex）を作成する。"""
    projects = root / ".claude" / "projects" / "repo"
    spoken = _write(projects / "spoken.jsonl", [{"type": "user", "message": {"content": "やあ"}}])
    silent = _write(projects / "silent.jsonl", [{"type": "permission-mode"}, {"type": "system", "subtype": "x"}])
    subagents = spoken.with_suffix("") / "subagents"
    _write(subagents / "agent-a.jsonl", [{"type": "assistant", "message": {"content": "作業"}}])
    (subagents / "agent-a.meta.json").write_text(json.dumps({"spawnDepth": 1}), encoding="utf-8")
    codex_day = root / "codex-home" / "sessions" / "2026" / "09" / "26"
    codex_silent = _write(codex_day / "rollout-silent.jsonl", [{"type": "session_meta", "payload": {"cwd": "/w"}}])
    codex_spoken = _write(
        codex_day / "rollout-spoken.jsonl",
        [{"type": "session_meta", "payload": {"cwd": "/w"}}, {"type": "response_item", "payload": {"role": "user"}}],
    )
    return {
        "spoken": spoken,
        "silent": silent,
        "subagent": subagents / "agent-a.jsonl",
        "codex_silent": codex_silent,
        "codex_spoken": codex_spoken,
    }


def _helper_list(root: pathlib.Path) -> list[dict[str, typing.Any]]:
    """リモート補助の一覧を、`root`をホームディレクトリとして取得する。"""
    environment = os.environ.copy()
    environment.update({"HOME": str(root), "CODEX_HOME": str(root / "codex-home")})
    result = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "atk_serve_sessions_remote_helper.py"),
            "list",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    return json.loads(result.stdout)["entries"]


def _count_record_opens(monkeypatch: pytest.MonkeyPatch) -> list[pathlib.Path]:
    """記録（`.jsonl`）を開いた回数を数えるため、開いたパスを順に記録する。"""
    opened: list[pathlib.Path] = []
    original_open = pathlib.Path.open

    def counting_open(self: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        if self.suffix == ".jsonl":
            opened.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", counting_open)
    return opened


def _age(*paths: pathlib.Path) -> None:
    """更新時刻を十分過去へ戻し、索引が解析結果を信用できる記録にする。"""
    for path in paths:
        os.utime(path, (1_700_000_000, 1_700_000_000))


def _retained_characters(value: typing.Any) -> int:
    """索引と全件結果の文字列長を、オブジェクトの共有にかかわらず項目ごとに合計する。"""
    if isinstance(value, str):
        return len(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return sum(_retained_characters(getattr(value, field.name)) for field in dataclasses.fields(value))
    if isinstance(value, dict):
        return sum(_retained_characters(key) + _retained_characters(item) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return sum(_retained_characters(item) for item in value)
    return 0


def _wait_until(condition: typing.Callable[[], bool], timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "条件が上限時間内に成立しなかった"
        time.sleep(0.05)


def _start_warmer(context: sessions.SessionsContext) -> sessions._session_costs.FamilyCostWarmer:
    warmer = sessions._session_costs.FamilyCostWarmer(
        context.state.family_costs,
        claude_home=context.claude_home,
        codex_home=context.codex_home,
        state_dir=context.state_dir,
        slim_directory=context.state.slim_records.directory,
    )
    context.state.cost_warmer = warmer
    return warmer
