"""Codex backendのsession状態遷移を検証する。"""

# テストではloggerを含む内部境界を直接検証する。
# pylint: disable=protected-access

import asyncio
import json
import pathlib
import shutil
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import state as shared_state


class _ThreadStartClient:
    """thread/startの入力を保持して固定threadを返すスタブ。"""

    def __init__(self) -> None:
        self.params: dict[str, Any] | None = None

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "thread/start"
        self.params = params
        return {"thread": {"id": "inner-thread"}}


class _TierClient:
    """実際のbackendが送る要求と送信通知を再現し、モデルの実行を伴わずtierを確かめる。"""

    closed = False
    reader_failure = None

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method: str, params: dict[str, Any], *, on_sent: Any = None) -> dict[str, Any]:
        self.requests.append((method, dict(params)))
        if on_sent is not None:
            on_sent()
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "tier-thread"}}
        assert method == "turn/start"
        return {"turn": {"id": f"turn-{len(self.requests)}"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [True, False])
async def test_config_changes_apply_to_new_reply_and_restored_turns(
    initial: bool, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開configの変更が3RPCへ明示され、実際に送った最新turnの速度を保持する。"""
    monkeypatch.setattr(subject._atk_config, "_config_dir", lambda: tmp_path / "config")
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE", raising=False)

    def set_fast(enabled: bool) -> None:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "set", "codex_fast_mode", str(enabled).lower()], home=tmp_path)
        assert not capsys.readouterr().err

    set_fast(initial)
    client = _TierClient()
    manager = subject.AppServerManager({}, asyncio.Condition(), publish_registry=True)
    monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
    session = await manager.start("開始", str(tmp_path), "gpt-6-sol", "medium")
    tier = "priority" if initial else "default"
    assert [method for method, _params in client.requests] == ["thread/start", "turn/start"]
    assert all(params["serviceTier"] == tier for _method, params in client.requests)
    assert session.fast_mode is initial
    session.status = "completed"
    session.turn_completed = True
    session.touch()
    set_fast(not initial)
    client.requests.clear()
    reply = await manager.send_message(session, "続行")
    assert reply["delivery"] == "reply_started"
    assert all(params["serviceTier"] == ("default" if initial else "priority") for _method, params in client.requests)
    assert session.fast_mode is (not initial)

    set_fast(initial)
    restored_client = _TierClient()
    restarted = subject.AppServerManager({}, asyncio.Condition())
    monkeypatch.setattr(restarted, "_ensure_client", AsyncMock(return_value=restored_client))
    restored = await restarted.resume(
        session.session_id,
        shared_state.ResumePrompt("再開"),
        str(tmp_path),
        session.model,
        session.effort,
        turn_seq=session.turn_seq,
        fast_mode=session.fast_mode,
    )
    assert [method for method, _params in restored_client.requests] == ["thread/resume", "turn/start"]
    assert all(params["serviceTier"] == tier for _method, params in restored_client.requests)
    assert restored.fast_mode is initial


class _SilentClient:
    """要求を受理したまま応答を返さないApp Serverクライアントのスタブ。"""

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del method, params  # noqa
        await asyncio.Event().wait()
        return {}  # pragma: no cover - 待機が解けないことを表す到達不能の分岐


class _HangingStream:
    """行を返さないまま待機し続けるストリームのスタブ。"""

    async def readline(self) -> bytes:
        await asyncio.Event().wait()
        return b""  # pragma: no cover - 待機が解けないことを表す到達不能の分岐


class _AcceptingStdin:
    """書き込みを受理するだけのstdinのスタブ。"""

    def write(self, data: bytes) -> None:
        del data  # noqa

    async def drain(self) -> None:
        return None


class _DisconnectedStdin:
    """書込時に接続断を返すstdinのスタブ。"""

    def write(self, data: bytes) -> None:
        del data  # noqa
        raise BrokenPipeError("peer disconnected")

    async def drain(self) -> None:
        return None


class _SilentProcess:
    """起動後にJSON-RPC応答を返さない子プロセスのスタブ。"""

    def __init__(self) -> None:
        self.stdin = _AcceptingStdin()
        self.stdout = _HangingStream()
        self.stderr = _HangingStream()
        self.returncode: int | None = None
        self.pid = 123

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


@pytest.mark.asyncio
async def test_json_rpc_write_failure_preserves_bounded_diagnostics() -> None:
    """接続断は到達段階、子PIDおよび直前stderrを例外とログへ残す。"""
    client = subject.JsonRpcProcess(_ignore_message, _ignore_message)
    process = _SilentProcess()
    process.__dict__["stdin"] = _DisconnectedStdin()
    # 実プロセスの代わりにテスト用の代替オブジェクトを割り当てるため、静的な型判定の対象から外す。
    client.process = process  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
    client._initialization_stage = "initialized_sent"
    client._stderr_text = "network unavailable"

    with pytest.raises(subject.AppServerError) as exc_info:
        await client.send({"method": "test"})

    message = str(exc_info.value)
    assert "peer disconnected" in message
    assert "initialized_sent" in message
    assert "child_pid" in message
    assert "network unavailable" in message


async def _ignore_message(message: dict[str, Any]) -> None:
    del message  # noqa


class _InspectableAppServerManager(subject.AppServerManager):
    """通知入力をテストへ公開する。"""

    async def handle_notification(self, message: dict[str, Any]) -> None:
        await self._handle_notification(message)


def _completed_turn(session: shared_state.SessionState) -> dict[str, Any]:
    return {
        "method": "turn/completed",
        "params": {
            "threadId": session.session_id,
            "turn": {
                "id": session.turn_id,
                "status": "completed",
                "error": None,
            },
        },
    }


def _item_started(session: shared_state.SessionState, item_type: str) -> dict[str, Any]:
    return {
        "method": "item/started",
        "params": {
            "threadId": session.session_id,
            "turnId": session.turn_id,
            "item": {"id": f"item-{item_type}", "type": item_type},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("output_type", ["reasoning", "agentMessage"])
async def test_model_output_is_observed_only_after_non_input_item(tmp_path: pathlib.Path, output_type: str) -> None:
    """入力の記録である`userMessage`ではモデル出力を観測済みにせず、モデル由来のitemで観測済みにする。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_item_started(session, "userMessage"))
    after_input = session.model_output_observed
    await manager.handle_notification(_item_started(session, output_type))

    assert not after_input
    assert session.model_output_observed


@pytest.mark.asyncio
async def test_completed_turn_with_live_child_holds_result_for_auto_resume(tmp_path: pathlib.Path) -> None:
    """未観測の子sessionが残るturnは、待機表明を完了報告として公開せず、自動再開まで結果を保留する。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_completed_turn(session))

    assert session.result_available is False
    assert session.awaiting_auto_resume is True
    assert session.status == "running"
    assert session.pending_result == {"status": "completed", "agent_message": "", "error": None}
    assert session.error is None
    assert session.live_child_session_ids == {"child-1"}
    await manager.close()


@pytest.mark.asyncio
async def test_completed_turn_after_consumed_auto_resume_publishes_unobserved_child(tmp_path: pathlib.Path) -> None:
    """そのturnで自動再開を消費済みなら保留せず、残る子sessionを未観測として記録して直ちに公開する。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    session.auto_resume_consumed = True
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_completed_turn(session))

    assert session.result_available is True
    assert session.status == "completed"
    assert session.awaiting_auto_resume is False
    assert session.error == {"unobservedSessions": ["child-1"]}
    assert session.live_child_session_ids == set()
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delivery", ("stdout", "output-file", "shell-stdout", "shell-output-file", "shell-unrelated", "failure", "unrelated")
)
async def test_cli_wait_updates_observed_child_sessions(tmp_path: pathlib.Path, delivery: str) -> None:
    """CLI待機が回収した子だけを観測済みにし、失敗時は未観測として残す。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    manager = _InspectableAppServerManager({session.session_id: session})
    result = json.dumps({"session_id": "child-1", "status": "completed"}) + "\n"
    if delivery in {"output-file", "shell-output-file"}:
        output_path = tmp_path / "wait-results.jsonl"
        output_path.write_text(result, encoding="utf-8")
        output = f"保存先: {output_path}\n"
    else:
        output = result
    command = "atk agents wait"
    if delivery in {"output-file", "shell-output-file"}:
        command = "timeout 300 atk agents wait --output-file " + str(tmp_path / "wait-results.jsonl")
    if delivery == "unrelated":
        command = "printf 'unrelated command'"
    if delivery == "shell-stdout":
        command = '/bin/bash -lc "atk agents wait"'
    if delivery == "shell-output-file":
        command = f'/bin/sh -c "timeout 300 atk agents wait --output-file {tmp_path / "wait-results.jsonl"}"'
    if delivery == "shell-unrelated":
        command = '/bin/bash -lc "printf unrelated"'
    item = {
        "type": "commandExecution",
        "id": "wait-command",
        "command": command,
        "aggregatedOutput": output,
        "exitCode": 1 if delivery == "failure" else 0,
    }

    await manager.handle_notification(
        {"method": "item/completed", "params": {"threadId": session.session_id, "turnId": session.turn_id, "item": item}}
    )
    await manager.handle_notification(_completed_turn(session))

    # 回収できなかった子は追跡に残り、自動再開まで結果を保留する。回収済みなら保留せず直ちに公開する。
    unobserved = delivery in {"failure", "unrelated", "shell-unrelated"}
    assert session.awaiting_auto_resume is unobserved
    assert session.result_available is not unobserved
    assert session.live_child_session_ids == ({"child-1"} if unobserved else set())
    assert session.error is None
    await manager.close()


@pytest.mark.asyncio
async def test_thread_start_overrides_agents_server_with_owner_and_writer_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """内側MCPサーバーへルートと書込主体を完全な定義で配送する。"""
    client = _ThreadStartClient()
    manager = subject.AppServerManager(root_session_id="root-session")
    monkeypatch.setattr(manager, "_ensure_client", lambda: _return(client))
    monkeypatch.setattr(
        subject.status_file, "write_host_alias", lambda root, writer, thread: aliases.append((root, writer, thread))
    )
    aliases: list[tuple[str, str, str]] = []
    monkeypatch.setattr(manager, "_start_turn", lambda *_args: _return_none())

    await manager.start("実装する", str(tmp_path))

    assert client.params is not None
    server = client.params["config"]["mcp_servers"]["agents_server"]
    assert server["command"] == "uv"
    assert server["args"][-1].endswith("agent_toolkit/agents_server_mcp.py")
    assert set(server["env"]) == {"AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_STATUS_HOST_SESSION"}
    assert server["env"]["AGENT_TOOLKIT_OWNER_SESSION"] == "root-session"
    assert server["default_tools_approval_mode"] == "approve"
    assert "AGENT_TOOLKIT_DELEGATED_SESSION" not in server["env"]
    assert aliases == [("root-session", server["env"]["AGENT_TOOLKIT_STATUS_HOST_SESSION"], "inner-thread")]


@pytest.mark.asyncio
async def test_client_start_aborts_when_initialize_never_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """initializeの応答が返らない接続を上限で打ち切り、子プロセスを終了する。"""
    monkeypatch.setattr(shared_state, "SESSION_INITIALIZATION_TIMEOUT", 0.05)
    process = _SilentProcess()

    async def _spawn(*args: Any, **kwargs: Any) -> _SilentProcess:
        del args, kwargs  # noqa
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    client = subject.JsonRpcProcess(_ignore_message, _ignore_message)

    with pytest.raises(shared_state.SessionInitializationTimeoutError):
        await client.start()

    assert client.closed is True
    assert process.returncode == -15


@pytest.mark.asyncio
async def test_client_logs_process_start_initialize_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex子プロセスの起動、initialize応答および終了を親loggerへ記録する。"""
    process = _SilentProcess()
    messages: list[str] = []

    async def _spawn(*args: Any, **kwargs: Any) -> _SilentProcess:
        del args, kwargs  # noqa
        return process

    def record(message: str, *args: object) -> None:
        messages.append(message % args)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(subject._LOG, "info", record)
    client = subject.JsonRpcProcess(_ignore_message, _ignore_message)
    client.request = AsyncMock(return_value={"serverInfo": {"name": "codex"}})  # type: ignore[method-assign]
    client.notify = AsyncMock()  # type: ignore[method-assign]

    await client.start()
    await client.close()

    assert any("Codex App Serverを起動しました" in message for message in messages)
    assert any("initialize応答を受信しました: keys=['serverInfo']" in message for message in messages)
    assert any("Codex App Serverを終了しました: returncode=-15" in message for message in messages)


@pytest.mark.asyncio
async def test_start_aborts_when_thread_start_never_returns(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """thread/startの応答が返らない起動を上限で打ち切り、session状態を登録せずに例外で返す。"""
    monkeypatch.setattr(shared_state, "SESSION_INITIALIZATION_TIMEOUT", 0.05)
    manager = subject.AppServerManager()
    monkeypatch.setattr(manager, "_ensure_client", lambda: _return(_SilentClient()))

    with pytest.raises(shared_state.SessionInitializationTimeoutError):
        await manager.start("実装する", str(tmp_path))

    assert not manager.sessions
    await manager.close()


def _make_plugin_root(base: pathlib.Path, version: str, *, versioned: bool) -> pathlib.Path:
    """`plugin.json`を持つ配布物rootを、版別ディレクトリの有無を変えて作成する。"""
    root = base / version / "agent-toolkit" if versioned else base / "checkout" / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(f'{{"version": "{version}"}}\n', encoding="utf-8")
    (root / "agent_toolkit").mkdir()
    (root / "agent_toolkit" / "agents_server_mcp.py").write_text("# entry\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _isolate_stable_plugin_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """複製先の記録とmanaged-tempの作成先をテストごとに分離する。"""
    monkeypatch.setattr(subject, "_stable_plugin_roots", {})
    monkeypatch.setattr(subject._managed_temp, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "managed-temp"))
    (tmp_path / "managed-temp").mkdir()


def test_versioned_plugin_root_is_copied_to_a_stable_location(tmp_path: pathlib.Path) -> None:
    """版別ディレクトリ配下の配布物rootは、複製元を失っても解決できる実体へ写す。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True)

    resolved = subject.resolve_stable_plugin_root(source)

    assert resolved != source
    shutil.rmtree(source)
    assert (resolved / "agent_toolkit" / "agents_server_mcp.py").is_file()
    assert not (resolved / ".venv").exists()


def test_versioned_plugin_root_is_copied_once_per_source(tmp_path: pathlib.Path) -> None:
    """同じ複製元への解決を繰り返しても複製先は変わらない。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True)

    first = subject.resolve_stable_plugin_root(source)
    second = subject.resolve_stable_plugin_root(source)

    assert first == second
    assert first != source
    assert [entry.name for entry in first.parent.parent.iterdir() if entry.is_dir()] == [first.parent.name]


def test_plain_plugin_root_is_used_as_is(tmp_path: pathlib.Path) -> None:
    """版別ディレクトリに当たらない配布物rootは複製せずそのまま使う。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=False)

    assert subject.resolve_stable_plugin_root(source) == source


def test_copy_failure_falls_back_to_the_resolved_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """複製に失敗しても例外を伝播させず、解決したrootで委譲の起動を成立させる。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True)

    def _fail(*_args: Any, **_kwargs: Any) -> pathlib.Path:
        raise subject._managed_temp.ManagedTempError("一時領域を作成できない")

    monkeypatch.setattr(subject._managed_temp, "create_managed_temp", _fail)

    assert subject.resolve_stable_plugin_root(source) == source


def test_agents_server_config_uses_the_stable_plugin_root() -> None:
    """内側MCPサーバーの起動コマンドは、安定した配布物rootの実体だけを指す。"""
    config = subject.AppServerManager._agents_server_config("owner", "writer")

    args = config["mcp_servers"]["agents_server"]["args"]
    expected_root = subject.resolve_stable_plugin_root()
    assert args[1] == "--project"
    assert args[2] == str(expected_root)
    assert args[-1] == str(expected_root / "agent_toolkit" / "agents_server_mcp.py")
    assert pathlib.Path(args[-1]).is_file()


async def _return(value: Any) -> Any:
    return value


async def _return_none() -> None:
    return None


def _overloaded_turn(session: shared_state.SessionState, error_info: str = "serverOverloaded") -> dict[str, Any]:
    message = _completed_turn(session)
    message["params"]["turn"]["status"] = "failed"
    message["params"]["turn"]["error"] = {
        "message": "Selected model is at capacity. Please try a different model.",
        "codexErrorInfo": error_info,
    }
    return message


@pytest.mark.asyncio
async def test_overloaded_turn_after_availability_check_holds_result_and_reports_wait(tmp_path: pathlib.Path) -> None:
    """可用性確認を過ぎた後の過負荷は失敗を公開せず保留し、待機中の状態を`api_error`で示す。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = True
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_overloaded_turn(session))

    assert session.result_available is False
    assert session.awaiting_auto_resume is True
    assert session.status == "running"
    assert session.pending_result is not None
    assert session.pending_result["status"] == "failed"
    assert session.overload_resume_count == 1
    assert session.overload_resume_at is not None
    assert session.api_error is not None
    assert session.api_error["type"] == "serverOverloaded"
    assert session.api_error["http_status"] is None
    assert session.api_error["count"] == 1
    await manager.close()


@pytest.mark.asyncio
async def test_overloaded_reply_turn_is_held_even_before_flag(tmp_path: pathlib.Path) -> None:
    """委譲元の`send_message`で始めたturn（2番目以降）の過負荷も自動継続の対象とする。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-2", turn_seq=2)
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_overloaded_turn(session))

    assert session.awaiting_auto_resume is True
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("availability_checked", "error_info", "resume_count"),
    [
        (False, "serverOverloaded", 0),
        (True, "usageLimitExceeded", 0),
        (True, "rateLimitExceeded", 0),
        (True, "serverOverloaded", len(shared_state.OVERLOAD_RESUME_DELAYS_SECONDS)),
    ],
)
async def test_turn_outside_overload_resume_is_published_as_failed(
    tmp_path: pathlib.Path, availability_checked: bool, error_info: str, resume_count: int
) -> None:
    """起動確認前の過負荷、他の可用性失敗、上限到達後の過負荷は現行どおり失敗として公開する。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = availability_checked
    session.overload_resume_count = resume_count
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_overloaded_turn(session, error_info))

    assert session.result_available is True
    assert session.status == "failed"
    assert session.error["codexErrorInfo"] == error_info
    assert session.awaiting_auto_resume is False
    assert session.overload_resume_count == 0
    await manager.close()


@pytest.mark.asyncio
async def test_non_overload_completion_ends_overload_chain(tmp_path: pathlib.Path) -> None:
    """継続したturnが過負荷以外で終われば結果を公開し、連鎖の回数を戻す。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-2", turn_seq=2)
    session.overload_resume_count = 2
    session.overload_first_at = "2026-10-04T00:00:00+00:00"
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_completed_turn(session))

    assert session.result_available is True
    assert session.status == "completed"
    assert session.overload_resume_count == 0
    assert session.overload_first_at is None
    await manager.close()


@pytest.mark.asyncio
async def test_kill_during_overload_wait_publishes_last_failure_without_new_turn(tmp_path: pathlib.Path) -> None:
    """待機中の`kill`は新しいturnを始めず、保留した最後の失敗でsessionを終端する。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = True
    manager = _InspectableAppServerManager({session.session_id: session})
    await manager.handle_notification(_overloaded_turn(session))

    await manager.interrupt(session)

    assert session.result_available is True
    assert session.status == "failed"
    assert session.error["codexErrorInfo"] == "serverOverloaded"
    assert session.overload_resume_count == 0
    assert session.overload_resume_at is None
    assert session.turn_seq == 1
    await manager.close()


@pytest.mark.asyncio
async def test_send_message_during_overload_wait_delivers_instruction_as_reply(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """待機中の委譲元の`send_message`は保留を確定し、その指示を同じsessionのreplyとして配送する。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = True
    manager = _InspectableAppServerManager({session.session_id: session})
    await manager.handle_notification(_overloaded_turn(session))
    delivered: list[str] = []

    async def start_reply(_session: shared_state.SessionState, prompt: str) -> tuple[str, dict[str, Any], None]:
        delivered.append(prompt)
        return "reply_started", {}, None

    monkeypatch.setattr(manager, "_start_reply_locked", start_reply)

    result = await manager.send_message(session, "委譲元の指示")

    assert delivered == ["委譲元の指示"]
    assert result["delivery"] == "reply_started"
    assert result["previous_result"]["status"] == "failed"
    assert session.awaiting_auto_resume is False
    assert session.overload_resume_count == 0
    assert session.overload_resume_at is None
    await manager.close()
