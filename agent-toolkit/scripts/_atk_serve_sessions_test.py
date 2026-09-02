"""`atk serve`のセッション画面の処理のテスト。"""

# pylint: disable=protected-access

import asyncio
import base64
import json
import pathlib
import subprocess
import typing

import _atk_serve_sessions as sessions
import pytest


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


def test_listing_identifies_engine_host_project_and_time(tmp_path: pathlib.Path) -> None:
    """一覧は実行系・ホスト・プロジェクト・日時で記録を識別できる。"""
    claude_path = _claude_record(tmp_path)
    codex_path = _codex_record(tmp_path)
    # サブエージェント記録（深さ4）はセッション本体ではないため一覧へ含めない。
    _write(claude_path.with_suffix("") / "subagents" / "agent-1.jsonl", [{"type": "user", "message": {"content": "x"}}])

    entries = sessions.list_local_sessions(_context(tmp_path))

    by_engine = {entry.engine: entry for entry in entries}
    assert set(by_engine) == {"claude", "codex"}
    assert [entry.host for entry in entries] == ["local-host", "local-host"]
    assert by_engine["claude"].project == "-home-aki-proj"
    assert by_engine["claude"].session_id == "11111111-2222-3333-4444-555555555555"
    assert by_engine["claude"].path == str(claude_path)
    assert by_engine["codex"].session_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert by_engine["codex"].path == str(codex_path)
    for entry in entries:
        assert entry.updated_at is not None
        assert entry.size is not None


def test_detail_renders_claude_records_in_order(tmp_path: pathlib.Path) -> None:
    """Claude Codeの詳細は思考・ツール呼び出しと結果・圧縮境界・使用量を時系列に返す。"""
    path = _claude_record(tmp_path)
    meta = path.with_suffix("") / "subagents" / "agent-1.meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(
        json.dumps({"agentType": "Explore", "description": "探索", "spawnDepth": 1, "parentAgentId": None, "model": "opus"}),
        encoding="utf-8",
    )

    detail = sessions.read_local_detail(_context(tmp_path), "claude", str(path))

    assert [event["kind"] for event in detail["events"]] == [
        "user",
        "thinking",
        "tool_call",
        "assistant",
        "tool_result",
        "compact_boundary",
    ]
    assert detail["events"][2]["name"] == "Bash"
    assert detail["events"][5]["detail"] == {"trigger": "auto"}
    assert detail["usage"] == {"input_tokens": 10, "output_tokens": 3}
    # 記録本体が残っていないサブエージェントは、開く対象が無いことを`path`のnullで表す。
    assert detail["subagents"] == [
        {
            "agent_id": "agent-1",
            "agent_type": "Explore",
            "description": "探索",
            "spawn_depth": 1,
            "parent_agent_id": None,
            "model": "opus",
            "path": None,
        }
    ]
    assert detail["project"] == "/home/aki/proj"
    assert detail["started_at"] == "2026-09-01T00:00:00Z"


def test_subagent_records_are_reachable_from_the_parent_detail(tmp_path: pathlib.Path) -> None:
    """記録本体があるサブエージェントは絶対パスを返し、その詳細から下位の階層も辿れる。"""
    path = _claude_record(tmp_path)
    subagents = path.with_suffix("") / "subagents"
    for agent_id, depth in (("agent-parent", 1), ("agent-child", 2)):
        meta = subagents / f"{agent_id}.meta.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"agentType": "Explore", "spawnDepth": depth}), encoding="utf-8")
    parent_record = _write(subagents / "agent-parent.jsonl", [{"type": "user", "message": {"content": "親の発話"}}])
    nested = parent_record.with_suffix("") / "subagents" / "agent-nested.meta.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text(json.dumps({"agentType": "Plan", "spawnDepth": 2}), encoding="utf-8")
    context = _context(tmp_path)

    detail = sessions.read_local_detail(context, "claude", str(path))

    by_id = {item["agent_id"]: item for item in detail["subagents"]}
    assert by_id["agent-parent"]["path"] == str(parent_record)
    # 記録本体が無い項目は開けないため、深さだけを返す。
    assert by_id["agent-child"]["path"] is None
    assert by_id["agent-child"]["spawn_depth"] == 2

    nested_detail = sessions.read_local_detail(context, "claude", by_id["agent-parent"]["path"])

    assert [event["text"] for event in nested_detail["events"]] == ["親の発話"]
    assert [item["agent_id"] for item in nested_detail["subagents"]] == ["agent-nested"]


def test_detail_renders_codex_records_in_order(tmp_path: pathlib.Path) -> None:
    """Codexの詳細は発話・思考・ツール呼び出しを時系列に返し、使用量へ累計値を反映する。"""
    path = _codex_record(tmp_path)

    detail = sessions.read_local_detail(_context(tmp_path), "codex", str(path))

    assert [event["kind"] for event in detail["events"]] == ["user", "thinking", "tool_call"]
    assert detail["events"][2]["name"] == "shell"
    # Codexは累計値を通知するため、加算せず最後の観測値で置き換える。
    assert detail["usage"] == {"input_tokens": 21, "output_tokens": 5}
    assert detail["project"] == "/home/aki/other"


def test_absent_fields_are_reported_as_unavailable(tmp_path: pathlib.Path) -> None:
    """記録が持たない情報は0や空文字列で補わず、取得不能として返す。"""
    path = _write(
        tmp_path / "claude" / "projects" / "proj" / "abc.jsonl",
        [{"type": "assistant", "message": {"content": [{"type": "text", "text": "やあ"}]}}],
    )

    detail = sessions.read_local_detail(_context(tmp_path), "claude", str(path))

    assert detail["started_at"] is None
    assert detail["usage"] == {"input_tokens": None, "output_tokens": None}
    # サブエージェント記録が無い場合は空配列ではなくnullとし、「0件」と区別する。
    assert detail["subagents"] is None
    # ローカルの記録は保存先を直接読むため、有無の判定自体は常に成立する。
    assert detail["subagents_unavailable"] is False
    assert detail["events"][0]["timestamp"] is None
    assert detail["events"][0]["name"] is None
    assert detail["events"][0]["usage"] is None


def test_broken_record_is_reported_per_entry(tmp_path: pathlib.Path) -> None:
    """書き込み途中の行があっても他の行を失わせず、該当件数を項目単位で返す。"""
    path = tmp_path / "claude" / "projects" / "proj" / "abc.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"type":"user","message":{"content":"1件目"}}\n'
        '{"type":"user","message":{"content":"途中で途絶\n'
        "[1, 2]\n"
        '{"type":"user","message":{"content":"3件目"}}\n',
        encoding="utf-8",
    )

    detail = sessions.read_local_detail(_context(tmp_path), "claude", str(path))

    assert [event["text"] for event in detail["events"]] == ["1件目", "3件目"]
    assert detail["broken_lines"] == 2
    # 破損した1件が一覧全体を失敗させない。
    assert [entry.session_id for entry in sessions.list_local_sessions(_context(tmp_path))] == ["abc"]


def test_local_record_path_outside_the_roots_is_rejected(tmp_path: pathlib.Path) -> None:
    """保存先の外を指す読み取り要求を拒否する。"""
    context = _context(tmp_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")

    assert not sessions.is_local_record_path(context, str(outside))
    assert not sessions.is_local_record_path(context, str(tmp_path / "claude" / "projects" / ".." / "x.jsonl"))
    assert not sessions.is_local_record_path(context, str(tmp_path / "claude" / "projects" / "p" / "x.txt"))
    with pytest.raises(sessions.SessionNotFoundError):
        sessions.read_local_detail(context, "claude", str(outside))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/home/aki/.claude/projects/p/a.jsonl", True),
        ("/home/aki/.claude/projects/../../etc/a.jsonl", False),
        ("/home/aki/.claude/projects/p/a.txt", False),
        ("", False),
        ("C:\\Users\\aki\\a.jsonl", False),
    ],
)
def test_remote_record_path_is_validated_before_ssh(raw: str, expected: bool) -> None:
    """上位ディレクトリ参照と対象外の接尾辞は、SSH呼び出しの前に拒否する。"""
    assert sessions.is_safe_remote_record_path(raw) is expected


class _FakeRpcClient:
    """常駐RPCの接続状態と応答を差し替える検体。"""

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
                    "project": "/srv/work",
                    "session_id": "remote-session",
                    "path": "/home/aki/.codex/sessions/2026/09/01/rollout-x.jsonl",
                    "updated_at": 1_800_000_000,
                    "size": 12,
                }
            ],
        }
    )
    context = _context(tmp_path, remote_hosts=["circe"], ssh_runner=runner)

    entries, warnings = await sessions.list_sessions(context)

    assert warnings == []
    assert {(entry.host, entry.session_id) for entry in entries} == {
        ("local-host", "11111111-2222-3333-4444-555555555555"),
        ("circe", "remote-session"),
    }
    assert calls == [("circe", "list", [])]


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


def _failed_ssh(returncode: int, stderr: bytes) -> typing.Callable[..., subprocess.CompletedProcess[bytes]]:
    """指定した終了コードと標準エラー出力を返す`subprocess.run`の代用を組み立てる。"""

    def run(*args: typing.Any, **kwargs: typing.Any) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        return subprocess.CompletedProcess(args=["ssh"], returncode=returncode, stdout=b"", stderr=stderr)

    return run


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
    monkeypatch.setattr(sessions.subprocess, "run", _failed_ssh(2, stderr))
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
    monkeypatch.setattr(sessions.subprocess, "run", _failed_ssh(2, stderr))
    context = _context(tmp_path, remote_hosts=["down-host"])

    _, warnings = await sessions.list_sessions(context)

    reason = warnings[0]["reason"]
    assert "末尾の理由" in reason
    assert head not in reason
    assert len(reason) < len(head)


@pytest.mark.asyncio
async def test_host_status_reports_connection_state(tmp_path: pathlib.Path) -> None:
    """ホストごとの接続状態を返す。ローカルは常に接続済みとする。"""
    context = _context(tmp_path, remote_hosts=["circe"])

    assert await sessions.host_status(context) == {"local-host": "connected", "circe": "connecting"}

    async with context.state.lock:
        context.state.host_status["circe"] = "disconnected"
    assert (await sessions.host_status(context))["circe"] == "disconnected"


@pytest.mark.asyncio
async def test_remote_call_falls_back_to_single_ssh(tmp_path: pathlib.Path) -> None:
    """常駐RPCが未接続・失敗・エラー応答の場合は単発SSHへ切り替える。"""
    runner, calls = _runner_returning({"ok": True, "entries": []})
    context = _context(tmp_path, remote_hosts=["circe"], ssh_runner=runner)

    # 未接続。
    disconnected = _FakeRpcClient(connected=False, response={"ok": True, "entries": []})
    context.state.clients["circe"] = typing.cast(typing.Any, disconnected)
    assert await sessions._remote_call(context, "circe", "list", {}) == {"ok": True, "entries": []}
    # RPCが例外で失敗。
    context.state.clients["circe"] = typing.cast(typing.Any, _FakeRpcClient(connected=True, response=RuntimeError("切断")))
    assert await sessions._remote_call(context, "circe", "list", {}) == {"ok": True, "entries": []}
    # RPCがエラー応答を返した。
    failing = _FakeRpcClient(connected=True, response={"ok": False, "error": "no such file"})
    context.state.clients["circe"] = typing.cast(typing.Any, failing)
    assert await sessions._remote_call(context, "circe", "list", {}) == {"ok": True, "entries": []}

    assert calls == [("circe", "list", []), ("circe", "list", []), ("circe", "list", [])]
    assert failing.calls == [("list", {})]


@pytest.mark.asyncio
async def test_remote_call_uses_rpc_when_connected(tmp_path: pathlib.Path) -> None:
    """常駐RPCが応答する場合は単発SSHを起動しない。"""
    runner, calls = _runner_returning({"ok": True, "entries": []})
    context = _context(tmp_path, remote_hosts=["circe"], ssh_runner=runner)
    client = _FakeRpcClient(connected=True, response={"ok": True, "entries": [{"path": "/x.jsonl"}]})
    context.state.clients["circe"] = typing.cast(typing.Any, client)

    payload = await sessions._remote_call(context, "circe", "list", {})

    assert payload["entries"] == [{"path": "/x.jsonl"}]
    assert not calls
    assert client.calls == [("list", {})]


@pytest.mark.asyncio
async def test_remote_detail_is_normalized_like_local(tmp_path: pathlib.Path) -> None:
    """リモートの記録も同じ表示モデルへ正規化する。"""
    text = json.dumps({"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": "やあ"}}) + "\n"
    runner, calls = _runner_returning({"ok": True, "data": base64.b64encode(text.encode("utf-8")).decode("ascii")})
    context = _context(tmp_path, remote_hosts=["circe"], ssh_runner=runner)

    detail = await sessions.session_detail(context, "claude", "circe", "/home/aki/.claude/projects/p/abc.jsonl")

    assert detail["host"] == "circe"
    assert detail["session_id"] == "abc"
    assert [event["text"] for event in detail["events"]] == ["やあ"]
    assert calls[0][1] == "read"


@pytest.mark.asyncio
async def test_remote_subagents_are_listed_or_reported_as_unavailable(tmp_path: pathlib.Path) -> None:
    """リモートの記録もサブエージェント一覧を返し、当該欄を持たない応答は判定不能として区別する。"""
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
        context = _context(tmp_path, remote_hosts=["circe"], ssh_runner=runner)
        return await sessions.session_detail(context, "claude", "circe", "/home/aki/.claude/projects/p/abc.jsonl")

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
    context = _context(tmp_path, remote_hosts=["circe"], ssh_runner=runner)

    for engine, host, path in (
        ("gemini", "circe", "/a.jsonl"),
        ("claude", "unknown", "/a.jsonl"),
        ("claude", "circe", "/home/aki/../etc/a.jsonl"),
    ):
        with pytest.raises(sessions.SessionNotFoundError):
            await sessions.session_detail(context, engine, host, path)


@pytest.mark.asyncio
async def test_refresh_notification_is_delivered_to_subscribers(tmp_path: pathlib.Path) -> None:
    """SSE購読者へ一覧の再取得を促す通知を配信する。"""
    context = _context(tmp_path)
    queue = await sessions.subscribe(context.state)
    try:
        await sessions.deliver_refresh(context.state)
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=1)) == {"type": "refresh"}
        # キューが満杯の間は新規通知を破棄し、配信で待たない。
        await sessions.deliver_refresh(context.state)
        await sessions.deliver_refresh(context.state)
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=1)) == {"type": "refresh"}
    finally:
        await sessions.unsubscribe(context.state, queue)
    assert context.state.subscribers == set()


def test_local_hostname_must_not_collide_with_remote_hosts(tmp_path: pathlib.Path) -> None:
    """ローカルホスト名とリモートホスト名の重複は起動時に拒絶する。"""
    with pytest.raises(ValueError):
        _context(tmp_path, remote_hosts=["local-host"])
