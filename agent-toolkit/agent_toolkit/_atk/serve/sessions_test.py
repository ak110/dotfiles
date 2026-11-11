"""serveのsessionsに関するremoteの試験。"""

# pylint: disable=protected-access

import asyncio
import base64
import json
import pathlib
import sys
import types
import typing

import pytest

from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve import sessions
from agent_toolkit._testing import late_descendant
from agent_toolkit._testing.serve_sessions_support import (
    _claude_record,
    _connected_client,
    _context,
    _failed_ssh,
    _FakeRpcClient,
    _runner_returning,
)


@pytest.mark.asyncio
async def test_session_stream_restores_long_rpc_response_and_notifications(tmp_path: pathlib.Path) -> None:
    """長い応答の前後のreadyと更新通知も保持し、改行なしの最終応答を解決する。"""
    context = _context(tmp_path, remote_hosts=["remote-host"])
    client = sessions.RemoteSessionClient("remote-host", context.state)
    queue = await sessions.subscribe(context.state)
    future = asyncio.get_running_loop().create_future()
    client._pending[1] = future
    response = {"type": "response", "id": 1, "ok": True, "data": "あ" * 50000}
    stream = asyncio.StreamReader(limit=64)
    stream.feed_data(
        (
            json.dumps({"type": "ready"})
            + "\n"
            + json.dumps({"type": "refresh"})
            + "\n"
            + json.dumps(response, ensure_ascii=False)
        ).encode()
    )
    stream.feed_eof()

    await client._process_stream(stream)

    assert context.state.host_status["remote-host"] == "connected"
    assert json.loads(queue.get_nowait()) == {"type": "refresh"}
    assert future.result() == response


@pytest.mark.asyncio
async def test_unconfirmed_helper_stop_blocks_reconnect_until_same_process_stops(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """常駐ヘルパーの停止を確認できない間は参照を保持し、同じプロセスの停止を確認するまで再接続しない。"""
    context = _context(tmp_path, remote_hosts=["remote-host"])
    client = sessions.RemoteSessionClient("remote-host", context.state)
    client._backoff = 0
    helper = types.SimpleNamespace(stdout=asyncio.StreamReader(), stdin=None, returncode=None, pid=99)
    helper.stdout.feed_eof()
    events: list[tuple[str, object]] = []
    results = iter([False, False, True])

    async def connect() -> object:
        events.append(("connect", None))
        if len([event for event in events if event[0] == "connect"]) > 1:
            raise asyncio.CancelledError
        return helper

    async def stop(proc: object, _stderr_task: object) -> bool:
        events.append(("stop", proc))
        return next(results, True)

    monkeypatch.setattr(client, "_connect", connect)
    monkeypatch.setattr(_atk_serve_remote, "stop_resident_helper", stop)
    with pytest.raises(asyncio.CancelledError):
        await client.run()

    # 最後の停止は、取消された接続試行の後始末（起動前のためプロセスは無い）である。
    assert events == [
        ("connect", None),
        ("stop", helper),
        ("stop", helper),
        ("stop", helper),
        ("connect", None),
        ("stop", None),
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/home/aki/.claude/projects/p/a.jsonl", True),
        ("/home/aki/.claude/projects/../../etc/a.jsonl", False),
        ("/home/aki/.claude/projects/p/a.txt", False),
        ("", False),
        ("C:\\Users\\aki\\a.jsonl", True),
        ("C:\\Users\\aki\\..\\outside.jsonl", False),
        ("C:\\Users\\aki\\a.txt", False),
        ("relative\\a.jsonl", False),
    ],
)
def test_remote_record_path_is_validated_before_ssh(raw: str, expected: bool) -> None:
    """上位ディレクトリ参照と対象外の接尾辞は、SSH呼び出しの前に拒否する。"""
    assert sessions.is_safe_remote_record_path(raw) is expected


@pytest.mark.asyncio
async def test_remote_entries_are_merged_into_the_listing(tmp_path: pathlib.Path) -> None:
    """設定済みリモートホストの記録を一覧へ含める。"""
    _claude_record(tmp_path)
    runner, calls = _runner_returning(
        {
            "ok": True,
            "entries": [
                {
                    "engine": "codex",
                    "cwd": "/srv/work",
                    "first_user_message": "リモートの最初の発話",
                    "session_id": "remote-session",
                    "path": "/home/aki/.codex/sessions/2026/09/01/rollout-x.jsonl",
                    "started_at": "2026-09-02T00:00:00Z",
                    "updated_at": 1_800_000_000,
                    "size": 12,
                }
            ],
        }
    )
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)

    entries, warnings = await sessions.list_sessions(context)

    assert warnings == []
    assert [entry.session_id for entry in entries] == [
        "remote-session",
        "11111111-2222-3333-4444-555555555555",
    ]
    assert {(entry.host, entry.session_id) for entry in entries} == {
        ("local-host", "11111111-2222-3333-4444-555555555555"),
        ("remote-host", "remote-session"),
    }
    remote = next(entry for entry in entries if entry.host == "remote-host")
    assert remote.cwd == "/srv/work"
    assert remote.first_user_message == "リモートの最初の発話"
    assert remote.started_at == "2026-09-02T00:00:00Z"
    assert calls == [("remote-host", "list", [])]


@pytest.mark.asyncio
async def test_unreachable_host_is_reported_and_others_are_returned(tmp_path: pathlib.Path) -> None:
    """1台へ到達できなくても、他のホストとローカルの一覧は返す。"""
    _claude_record(tmp_path)

    async def runner(host: str, op: str, args: list[str]) -> str:
        del op, args
        if host == "down-host":
            raise OSError("接続できません")
        return json.dumps({"ok": True, "entries": []})

    context = _context(tmp_path, remote_hosts=["down-host", "up-host"], ssh_runner=runner)

    entries, warnings = await sessions.list_sessions(context)

    assert [entry.host for entry in entries] == ["local-host"]
    assert [warning["host"] for warning in warnings] == ["down-host"]
    assert warnings[0]["reason"].startswith("記録を取得できません: ")
    assert "接続できません" in warnings[0]["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (b"helper not found\n", "helper not found"),
        (b"  \n ", "標準エラー出力はありません"),
        (b"\xff\xfe helper failed", "helper failed"),
    ],
    ids=["message", "empty", "undecodable"],
)
async def test_remote_failure_warning_carries_stderr(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    stderr: bytes,
    expected: str,
) -> None:
    """リモート実行が非0で終了した場合、終了コードと失敗元の標準エラー出力を警告本文へ引き継ぐ。"""
    monkeypatch.setattr(_atk_serve_remote, "run_ssh", _failed_ssh(2, stderr))
    context = _context(tmp_path, remote_hosts=["down-host"])

    _, warnings = await sessions.list_sessions(context)

    reason = warnings[0]["reason"]
    assert reason.startswith("記録を取得できません: ")
    assert "終了コード2" in reason
    assert expected in reason


@pytest.mark.asyncio
async def test_long_stderr_keeps_the_tail_in_the_warning(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """標準エラー出力が上限を超える場合、失敗の直接原因が現れる末尾側を残して切り詰める。"""
    head = "先頭の行" * sessions.STDERR_EXCERPT_MAX_CHARS
    stderr = f"{head}\n末尾の理由\n".encode()
    monkeypatch.setattr(_atk_serve_remote, "run_ssh", _failed_ssh(2, stderr))
    context = _context(tmp_path, remote_hosts=["down-host"])

    _, warnings = await sessions.list_sessions(context)

    reason = warnings[0]["reason"]
    assert "末尾の理由" in reason
    assert head not in reason
    assert len(reason) < len(head)


@pytest.mark.asyncio
async def test_host_status_reports_connection_state(tmp_path: pathlib.Path) -> None:
    """ホストごとの接続状態を返す。ローカルは常に接続済みとする。"""
    context = _context(tmp_path, remote_hosts=["remote-host"])

    assert await sessions.host_status(context) == {"local-host": "connected", "remote-host": "connecting"}

    async with context.state.lock:
        context.state.host_status["remote-host"] = "disconnected"
    assert (await sessions.host_status(context))["remote-host"] == "disconnected"


@pytest.mark.asyncio
async def test_remote_call_falls_back_to_single_ssh(tmp_path: pathlib.Path) -> None:
    """常駐RPCが未接続・失敗・エラー応答の場合は単発SSHへ切り替える。"""
    runner, calls = _runner_returning({"ok": True, "entries": []})
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)

    # 未接続。
    disconnected = _FakeRpcClient(connected=False, response={"ok": True, "entries": []})
    context.state.clients["remote-host"] = typing.cast(typing.Any, disconnected)
    assert await sessions._remote_call(context, "remote-host", "list", {}) == {"ok": True, "entries": []}
    # RPCが例外で失敗。
    context.state.clients["remote-host"] = typing.cast(
        typing.Any, _FakeRpcClient(connected=True, response=RuntimeError("切断"))
    )
    assert await sessions._remote_call(context, "remote-host", "list", {}) == {"ok": True, "entries": []}
    # RPCがエラー応答を返した。
    failing = _FakeRpcClient(connected=True, response={"ok": False, "error": "no such file"})
    context.state.clients["remote-host"] = typing.cast(typing.Any, failing)
    assert await sessions._remote_call(context, "remote-host", "list", {}) == {"ok": True, "entries": []}

    assert calls == [("remote-host", "list", []), ("remote-host", "list", []), ("remote-host", "list", [])]
    assert failing.calls == [("list", {})]


@pytest.mark.asyncio
async def test_remote_list_accepts_response_slower_than_read_timeout(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`list`の上限は`read`と別に定め、`read`の上限を超えて遅れる一覧の応答も受け取って単発SSHを起動しない。

    一覧の初回走査は記録量に比例して長くなるため、1件の読み取りと同じ上限で打ち切ると、
    記録の多いホストの一覧が常に取得できなくなる。
    """
    monkeypatch.setitem(sessions.OPERATION_TIMEOUT_SEC, "read", 0.05)
    monkeypatch.setitem(sessions.OPERATION_TIMEOUT_SEC, "list", 5.0)
    runner, calls = _runner_returning({"ok": True, "entries": []})
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)
    client, stdin = _connected_client(context, "remote-host")

    async def respond_late() -> None:
        while not stdin.requests:
            await asyncio.sleep(0.01)
        stream = asyncio.StreamReader()
        response = {
            "type": "response",
            "id": stdin.requests[0]["id"],
            "ok": True,
            "entries": [{"engine": "claude", "path": "/r/a.jsonl"}],
        }
        stream.feed_data(json.dumps(response).encode("utf-8") + b"\n")
        stream.feed_eof()
        loop = asyncio.get_running_loop()
        after_read_deadline = loop.time() + 0.2
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(loop, "time", lambda: after_read_deadline)
            advanced = asyncio.Event()
            loop.call_later(0, advanced.set)
            await advanced.wait()
            await client._process_stream(stream)

    responder = asyncio.ensure_future(respond_late())
    entries, warnings = await sessions.list_sessions(context)
    await responder

    assert [(entry.host, entry.path) for entry in entries] == [("remote-host", "/r/a.jsonl")]
    assert not warnings
    assert not calls
    assert [request["op"] for request in stdin.requests] == ["list"]


@pytest.mark.asyncio
async def test_remote_list_rpc_timeout_does_not_fall_back(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`list`のRPCが上限を超えた場合は単発SSHを起動せず、上限超過と秒数を理由に持つ警告を返す。

    単発SSHは同じ走査を索引の無い新しいプロセスで最初からやり直すため、走査を続ける常駐ヘルパーより先に終わらない。
    """
    monkeypatch.setitem(sessions.OPERATION_TIMEOUT_SEC, "list", 0.05)
    runner, calls = _runner_returning({"ok": True, "entries": []})
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)
    _connected_client(context, "remote-host")

    entries, warnings = await sessions.list_sessions(context)

    assert not [entry for entry in entries if entry.host == "remote-host"]
    assert not calls
    assert [warning["host"] for warning in warnings] == ["remote-host"]
    assert "op=list" in warnings[0]["reason"]
    assert "上限の0.05秒" in warnings[0]["reason"]


@pytest.mark.asyncio
async def test_remote_call_uses_rpc_when_connected(tmp_path: pathlib.Path) -> None:
    """常駐RPCが応答する場合は単発SSHを起動しない。"""
    runner, calls = _runner_returning({"ok": True, "entries": []})
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)
    client = _FakeRpcClient(connected=True, response={"ok": True, "entries": [{"path": "/x.jsonl"}]})
    context.state.clients["remote-host"] = typing.cast(typing.Any, client)

    payload = await sessions._remote_call(context, "remote-host", "list", {})

    assert payload["entries"] == [{"path": "/x.jsonl"}]
    assert not calls
    assert client.calls == [("list", {})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record_path", ["/home/aki/.claude/projects/p/abc.jsonl", "C:\\Users\\aki\\.claude\\projects\\p\\abc.jsonl"]
)
async def test_remote_detail_is_normalized_like_local(tmp_path: pathlib.Path, record_path: str) -> None:
    """リモートの記録も同じ表示モデルへ正規化する。"""
    text = json.dumps({"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": "やあ"}}) + "\n"
    runner, calls = _runner_returning({"ok": True, "data": base64.b64encode(text.encode("utf-8")).decode("ascii")})
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)

    detail = await sessions.session_detail(context, "claude", "remote-host", record_path)

    assert detail["host"] == "remote-host"
    assert detail["session_id"] == "abc"
    assert detail["path"] == record_path
    assert [event["text"] for event in detail["events"]] == ["やあ"]
    assert calls[0][1] == "read"


@pytest.mark.asyncio
async def test_remote_subagents_are_listed_or_reported_as_unavailable(tmp_path: pathlib.Path) -> None:
    """リモートの記録もサブエージェント一覧を返し、一覧欄がない応答は判定不能として区別する。"""
    text = json.dumps({"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": "やあ"}}) + "\n"
    data = base64.b64encode(text.encode("utf-8")).decode("ascii")
    subagent = {
        "agent_id": "agent-1",
        "agent_type": "Explore",
        "description": "探索",
        "spawn_depth": 1,
        "parent_agent_id": None,
        "model": "opus",
        "path": "/home/aki/.claude/projects/p/abc/subagents/agent-1.jsonl",
    }

    async def detail_for(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
        runner, _ = _runner_returning(payload)
        context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)
        return await sessions.session_detail(context, "claude", "remote-host", "/home/aki/.claude/projects/p/abc.jsonl")

    listed = await detail_for({"ok": True, "data": data, "subagents": [subagent]})
    assert listed["subagents"] == [subagent]
    assert listed["subagents_unavailable"] is False

    # サブエージェント一覧を返さない版のヘルパーが動くホストでは、読み取り自体は成功するため欄の欠落で判別する。
    legacy = await detail_for({"ok": True, "data": data})
    assert legacy["subagents"] is None
    assert legacy["subagents_unavailable"] is True

    # 空配列を返すホストはサブエージェントが無いことを示すため、判定不能としない。
    empty = await detail_for({"ok": True, "data": data, "subagents": []})
    assert empty["subagents"] is None
    assert empty["subagents_unavailable"] is False


@pytest.mark.asyncio
async def test_unknown_engine_or_host_is_not_found(tmp_path: pathlib.Path) -> None:
    """未知の実行系・ホスト・危険なパスは詳細を返さない。"""
    runner, _ = _runner_returning({"ok": True, "data": ""})
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)

    for engine, host, path in (
        ("gemini", "remote-host", "/a.jsonl"),
        ("claude", "unknown", "/a.jsonl"),
        ("claude", "remote-host", "/home/aki/../etc/a.jsonl"),
    ):
        with pytest.raises(sessions.SessionNotFoundError):
            await sessions.session_detail(context, engine, host, path)


def test_local_hostname_must_not_collide_with_remote_hosts(tmp_path: pathlib.Path) -> None:
    """ローカルホスト名とリモートホスト名の重複は起動時に拒絶する。"""
    with pytest.raises(ValueError):
        _context(tmp_path, remote_hosts=["local-host"])


@pytest.mark.asyncio
async def test_remote_change_notifications_are_relayed_to_subscribers(tmp_path: pathlib.Path) -> None:
    """リモート補助の変更通知を、ホスト名を付けてSSE購読者へ中継する。"""
    context = _context(tmp_path, remote_hosts=["remote-host"])
    client = sessions.RemoteSessionClient("remote-host", context.state)
    queue = await sessions.subscribe(context.state)
    stream = asyncio.StreamReader()
    stream.feed_data(
        b'{"type":"ready","host":"remote-host"}\n{"type":"record","engine":"claude","path":"/r/a.jsonl"}\n{"type":"unknown"}\n'
    )
    stream.feed_eof()

    await client._process_stream(stream)

    assert json.loads(queue.get_nowait()) == {
        "type": "record",
        "host": "remote-host",
        "engine": "claude",
        "path": "/r/a.jsonl",
    }
    assert queue.empty()
    stream = asyncio.StreamReader()
    stream.feed_data(b'{"type":"refresh"}\n')
    stream.feed_eof()
    await client._process_stream(stream)
    assert json.loads(queue.get_nowait()) == {"type": "refresh"}


@pytest.mark.asyncio
async def test_run_does_not_reconnect_when_cancelled_during_cleanup(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """常駐SSHが停止処理と同時に終了しても、停止処理のキャンセルで再接続せずに終わる。

    プロセスグループ全体へSIGTERMが届くと、子のSSHが先に終了して後始末へ入ったタスクへ停止処理のキャンセルが届く。
    後始末の待機がキャンセルを吸収したまま再接続すると、新しいSSHの出力を待ち続けてatk serveの停止が完了しない。
    """
    started: list[tuple[typing.Any, ...]] = []
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*cmd: typing.Any, **kwargs: typing.Any) -> asyncio.subprocess.Process:
        # 標準出力を閉じて接続断を起こし、標準入力の終端を通知してから解放まで保持する子プロセスで代替する。
        started.append(cmd)
        script = "import os,sys; os.close(1); sys.stdin.read(); " + late_descendant.held_source(tmp_path / "cleanup.release")
        return await real_exec(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    context = _context(tmp_path, remote_hosts=["remote-host"])
    sessions.start_remote_clients(context)
    for _ in range(500):
        if context.state.host_status.get("remote-host") == "disconnected":
            break
        await asyncio.sleep(0.01)
    assert context.state.host_status.get("remote-host") == "disconnected"

    async with asyncio.timeout(5):
        while not (tmp_path / "cleanup.ready").exists():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(sessions.stop_remote_clients(context), timeout=5)

    assert len(started) == 1
