"""agents_server MCPの公開契約とCodex・Claudeバックエンドを検証する。"""

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access

import asyncio
import pathlib
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, cast

import _agents_server_claude as claude_backend
import _agents_server_codex as codex_backend
import _agents_server_state as state
import agents_server_mcp as subject
import pytest

_FORBIDDEN_PUBLIC_KEYS = {"turn_id", "result_available"}


def _assert_no_forbidden_keys(value: Any) -> None:
    """応答と入れ子のprevious_resultから内部状態キーを除外する。"""
    if isinstance(value, dict):
        assert _FORBIDDEN_PUBLIC_KEYS.isdisjoint(value)
        for nested in value.values():
            _assert_no_forbidden_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_forbidden_keys(nested)


def _complete(session: subject.SessionState, *, message: str = "完了", error: Any = None) -> None:
    """テスト用sessionを結果取得可能な終端状態へ進める。"""
    session.status = "failed" if error is not None else "completed"
    session.agent_message = message
    session.error = error
    session.turn_completed = True
    session.turn_start_ambiguous = False
    session.touch()


class FakeBackend:
    """共有MCP層の契約だけを検証するバックエンド。"""

    def __init__(self, sessions: dict[str, subject.SessionState], engine: str, delivery: str = "reply_started") -> None:
        self.sessions = sessions
        self.engine = engine
        self.delivery = delivery
        self.interrupt_calls = 0
        self.send_calls = 0
        self.resume_calls: list[str] = []

    async def start(self, prompt: str, cwd: str, model: str | None, effort: str | None) -> subject.SessionState:
        del prompt
        session = subject.SessionState(
            session_id=f"{self.engine}-session",
            cwd=cwd,
            model=model,
            effort=effort,
            engine=self.engine,
        )
        self.sessions[session.session_id] = session
        state._initialize_turn(session)
        return session

    async def resume(
        self,
        session_id: str,
        prompt: state.ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
    ) -> subject.SessionState:
        async def accept_prompt(value: str) -> None:
            del value

        self.resume_calls.append(session_id)
        session = subject.SessionState(
            session_id=session_id,
            cwd=cwd,
            model=model,
            effort=effort,
            engine=self.engine,
        )
        self.sessions[session_id] = session
        state._initialize_turn(session)
        await prompt.deliver(accept_prompt)
        return session

    async def send_message(self, session: subject.SessionState, prompt: str) -> dict[str, Any]:
        del prompt
        self.send_calls += 1
        if session.terminal:
            previous = session.previous_result()
            state._begin_reply(session)
            return {"delivery": self.delivery, "previous_result": previous}
        return {"delivery": "steered"}

    async def interrupt(self, session: subject.SessionState) -> None:
        del session
        self.interrupt_calls += 1

    async def close(self) -> None:
        """バックエンド終了処理のダミー。"""


class BlockingInterruptBackend(FakeBackend):
    """中断要求の配送を解除イベントまで停止する偽バックエンド。"""

    def __init__(self, sessions: dict[str, subject.SessionState], engine: str) -> None:
        super().__init__(sessions, engine)
        self.interrupt_started = asyncio.Event()
        self.release_interrupt = asyncio.Event()

    async def interrupt(self, session: subject.SessionState) -> None:
        del session
        self.interrupt_calls += 1
        self.interrupt_started.set()
        await self.release_interrupt.wait()


class BlockingResumeBackend(FakeBackend):
    """保存済みsessionの再開を解除イベントまで停止する偽バックエンド。"""

    def __init__(self, sessions: dict[str, subject.SessionState], engine: str) -> None:
        super().__init__(sessions, engine)
        self.resume_started = asyncio.Event()
        self.release_resume = asyncio.Event()

    async def resume(
        self,
        session_id: str,
        prompt: state.ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
    ) -> subject.SessionState:
        self.resume_started.set()
        await self.release_resume.wait()
        return await super().resume(session_id, prompt, cwd, model, effort)


class ConcurrentOwnerGoneBackend(FakeBackend):
    """2件の継続要求へ同時に所有主体終了を返す偽バックエンド。"""

    def __init__(self, sessions: dict[str, subject.SessionState], engine: str) -> None:
        super().__init__(sessions, engine)
        self.owner_gone_session: subject.SessionState | None = None
        self.send_started = 0
        self.both_started = asyncio.Event()

    async def send_message(self, session: subject.SessionState, prompt: str) -> dict[str, Any]:
        if session is self.owner_gone_session:
            self.send_started += 1
            if self.send_started == 2:
                self.both_started.set()
            await self.both_started.wait()
            raise state.SessionOwnerGoneError("owner gone")
        return await super().send_message(session, prompt)


def _manager_with_fake(engine: str, delivery: str = "reply_started") -> tuple[subject.AgentsServerManager, FakeBackend]:
    """指定engineだけをFakeBackendへ差し替えた共有managerを返す。"""
    manager = subject.AgentsServerManager()
    backend = FakeBackend(manager.sessions, engine, delivery)
    if engine == "codex":
        manager._codex = backend
    else:
        manager._claude = backend
    return manager, backend


def test_backend_imports_survive_plugin_path_removal(tmp_path: pathlib.Path) -> None:
    """MCP初期化後にプラグイン配置を除去しても両backendを生成できる。"""
    source_dir = pathlib.Path(__file__).parent
    script_dir = tmp_path / "plugin" / "scripts"
    script_dir.mkdir(parents=True)
    for name in (
        "agents_server_mcp.py",
        "_agents_server_codex.py",
        "_agents_server_claude.py",
        "_agents_server_state.py",
    ):
        shutil.copyfile(source_dir / name, script_dir / name)

    check = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import shutil, sys\n"
                "from pathlib import Path\n"
                "script_dir = Path(sys.argv[1])\n"
                "sys.path.insert(0, str(script_dir))\n"
                "import agents_server_mcp as subject\n"
                "assert 'claude_agent_sdk' not in sys.modules\n"
                "sys.path.remove(str(script_dir))\n"
                "shutil.rmtree(script_dir)\n"
                "assert subject._MANAGER._backend('codex').__class__.__name__ == 'AppServerManager'\n"
                "assert subject._MANAGER._backend('claude').__class__.__name__ == 'ClaudeServerManager'\n"
            ),
            str(script_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr


def test_public_tools_are_exactly_four_async_operations() -> None:
    """公開ツール集合をstart・wait・send_message・killへ固定する。"""
    assert set(subject.mcp._tool_manager._tools) == {"start", "wait", "send_message", "kill"}


def test_public_timeout_schemas_expose_unified_defaults() -> None:
    """公開schemaの待機系操作が270秒の既定timeoutと省略契約を示す。"""
    assert subject.DEFAULT_WAIT_TIMEOUT == 270.0
    assert subject.DEFAULT_KILL_TIMEOUT == 270.0
    assert subject.DEFAULT_SEND_MESSAGE_TIMEOUT == 270.0
    assert codex_backend.DEFAULT_WAIT_TIMEOUT == 300.0
    wait_tool = subject.mcp._tool_manager.get_tool("wait")
    send_tool = subject.mcp._tool_manager.get_tool("send_message")
    kill_tool = subject.mcp._tool_manager.get_tool("kill")
    assert wait_tool is not None
    assert send_tool is not None
    assert kill_tool is not None

    wait_timeout = wait_tool.parameters["properties"]["timeout"]
    assert wait_timeout["default"] == 270.0
    assert wait_timeout["description"] == (
        "待機上限秒数。固有のtimeout要件がなければ引数を省略して通常既定を使う。0は待機せず現状態を返す。"
    )
    assert "通常の既定は270秒" in wait_tool.description
    assert "固有のtimeout要件がなければ引数を省略して通常既定を使う" in wait_tool.description
    assert "`timeout=0`は待機せず現状態を返す" in wait_tool.description
    send_timeout = send_tool.parameters["properties"]["timeout"]
    assert send_timeout["default"] == 270.0
    assert send_timeout["description"] == (
        "継続要求の配送結果が確定するまでの待機上限秒数。固有のtimeout要件がなければ引数を省略して通常既定を使う。"
        "委譲先の応答生成の完了は待たない。0以下は受理しない。"
    )
    assert "通常の既定は270秒" in send_tool.description
    assert "固有のtimeout要件がなければ引数を省略して通常既定を使う" in send_tool.description
    assert "待つのは継続要求の配送結果が確定するまで" in send_tool.description
    assert "委譲先の応答生成の完了ではない" in send_tool.description
    assert "上限に達した場合は配送の成否が確定しないため、`wait`で状態を確認する" in send_tool.description
    kill_timeout = kill_tool.parameters["properties"]["timeout"]
    assert kill_timeout["default"] == 270.0
    assert kill_timeout["description"] == (
        "中断要求後に終端を待つ上限秒数。固有のtimeout要件がなければ引数を省略して通常既定を使う。"
        "0は中断要求配送後の現状態を返す。"
    )
    assert "通常の既定は270秒" in kill_tool.description
    assert "固有のtimeout要件がなければ引数を省略して通常既定を使う" in kill_tool.description
    assert "`timeout=0`は中断要求配送後の現状態を返す" in kill_tool.description


def test_progress_excerpt_normalizes_newline_and_keeps_tail() -> None:
    """進捗本文は改行を除き、長文では末尾80文字だけを返す。"""
    assert state._progress_excerpt("a\r\nb\rc\nd") == "a b c d"
    value = state._progress_excerpt("x" * 100)
    assert value == "…" + "x" * 80


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
async def test_start_projects_shared_state_without_internal_fields(engine: str, tmp_path: pathlib.Path) -> None:
    """両engineの開始応答が同じ公開射影を持つ。"""
    manager, _ = _manager_with_fake(engine)
    response = await manager.start(engine, "調査", str(tmp_path))
    assert response == {
        "session_id": f"{engine}-session",
        "engine": engine,
        "status": "running",
        "progress": "",
    }
    _assert_no_forbidden_keys(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
async def test_send_message_resumes_expired_session_id(engine: str, tmp_path: pathlib.Path) -> None:
    """期限切れ識別子へのsend_messageが同じ会話の新しいturnを開始する。"""
    manager, backend = _manager_with_fake(engine)
    session = subject.SessionState("saved-session", str(tmp_path), engine=engine)
    _complete(session)
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session

    response = await manager.send_message("saved-session", "続行")

    assert response["session_id"] == "saved-session"
    assert response["status"] == "running"
    assert response["delivery"] == "reply_started"
    assert "previous_result" not in response
    assert backend.resume_calls == ["saved-session"]
    assert "saved-session" not in manager.expired_sessions


@pytest.mark.asyncio
async def test_wait_returns_same_terminal_result_without_consuming_state(tmp_path: pathlib.Path) -> None:
    """waitは終端結果を何度呼んでも同じ本文で返す。"""
    manager, _ = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex")
    _complete(session, message="最終結果", error={"message": "補足"})
    manager.sessions[session.session_id] = session
    first = await manager.wait(session.session_id, timeout=0)
    second = await manager.wait(session.session_id, timeout=0)
    assert first == second
    assert first == {
        "session_id": "thread-1",
        "engine": "codex",
        "status": "failed",
        "progress": "",
        "agent_message": "最終結果",
        "error": {"message": "補足"},
    }
    _assert_no_forbidden_keys(first)


@pytest.mark.asyncio
async def test_wait_timeout_zero_does_not_return_unfinished_result(tmp_path: pathlib.Path) -> None:
    """未終端sessionのtimeout=0は本文なしの現状態を返す。"""
    manager, _ = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex")
    manager.sessions[session.session_id] = session
    response = await manager.wait(session.session_id, timeout=0)
    assert response == {
        "session_id": "thread-1",
        "engine": "codex",
        "status": "running",
        "progress": "",
    }


@pytest.mark.asyncio
async def test_kill_timeout_zero_returns_request_state(tmp_path: pathlib.Path) -> None:
    """killのtimeout=0は中断要求の受理後に現在状態を返す。"""
    manager, backend = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    response = await manager.kill(session.session_id, timeout=0)

    assert response == {
        "session_id": "thread-1",
        "engine": "codex",
        "status": "running",
        "progress": "",
        "kill_requested": True,
    }
    assert backend.interrupt_calls == 1


@pytest.mark.asyncio
async def test_kill_waits_for_terminal_result_and_preserves_request_marker(tmp_path: pathlib.Path) -> None:
    """正のtimeoutを指定したkillは終端結果と要求済み状態を返す。"""
    manager, backend = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    kill_task = asyncio.create_task(manager.kill(session.session_id, timeout=1))
    while backend.interrupt_calls == 0:
        await asyncio.sleep(0)
    _complete(session, message="中断結果")
    await manager._notify_waiters()

    assert await kill_task == {
        "session_id": "thread-1",
        "engine": "codex",
        "status": "completed",
        "progress": "",
        "agent_message": "中断結果",
        "kill_requested": True,
    }


@pytest.mark.asyncio
async def test_kill_terminal_session_is_idempotent_without_backend_request(tmp_path: pathlib.Path) -> None:
    """終端済みsessionへのkillは要求を送らず結果を返す。"""
    manager, backend = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex")
    _complete(session, message="既存結果")
    manager.sessions[session.session_id] = session

    response = await manager.kill(session.session_id, timeout=0)

    assert response["kill_requested"] is False
    assert response["agent_message"] == "既存結果"
    assert backend.interrupt_calls == 0


@pytest.mark.asyncio
async def test_concurrent_kill_requests_share_one_backend_request(tmp_path: pathlib.Path) -> None:
    """同一turnへの並行killは中断要求を1回だけ送る。"""
    manager, backend = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    first, second = await asyncio.gather(
        manager.kill(session.session_id, timeout=0),
        manager.kill(session.session_id, timeout=0),
    )

    assert first["kill_requested"] is True
    assert second["kill_requested"] is True
    assert backend.interrupt_calls == 1


@pytest.mark.asyncio
async def test_kill_lock_wait_respects_positive_timeout(tmp_path: pathlib.Path) -> None:
    """正のtimeoutはturn制御ロックの取得待ちにも適用する。"""
    manager, backend = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    async with session.turn_control_lock:
        with pytest.raises(TimeoutError, match="kill timed out: thread-1"):
            await manager.kill(session.session_id, timeout=0.01)

    assert backend.interrupt_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -0.1])
async def test_send_message_rejects_non_positive_timeout(timeout: float, tmp_path: pathlib.Path) -> None:
    """継続要求のtimeoutは正の値だけを受理し、backendへ配送しない。"""
    manager, backend = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    with pytest.raises(ValueError, match="timeout must be positive"):
        await manager.send_message(session.session_id, "追加指示", timeout=timeout)

    assert backend.send_calls == 0


@pytest.mark.asyncio
async def test_kill_timeout_zero_bounds_turn_control_lock_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """killのtimeout=0でも中断要求配送前のロック待ちを有限時間で打ち切る。"""
    monkeypatch.setattr(subject, "DEFAULT_SEND_MESSAGE_TIMEOUT", 0.01, raising=False)
    manager, backend = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    async with session.turn_control_lock:
        with pytest.raises(TimeoutError, match="the interrupt request was not delivered"):
            await asyncio.wait_for(manager.kill(session.session_id, timeout=0), timeout=0.1)

    assert backend.interrupt_calls == 0


@pytest.mark.asyncio
async def test_kill_timeout_zero_bounds_interrupt_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """killのtimeout=0でも中断要求の配送待ちを有限時間で打ち切る。"""
    monkeypatch.setattr(subject, "DEFAULT_SEND_MESSAGE_TIMEOUT", 0.01, raising=False)
    manager = subject.AgentsServerManager()
    backend = BlockingInterruptBackend(manager.sessions, "codex")
    manager._codex = backend
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    with pytest.raises(TimeoutError, match="interrupt delivery is undetermined"):
        await asyncio.wait_for(manager.kill(session.session_id, timeout=0), timeout=0.1)

    assert session.session_id in manager.sessions
    assert backend.interrupt_calls == 1
    assert session.interrupt_requested is False

    backend.release_interrupt.set()
    response = await manager.kill(session.session_id, timeout=0)

    assert response["kill_requested"] is True
    assert backend.interrupt_calls == 2


@pytest.mark.asyncio
async def test_send_message_timeout_covers_turn_control_lock(tmp_path: pathlib.Path) -> None:
    """継続要求のtimeoutはturn制御ロックの取得を含む操作全体へ適用する。"""
    manager = subject.AgentsServerManager()
    backend = claude_backend.ClaudeServerManager(manager.sessions, manager._condition)
    manager._claude = backend
    session = subject.SessionState("claude-locked", str(tmp_path), engine="claude")
    manager.sessions[session.session_id] = session

    async with session.turn_control_lock:
        with pytest.raises(TimeoutError, match="send_message timed out: claude-locked"):
            await manager.send_message(session.session_id, "追加指示", timeout=0.01)


@pytest.mark.asyncio
async def test_send_message_timeout_covers_resume(tmp_path: pathlib.Path) -> None:
    """継続要求のtimeoutは保存済みsessionの再開待ちにも適用する。"""
    manager = subject.AgentsServerManager()
    backend = BlockingResumeBackend(manager.sessions, "claude")
    manager._claude = backend
    session = subject.SessionState("claude-expired", str(tmp_path), engine="claude")
    _complete(session)
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session

    try:
        with pytest.raises(TimeoutError, match="send_message timed out: claude-expired"):
            await manager.send_message(session.session_id, "追加指示", timeout=0.01)

        assert backend.resume_started.is_set()
    finally:
        await manager.close()

    assert not manager._pending_resumes


@pytest.mark.asyncio
async def test_concurrent_owner_gone_send_messages_resume_once(tmp_path: pathlib.Path) -> None:
    """所有主体終了を同時検出した継続要求は再開を共有して両方を配送する。"""
    manager = subject.AgentsServerManager()
    backend = ConcurrentOwnerGoneBackend(manager.sessions, "claude")
    manager._claude = backend
    session = subject.SessionState("claude-race", str(tmp_path), engine="claude")
    manager.sessions[session.session_id] = session
    backend.owner_gone_session = session

    first, second = await asyncio.gather(
        manager.send_message(session.session_id, "先行指示", timeout=1),
        manager.send_message(session.session_id, "後続指示", timeout=1),
    )

    assert backend.resume_calls == [session.session_id]
    assert {first["delivery"], second["delivery"]} == {"reply_started", "steered"}


@pytest.mark.asyncio
async def test_kill_timeout_after_delivery_distinguishes_terminal_wait(tmp_path: pathlib.Path) -> None:
    """中断要求配送後の終端待ち超過を未配送と区別する。"""
    manager, backend = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    with pytest.raises(
        TimeoutError,
        match="the interrupt request was delivered but the turn did not terminate",
    ):
        await manager.kill(session.session_id, timeout=0.01)

    assert backend.interrupt_calls == 1


@pytest.mark.asyncio
async def test_kill_timeout_before_codex_turn_id_reports_not_delivered(tmp_path: pathlib.Path) -> None:
    """Codexのturn_id待ち超過を中断要求未配送として報告する。"""
    manager, backend = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex")
    manager.sessions[session.session_id] = session

    with pytest.raises(TimeoutError, match="the interrupt request was not delivered"):
        await manager.kill(session.session_id, timeout=0.01)

    assert backend.interrupt_calls == 0


@pytest.mark.asyncio
async def test_send_message_rejects_active_interrupt_without_backend_call(tmp_path: pathlib.Path) -> None:
    """中断要求が有効な未終端turnへ継続入力を送らない。"""
    manager, backend = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.interrupt_requested = True
    manager.sessions[session.session_id] = session

    with pytest.raises(ValueError, match="session is being interrupted: thread-1"):
        await manager.send_message(session.session_id, "追加指示")

    assert backend.send_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
@pytest.mark.parametrize("delivery", ["reply_started", "reply_failed", "reply_ambiguous"])
async def test_send_message_terminal_session_returns_previous_result_without_internal_fields(
    engine: str,
    delivery: str,
    tmp_path: pathlib.Path,
) -> None:
    """終端後の継続入力は回収済みフラグを要求せず、直前本文を退避する。"""
    manager, _ = _manager_with_fake(engine, delivery)
    session = subject.SessionState("thread-1", str(tmp_path), engine=engine)
    _complete(session, message="直前の結果")
    manager.sessions[session.session_id] = session
    response = await manager.send_message(session.session_id, "続行")
    assert response["delivery"] == delivery
    assert response["session_id"] == "thread-1"
    assert response["engine"] == engine
    assert response["status"] == "running"
    assert response["previous_result"] == {
        "session_id": "thread-1",
        "engine": engine,
        "status": "completed",
        "agent_message": "直前の結果",
    }
    _assert_no_forbidden_keys(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
@pytest.mark.parametrize(
    ("delivery", "expected_progress"),
    [("reply_started", ""), ("reply_ambiguous", ""), ("reply_failed", "直前の進捗")],
)
async def test_reply_resets_progress_only_after_delivery_is_accepted(
    engine: str,
    delivery: str,
    expected_progress: str,
    tmp_path: pathlib.Path,
) -> None:
    """reply失敗時は直前の進捗を保持し、開始済み又は曖昧時だけ破棄する。"""
    manager, _ = _manager_with_fake(engine, delivery)
    session = subject.SessionState("thread-1", str(tmp_path), engine=engine)
    session.set_progress("直前の進捗")
    _complete(session)
    manager.sessions[session.session_id] = session

    response = await manager.send_message(session.session_id, "続行")

    assert response["delivery"] == delivery
    assert response["progress"] == expected_progress


@pytest.mark.asyncio
async def test_send_message_steered_response_has_no_previous_result(tmp_path: pathlib.Path) -> None:
    """実行中turnへの追加指示はsteeredとして本文を退避しない。"""
    manager, _ = _manager_with_fake("codex")
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex")
    session.turn_id = "turn-1"
    manager.sessions[session.session_id] = session
    response = await manager.send_message(session.session_id, "追加指示")
    assert response == {
        "delivery": "steered",
        "session_id": "thread-1",
        "engine": "codex",
        "status": "running",
        "progress": "",
    }


class FakeCodexClient:
    """Codex App Server要求を記録する偽クライアント。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.reader_failure = None
        self._turn_count = 0

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        self.requests.append((method, params))
        if method in {"thread/start", "thread/resume"}:
            thread_id = params.get("threadId", "thread-codex")
            return {"thread": {"id": thread_id}}
        if method == "turn/start":
            self._turn_count += 1
            return {"turn": {"id": f"turn-{self._turn_count}"}}
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        return {}

    async def close(self) -> None:
        """実クライアントと同じ終了インターフェースを提供する。"""
        self.closed = True


class BlockingResumeCodexClient(FakeCodexClient):
    """最初のthread/resume応答を明示イベントまで保留する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.resume_started = asyncio.Event()
        self.release_resume = asyncio.Event()
        self.resume_finished = asyncio.Event()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "thread/resume":
            self.resume_started.set()
            await self.release_resume.wait()
        result = await super().request(method, params)
        if method == "thread/resume":
            self.resume_finished.set()
        return result


class LostTurnStartClient(FakeCodexClient):
    """thread/start成功後にturn/start応答を喪失する偽クライアント。"""

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "turn/start":
            raise codex_backend.AppServerError("turn/start response lost")
        return await super().request(method, params)


class SteerRaceClient(FakeCodexClient):
    """steer拒否とturn終端の競合を再現する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.steer_called = asyncio.Event()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "turn/steer":
            self.steer_called.set()
            raise codex_backend.JsonRpcResponseError(method, -32600, "turn is not active")
        return await super().request(method, params)


class ServerRequestClient(FakeCodexClient):
    """server-initiated requestへの応答を記録する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


class InterruptErrorClient(FakeCodexClient):
    """turn/interruptへJSON-RPC errorを返す偽クライアント。"""

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "turn/interrupt":
            raise codex_backend.JsonRpcResponseError(method, -32600, "interrupt rejected")
        return await super().request(method, params)


class CompletingInterruptClient(FakeCodexClient):
    """中断要求の配送中に対象turnを終端させる偽クライアント。"""

    def __init__(self, backend: codex_backend.AppServerManager, session: subject.SessionState) -> None:
        super().__init__()
        self.backend = backend
        self.session = session

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "turn/interrupt":
            await self.backend._handle_notification(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": self.session.session_id,
                        "turn": {"id": self.session.turn_id, "status": "interrupted", "error": None},
                    },
                }
            )
        return await super().request(method, params)


@pytest.mark.asyncio
async def test_concurrent_kills_preserve_shared_request_when_turn_completes_before_lock_handoff(
    tmp_path: pathlib.Path,
) -> None:
    """ロック待ち中にturnが終端しても並行killは要求済み状態を共有する。"""
    manager = subject.AgentsServerManager()
    backend = codex_backend.AppServerManager(manager.sessions, manager._condition)
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session
    backend.client = cast(Any, CompletingInterruptClient(backend, session))
    manager._codex = backend

    async with session.turn_control_lock:
        first_task = asyncio.create_task(manager.kill(session.session_id, timeout=1))
        await asyncio.sleep(0)
        second_task = asyncio.create_task(manager.kill(session.session_id, timeout=1))
        await asyncio.sleep(0)

    first, second = await asyncio.gather(first_task, second_task)

    assert first["kill_requested"] is True
    assert second["kill_requested"] is True
    assert session.status == "interrupted"


@pytest.mark.asyncio
async def test_codex_start_uses_noninteractive_policy_and_shared_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Codex開始時の承認・sandbox固定値と公開射影を検証する。"""
    manager = codex_backend.AppServerManager()
    client = FakeCodexClient()

    async def ensure_client() -> FakeCodexClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    session = await manager.start("調査", str(tmp_path), "gpt-test", "high")
    assert session.status == "running"
    thread_start = client.requests[0][1]
    turn_start = client.requests[1][1]
    assert thread_start["approvalPolicy"] == "never"
    assert thread_start["sandbox"] == "danger-full-access"
    assert turn_start["sandboxPolicy"] == {"type": "dangerFullAccess"}
    assert turn_start["model"] == "gpt-test"
    assert turn_start["effort"] == "high"


@pytest.mark.asyncio
async def test_codex_turn_start_response_loss_remains_running_until_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """初回turn/start応答喪失後もsessionを保持し、完了通知で結果取得可能にする。"""
    manager = codex_backend.AppServerManager()
    client = LostTurnStartClient()

    async def ensure_client() -> LostTurnStartClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    session = await manager.start("調査", str(tmp_path))
    assert session.status == "running"
    assert session.turn_start_ambiguous is True
    await manager._handle_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": session.session_id,
                "turn": {"id": "turn-lost", "status": "completed", "error": None},
            },
        }
    )
    assert session.result_available is True


@pytest.mark.asyncio
async def test_codex_steer_rejection_waits_for_terminal_race_then_replies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """steer拒否後は同一turnの終端を待ってからreplyへ切り替える。"""
    manager = codex_backend.AppServerManager()
    client = SteerRaceClient()

    async def ensure_client() -> SteerRaceClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    session = await manager.start("調査", str(tmp_path))
    manager.client = cast(Any, client)
    send_task = asyncio.create_task(manager.send_message(session, "続行"))
    await client.steer_called.wait()
    assert send_task.done() is False
    await manager._handle_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": session.session_id,
                "turn": {"id": session.turn_id, "status": "completed", "error": None},
            },
        }
    )
    response = await send_task
    assert response["delivery"] == "reply_started"
    assert [method for method, _ in client.requests].count("thread/resume") == 1


@pytest.mark.asyncio
async def test_codex_server_request_marks_target_failed_and_replies(tmp_path: pathlib.Path) -> None:
    """非対話server requestへerror応答し、対象sessionをfailedへ遷移する。"""
    manager = codex_backend.AppServerManager()
    client = ServerRequestClient()
    manager.client = cast(Any, client)
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex")
    manager.sessions[session.session_id] = session

    await manager._handle_server_request(
        {"id": 7, "method": "item/tool/requestUserInput", "params": {"threadId": session.session_id}}
    )

    assert session.status == "failed"
    assert session.result_available is True
    assert client.sent[-1]["id"] == 7
    assert client.sent[-1]["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_codex_reader_failure_marks_all_active_sessions_failed(tmp_path: pathlib.Path) -> None:
    """reader異常は同じ接続上の全active sessionをfailedへ遷移する。"""
    manager = codex_backend.AppServerManager()
    for session_id in ("thread-1", "thread-2"):
        manager.sessions[session_id] = subject.SessionState(session_id, str(tmp_path), engine="codex")

    await manager._handle_client_failure(RuntimeError("invalid JSON line"))

    assert {session.status for session in manager.sessions.values()} == {"failed"}
    assert all(session.result_available for session in manager.sessions.values())
    assert all(session.retention_deadline is not None for session in manager.sessions.values())


@pytest.mark.asyncio
async def test_codex_interrupt_response_error_is_recorded_on_target_turn(tmp_path: pathlib.Path) -> None:
    """turn/interruptの応答エラーを対象turnの状態へ記録する。"""
    manager = codex_backend.AppServerManager()
    manager.client = cast(Any, InterruptErrorClient())
    session = subject.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    await manager._interrupt(session.session_id, session.turn_id)

    assert session.error == {"message": "turn/interrupt: interrupt rejected"}
    assert session.protocol_warnings == ["turn/interrupt failed: turn/interrupt: interrupt rejected"]


@pytest.mark.asyncio
async def test_codex_json_rpc_process_passes_stream_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """App Server subprocessへ64KiB超のJSONLを許容するstream limitを渡す。"""
    observed: dict[str, Any] = {}

    async def create_subprocess(*_args: Any, **kwargs: Any) -> Any:
        observed.update(kwargs)
        raise RuntimeError("capture complete")

    monkeypatch.setattr(codex_backend.asyncio, "create_subprocess_exec", create_subprocess)
    client = codex_backend.JsonRpcProcess(lambda _message: asyncio.sleep(0), lambda _message: asyncio.sleep(0))
    with pytest.raises(RuntimeError, match="capture complete"):
        await client.start()
    assert observed["limit"] == codex_backend.APP_SERVER_STREAM_LIMIT_BYTES
    assert observed["limit"] > 64 * 1024


@pytest.mark.asyncio
async def test_codex_backend_send_supports_terminal_reply_without_result_flag(tmp_path: pathlib.Path) -> None:
    """Codexの終端後replyが結果回収フラグなしで開始できる。"""
    manager = codex_backend.AppServerManager()
    client = FakeCodexClient()
    manager.client = cast(Any, client)
    session = subject.SessionState("thread-codex", str(tmp_path), engine="codex")
    session.turn_id = "turn-old"
    _complete(session, message="Codex結果")
    manager.sessions[session.session_id] = session
    response = await manager.send_message(session, "続行")
    assert response["delivery"] == "reply_started"
    assert response["status"] == "running"
    assert response["previous_result"]["agent_message"] == "Codex結果"
    _assert_no_forbidden_keys(response)


@pytest.mark.asyncio
async def test_codex_progress_notification_is_shared(tmp_path: pathlib.Path) -> None:
    """Codexのdelta通知を共有SessionStateのprogressへ反映する。"""
    manager = codex_backend.AppServerManager()
    session = subject.SessionState("thread-codex", str(tmp_path), engine="codex")
    session.turn_id = "turn-1"
    manager.sessions[session.session_id] = session
    await manager._handle_notification(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-codex", "turnId": "turn-1", "itemId": "item-1", "delta": "進捗\n"},
        }
    )
    assert session.progress == "進捗 "

    await manager._handle_notification(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-codex", "turnId": "turn-1", "itemId": "item-1", "delta": "途中"},
        }
    )
    assert session.progress == "進捗 途中"

    await manager._handle_notification(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-codex",
                "turnId": "turn-1",
                "item": {"id": "item-1", "type": "agentMessage", "text": "完成した全文"},
            },
        }
    )
    assert session.progress == "完成した全文"

    await manager._handle_notification(
        {
            "method": "item/plan/delta",
            "params": {"threadId": "thread-codex", "turnId": "turn-1", "itemId": "plan-1", "delta": "計画"},
        }
    )
    assert session.progress == "完成した全文"


@pytest.mark.asyncio
async def test_shared_manager_integrates_codex_start_and_send_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """共有MCP層から実Codexバックエンドの開始と継続入力を通す。"""
    manager = subject.AgentsServerManager()
    backend = codex_backend.AppServerManager(manager.sessions, manager._condition)
    client = FakeCodexClient()

    async def ensure_client() -> FakeCodexClient:
        return client

    monkeypatch.setattr(backend, "_ensure_client", ensure_client)
    manager._codex = backend
    response = await manager.start("codex", "調査", str(tmp_path), "gpt-test", "high")
    assert response == {
        "session_id": "thread-codex",
        "engine": "codex",
        "status": "running",
        "progress": "",
    }
    backend.client = cast(Any, client)
    steered = await manager.send_message("thread-codex", "追加指示")
    assert steered["delivery"] == "steered"
    assert client.requests[-1][0] == "turn/steer"
    killed = await manager.kill("thread-codex", timeout=0)
    assert killed["kill_requested"] is True
    assert client.requests[-1][0] == "turn/interrupt"
    with pytest.raises(ValueError, match="being interrupted"):
        await manager.send_message("thread-codex", "競合入力")


@pytest.mark.asyncio
async def test_shared_manager_send_message_resumes_expired_codex_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """共有MCP層のstartが保存済みCodex threadを再開する。"""
    manager = subject.AgentsServerManager()
    backend = codex_backend.AppServerManager(manager.sessions, manager._condition)
    client = FakeCodexClient()

    async def ensure_client() -> FakeCodexClient:
        return client

    monkeypatch.setattr(backend, "_ensure_client", ensure_client)
    manager._codex = backend

    manager.expired_sessions["thread-saved"] = state.SessionResumeState(
        session_id="thread-saved",
        cwd=str(tmp_path),
        model="gpt-test",
        effort="high",
        engine="codex",
    )
    response = await manager.send_message("thread-saved", "続行")

    assert response == {
        "delivery": "reply_started",
        "session_id": "thread-saved",
        "engine": "codex",
        "status": "running",
        "progress": "",
    }
    assert client.requests[0] == (
        "thread/resume",
        {
            "threadId": "thread-saved",
            "cwd": str(tmp_path),
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
            "model": "gpt-test",
        },
    )
    assert client.requests[1][0] == "turn/start"


@pytest.mark.asyncio
async def test_codex_resume_timeout_drops_prompt_without_duplicate_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Codex再開のtimeout後に旧promptを配送せず、後続入力で再開を重複しない。"""
    manager = subject.AgentsServerManager()
    backend = codex_backend.AppServerManager(manager.sessions, manager._condition)
    client = BlockingResumeCodexClient()

    async def ensure_client() -> BlockingResumeCodexClient:
        return client

    monkeypatch.setattr(backend, "_ensure_client", ensure_client)
    backend.client = cast(Any, client)
    manager._codex = backend
    session_id = "thread-pending"
    manager.expired_sessions[session_id] = state.SessionResumeState(
        session_id=session_id,
        cwd=str(tmp_path),
        model=None,
        effort=None,
        engine="codex",
    )

    try:
        with pytest.raises(TimeoutError, match="send_message timed out: thread-pending"):
            await manager.send_message(session_id, "再開指示", timeout=0.01)

        assert await manager.wait(session_id, timeout=0) == {
            "session_id": session_id,
            "engine": "codex",
            "status": "running",
            "progress": "",
        }
        assert session_id not in manager.expired_sessions

        client.release_resume.set()
        await asyncio.wait_for(client.resume_finished.wait(), timeout=0.1)
        assert [method for method, _params in client.requests] == ["thread/resume"]
        response = await manager.send_message(session_id, "後続指示", timeout=1)

        assert response["delivery"] == "reply_started"
        assert [method for method, _params in client.requests].count("thread/resume") == 1
        turn_starts = [params for method, params in client.requests if method == "turn/start"]
        assert [item["text"] for params in turn_starts for item in params["input"]] == ["後続指示"]
    finally:
        client.release_resume.set()
        await manager.close()


class SystemMessage:
    """Claude SDK initメッセージの偽型。"""

    subtype = "init"

    def __init__(self, session_id: str) -> None:
        self.data = {"session_id": session_id}


class AssistantMessage:
    """Claude SDK assistantメッセージの偽型。"""

    def __init__(self, text: str) -> None:
        self.content = [SimpleNamespace(text=text)]


class MultipleBlockAssistantMessage:
    """複数TextBlockを持つClaude assistantメッセージの偽型。"""

    def __init__(self, *texts: str) -> None:
        self.content = [SimpleNamespace(text=text) for text in texts]


class ResultMessage:
    """Claude SDK resultメッセージの偽型。"""

    is_error = False

    def __init__(self, result: str, terminal_reason: str | None = None) -> None:
        self.result = result
        self.errors: list[str] = []
        self.terminal_reason = terminal_reason


class FakeClaudeClient:
    """ClaudeSDKClientの最小互換。"""

    def __init__(self, streams: list[list[Any]]) -> None:
        self.streams = [list(stream) for stream in streams]
        self.queries: list[str] = []
        self.interrupts = 0
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        self.connected = True

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def interrupt(self) -> None:
        self.interrupts += 1

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message

        return stream()

    async def disconnect(self) -> None:
        self.disconnected = True


class DelayedClaudeClient(FakeClaudeClient):
    """init後の通常メッセージ間隔を遅延できる偽クライアント。"""

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for index, message in enumerate(messages):
                if index:
                    await asyncio.sleep(0.08)
                yield message

        return stream()


class InterruptAwareClaudeClient(FakeClaudeClient):
    """interruptの受理後に中断結果を返す偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([[SystemMessage("claude-interrupted")]])
        self.interrupt_event = asyncio.Event()

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message
            await self.interrupt_event.wait()
            yield ResultMessage("中断結果", "aborted_streaming")

        return stream()

    async def interrupt(self) -> None:
        self.interrupts += 1
        self.interrupt_event.set()


class FailingClaudeClient(FakeClaudeClient):
    """init後にmessage stream例外を発生させる偽クライアント。"""

    def receive_messages(self):
        async def stream():
            yield SystemMessage("claude-failed")
            raise RuntimeError("stream failed")

        return stream()


class SynchronizedFailingClaudeClient(FakeClaudeClient):
    """同じ待機バッチでmessage stream例外を発生させる偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([])
        self.message_waiting = asyncio.Event()
        self.release_message = asyncio.Event()

    def receive_messages(self):
        async def stream():
            yield SystemMessage("claude-batch-failed")
            self.message_waiting.set()
            await self.release_message.wait()
            raise RuntimeError("stream failed")

        return stream()


class BlockingContinuationClaudeClient(FakeClaudeClient):
    """継続入力のqueryを停止して所有タスクの取り消しを再現する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([[SystemMessage("claude-blocking")]])
        self.message_waiting = asyncio.Event()
        self.query_started = asyncio.Event()
        self.release_query = asyncio.Event()

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        if len(self.queries) > 1:
            self.query_started.set()
            await self.release_query.wait()

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message
            self.message_waiting.set()
            await self.release_query.wait()

        return stream()


class BlockingResumeClaudeClient(FakeClaudeClient):
    """resumeの最初のqueryを記録前に保留する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([])
        self.query_started = asyncio.Event()
        self.query_cancelled = asyncio.Event()
        self.release_query = asyncio.Event()
        self.stop_stream = asyncio.Event()
        self.connect_calls = 0
        self.query_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        await super().connect()

    async def query(self, prompt: str) -> None:
        self.query_calls += 1
        if self.query_calls == 1:
            self.query_started.set()
            try:
                await self.release_query.wait()
            except asyncio.CancelledError:
                self.query_cancelled.set()
                raise
        await super().query(prompt)

    def receive_messages(self):
        async def stream():
            yield SystemMessage("claude-pending")
            await self.stop_stream.wait()

        return stream()


@pytest.mark.asyncio
async def test_claude_command_channel_resolves_pending_requests_when_closed() -> None:
    """チャネル閉鎖時に滞留要求を解決し、閉鎖後の受理を拒否する。"""
    channel = claude_backend._CommandChannel()
    future = channel.send("prompt", "続行")

    channel.close()

    with pytest.raises(state.SessionOwnerGoneError, match="session owner task has ended"):
        await asyncio.wait_for(future, timeout=0.1)
    with pytest.raises(state.SessionOwnerGoneError, match="session owner task has ended"):
        channel.send("prompt", "閉鎖後").cancel()


@pytest.mark.asyncio
async def test_claude_command_classification_uses_state_when_dequeued(tmp_path: pathlib.Path) -> None:
    """投入後に終端した継続入力をreplyとして処理し、直前結果を退避する。"""
    client = FakeClaudeClient([[AssistantMessage("reply中"), ResultMessage("reply結果")]])
    manager = claude_backend.ClaudeServerManager(client_factory=lambda _options: client)
    session = subject.SessionState("claude-race", str(tmp_path), engine="claude")
    channel = claude_backend._CommandChannel()
    manager.sessions[session.session_id] = session
    manager._channels[session.session_id] = channel

    send_task = asyncio.create_task(manager.send_message(session, "続行"))
    command = await channel.get()
    _complete(session, message="直前結果")
    iterator = await manager._handle_command(client, session, command, None)
    response = await send_task

    assert response == {
        "delivery": "reply_started",
        "previous_result": {
            "session_id": "claude-race",
            "engine": "claude",
            "status": "completed",
            "agent_message": "直前結果",
        },
    }
    assert iterator is not None
    assert client.queries == ["続行"]


@pytest.mark.asyncio
async def test_claude_message_stream_failure_resolves_retrieved_command(tmp_path: pathlib.Path) -> None:
    """message stream例外と同じ待機バッチの継続要求を所有タスク終了で解決する。"""
    client = SynchronizedFailingClaudeClient()
    manager = claude_backend.ClaudeServerManager(client_factory=lambda _options: client)
    try:
        session = await manager.start("調査", str(tmp_path))
        await asyncio.wait_for(client.message_waiting.wait(), timeout=0.1)
        channel = manager._channels[session.session_id]
        future = channel.send("prompt", "同時要求")
        client.release_message.set()

        with pytest.raises(state.SessionOwnerGoneError, match="session owner task has ended"):
            await asyncio.wait_for(future, timeout=0.1)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_claude_owner_cancellation_resolves_active_command(tmp_path: pathlib.Path) -> None:
    """所有タスク取り消し時に処理中の継続要求を所有タスク終了で解決する。"""
    client = BlockingContinuationClaudeClient()
    manager = claude_backend.ClaudeServerManager(client_factory=lambda _options: client)
    session = await manager.start("調査", str(tmp_path))
    await asyncio.wait_for(client.message_waiting.wait(), timeout=0.1)
    send_task = asyncio.create_task(manager.send_message(session, "継続"))
    await asyncio.wait_for(client.query_started.wait(), timeout=0.1)

    await manager.close()

    with pytest.raises(state.SessionOwnerGoneError, match="session owner task has ended"):
        await asyncio.wait_for(send_task, timeout=0.1)


@pytest.mark.asyncio
async def test_send_message_timeout_reports_undetermined_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """継続要求の配送結果が確定しない場合に指定上限で打ち切る。"""
    client = BlockingContinuationClaudeClient()
    manager = subject.AgentsServerManager()
    backend = claude_backend.ClaudeServerManager(
        manager.sessions,
        manager._condition,
        client_factory=lambda _options: client,
        expire_session=manager._expire_session,
    )
    manager._claude = backend
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args: SimpleNamespace())
    try:
        session = await backend.start("調査", str(tmp_path))
        await asyncio.wait_for(client.message_waiting.wait(), timeout=0.1)

        with pytest.raises(TimeoutError, match="send_message timed out: claude-blocking; delivery is undetermined"):
            await manager.send_message(session.session_id, "継続", timeout=0.01)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_claude_resume_timeout_drops_prompt_without_duplicate_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Claude再開のtimeout後に旧promptを配送せず、後続入力で再開を重複しない。"""
    client = BlockingResumeClaudeClient()
    manager = subject.AgentsServerManager()
    backend = claude_backend.ClaudeServerManager(
        manager.sessions,
        manager._condition,
        client_factory=lambda _options: client,
        expire_session=manager._expire_session,
    )
    manager._claude = backend
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args: SimpleNamespace())
    session_id = "claude-pending"
    manager.expired_sessions[session_id] = state.SessionResumeState(
        session_id=session_id,
        cwd=str(tmp_path),
        model=None,
        effort=None,
        engine="claude",
    )

    try:
        with pytest.raises(TimeoutError, match="send_message timed out: claude-pending"):
            await manager.send_message(session_id, "再開指示", timeout=0.01)

        assert client.query_started.is_set()
        await asyncio.wait_for(client.query_cancelled.wait(), timeout=0.1)
        assert not client.queries
        assert await manager.wait(session_id, timeout=0) == {
            "session_id": session_id,
            "engine": "claude",
            "status": "running",
            "progress": "",
        }
        assert session_id not in manager.expired_sessions

        response = await manager.send_message(session_id, "後続指示", timeout=1)

        assert response["delivery"] == "reply_started"
        assert client.connect_calls == 1
        assert client.query_calls == 2
        assert client.queries == ["後続指示"]
    finally:
        client.release_query.set()
        client.stop_stream.set()
        await manager.close()


@pytest.mark.asyncio
async def test_claude_pending_resume_discards_previous_result_after_retention_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """再開待機中に保持期限を越えた直前結果を後続応答へ含めない。"""
    client = BlockingResumeClaudeClient()
    manager = subject.AgentsServerManager()
    backend = claude_backend.ClaudeServerManager(
        manager.sessions,
        manager._condition,
        client_factory=lambda _options: client,
        expire_session=manager._expire_session,
    )
    manager._claude = backend
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args: SimpleNamespace())
    session_id = "claude-pending"
    session = state.SessionState(session_id, str(tmp_path), engine="claude")
    session.status = "completed"
    session.turn_completed = True
    session.agent_message = "期限付き結果"
    session.retention_deadline = asyncio.get_running_loop().time() + 0.04
    original_deadline = session.retention_deadline
    manager.sessions[session_id] = session

    try:
        with pytest.raises(TimeoutError, match="send_message timed out: claude-pending"):
            await manager.send_message(session_id, "期限前指示", timeout=0.01)

        await asyncio.wait_for(client.query_cancelled.wait(), timeout=0.1)
        await asyncio.sleep(max(0.0, original_deadline - asyncio.get_running_loop().time()) + 0.01)
        response = await manager.send_message(session_id, "期限後指示", timeout=1)

        assert asyncio.get_running_loop().time() >= original_deadline
        assert "previous_result" not in response
        assert client.connect_calls == 1
        assert client.query_calls == 2
        assert client.queries == ["期限後指示"]
    finally:
        client.release_query.set()
        client.stop_stream.set()
        await manager.close()


@pytest.mark.asyncio
async def test_claude_timed_out_queued_prompt_is_not_delivered(tmp_path: pathlib.Path) -> None:
    """打ち切り済みで未処理の継続要求を所有タスクが後から配送しない。"""
    client = FakeClaudeClient([])
    manager = claude_backend.ClaudeServerManager(client_factory=lambda _options: client)
    session = subject.SessionState("claude-queued", str(tmp_path), engine="claude")
    channel = claude_backend._CommandChannel()
    manager.sessions[session.session_id] = session
    manager._channels[session.session_id] = channel

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(manager.send_message(session, "打ち切り"), timeout=0.01)

    command = await channel.get()
    await manager._handle_command(client, session, command, None)

    assert not client.queries


@pytest.mark.asyncio
async def test_claude_options_use_claude_code_preset(tmp_path: pathlib.Path) -> None:
    """Claude Agent SDKへClaude Code presetと設定読込元を渡す。"""
    options = claude_backend._build_options(str(tmp_path), "model", "high")
    assert options.system_prompt == {"type": "preset", "preset": "claude_code"}
    assert options.setting_sources == ["user", "project"]
    assert options.permission_mode == "bypassPermissions"


def test_claude_options_accept_saved_session_id(tmp_path: pathlib.Path) -> None:
    """Claude SDK optionsへ保存済みsession IDをresumeとして渡す。"""
    options = claude_backend._build_options(str(tmp_path), "model", "high", "claude-saved")
    assert options.resume == "claude-saved"


@pytest.mark.asyncio
async def test_claude_resume_owns_saved_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Claude backendがresume後の同じsession IDを新しい所有タスクへ登録する。"""
    client = FakeClaudeClient([[SystemMessage("claude-saved"), ResultMessage("再開結果")]])
    captured: dict[str, str | None] = {}

    def build_options(_cwd: str, _model: str | None, _effort: str | None, session_id: str | None = None) -> Any:
        captured["session_id"] = session_id
        return SimpleNamespace()

    manager = claude_backend.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude_backend, "_build_options", build_options)
    try:
        prompt = state.ResumePrompt("続行")
        session = await manager.resume("claude-saved", prompt, str(tmp_path), "model", "high")
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert captured == {"session_id": "claude-saved"}
        assert session.session_id == "claude-saved"
        assert session.agent_message == "再開結果"
    finally:
        await manager.close()


def test_claude_dependency_check_builds_options_without_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """依存検査は現在の作業ディレクトリでoptionsだけを構築する。"""
    calls: list[tuple[str, str | None, str | None]] = []

    def fake_build_options(cwd: str, model: str | None, effort: str | None) -> object:
        calls.append((cwd, model, effort))
        return object()

    monkeypatch.setattr(claude_backend, "_build_options", fake_build_options)
    claude_backend.check_dependencies()

    assert calls == [(str(pathlib.Path.cwd()), None, None)]


def test_dependency_check_cli_does_not_start_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """依存検査指定時はMCP stdioを起動しない。"""
    calls: list[bool] = []
    monkeypatch.setattr(claude_backend, "check_dependencies", lambda: calls.append(True))
    monkeypatch.setattr(subject.mcp, "run", lambda **_kwargs: pytest.fail("MCPを起動してはいけない"))

    assert subject.main(["--check-dependencies"]) == 0
    assert calls == [True]


def test_dependency_check_cli_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """依存検査の例外を握り潰さず呼び出し元へ伝える。"""

    def fail_check() -> None:
        raise ImportError("claude-agent-sdk is unavailable")

    monkeypatch.setattr(claude_backend, "check_dependencies", fail_check)
    with pytest.raises(ImportError, match="claude-agent-sdk is unavailable"):
        subject.main(["--check-dependencies"])


@pytest.mark.asyncio
async def test_claude_start_result_wait_and_reply(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Claude init・結果受信・終端後replyを1長命タスクで処理する。"""
    client = FakeClaudeClient(
        [
            [SystemMessage("claude-session"), AssistantMessage("途中経過"), ResultMessage("Claude結果")],
            [AssistantMessage("reply中"), ResultMessage("reply結果")],
        ]
    )
    options = SimpleNamespace(system_prompt={"type": "preset", "preset": "claude_code"})
    manager = claude_backend.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args: options)
    try:
        session = await manager.start("調査", str(tmp_path), "model", "high")
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert session.session_id == "claude-session"
        assert session.status == "completed"
        assert session.agent_message == "Claude結果"
        assert session.progress == "途中経過"
        reply = await manager.send_message(session, "続行")
        assert reply["delivery"] == "reply_started"
        assert reply["previous_result"]["agent_message"] == "Claude結果"
        _assert_no_forbidden_keys(reply)
        assert client.queries == ["調査", "続行"]
    finally:
        await manager.close()
    assert client.connected is True
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_claude_kill_uses_owner_task_interrupt_and_maps_terminal_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Claudeのkillは所有タスクからinterruptを呼び、中断理由を状態へ写像する。"""
    client = InterruptAwareClaudeClient()
    manager = subject.AgentsServerManager()
    backend = claude_backend.ClaudeServerManager(manager.sessions, manager._condition, client_factory=lambda _options: client)
    manager._claude = backend
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args: SimpleNamespace())

    try:
        start_response = await manager.start("claude", "調査", str(tmp_path))
        response = await manager.kill(start_response["session_id"], timeout=1)
        assert response["status"] == "interrupted"
        assert response["kill_requested"] is True
        assert response["agent_message"] == "中断結果"
        assert client.interrupts == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_claude_message_gap_does_not_cancel_stream(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """通常のメッセージ間隔が旧poll間隔を超えてもstreamを失敗扱いにしない。"""
    client = DelayedClaudeClient([[SystemMessage("claude-delayed"), AssistantMessage("途中"), ResultMessage("完了")]])
    manager = claude_backend.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args: SimpleNamespace())
    try:
        session = await manager.start("調査", str(tmp_path))
        for _ in range(40):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert session.status == "completed"
        assert session.agent_message == "完了"
        assert session.progress == "途中"
    finally:
        await manager.close()
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_claude_concatenates_multiple_text_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Claudeの複数TextBlockを改行区切りで連結して進捗へ保持する。"""
    assistant_message = AssistantMessage("一")
    assistant_message.content = MultipleBlockAssistantMessage("一", "二").content
    client = FakeClaudeClient([[SystemMessage("claude-blocks"), assistant_message, ResultMessage("完了")]])
    manager = claude_backend.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args: SimpleNamespace())
    try:
        session = await manager.start("調査", str(tmp_path))
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert session.progress == "一 二"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_claude_task_exception_disconnects_and_retains_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """message task例外時に切断し、failed結果を共有sessionへ保持する。"""
    client = FailingClaudeClient([])
    sessions: dict[str, subject.SessionState] = {}
    manager = claude_backend.ClaudeServerManager(sessions, client_factory=lambda _options: client)
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args: SimpleNamespace())
    session = await manager.start("調査", str(tmp_path))
    for _ in range(20):
        if client.disconnected:
            break
        await asyncio.sleep(0.01)
    assert client.disconnected is True
    assert sessions[session.session_id] is session
    assert session.status == "failed"
    assert session.error == {"message": "stream failed"}


@pytest.mark.asyncio
async def test_claude_retention_expiry_disconnects_and_removes_result_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保持期限経過時にSDKを切断し、結果recordを識別子だけへ縮小する。"""
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.01)
    client = FakeClaudeClient([[SystemMessage("claude-expired"), ResultMessage("完了")]])
    sessions: dict[str, subject.SessionState] = {}
    manager = subject.AgentsServerManager()
    manager.sessions = sessions
    backend = claude_backend.ClaudeServerManager(
        sessions,
        manager._condition,
        client_factory=lambda _options: client,
        expire_session=manager._expire_session,
    )
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args: SimpleNamespace())
    session = await backend.start("調査", str(tmp_path))
    for _ in range(20):
        if client.disconnected and session.session_id not in sessions:
            break
        await asyncio.sleep(0.01)
    assert client.disconnected is True
    assert session.session_id not in sessions
    assert manager.expired_sessions == {
        session.session_id: state.SessionResumeState(
            session_id=session.session_id,
            cwd=str(tmp_path),
            model=None,
            effort=None,
            engine="claude",
        )
    }
    with pytest.raises(ValueError, match="session retention expired: claude-expired"):
        await manager.wait(session.session_id, timeout=0)


@pytest.mark.asyncio
async def test_claude_server_close_disconnects_and_retains_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """サーバー終了時にSDKを切断し、取得済み結果recordは保持する。"""
    client = FakeClaudeClient([[SystemMessage("claude-close"), ResultMessage("完了")]])
    sessions: dict[str, subject.SessionState] = {}
    backend = claude_backend.ClaudeServerManager(sessions, client_factory=lambda _options: client)
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args: SimpleNamespace())
    session = await backend.start("調査", str(tmp_path))
    for _ in range(20):
        if session.result_available:
            break
        await asyncio.sleep(0.01)
    await backend.close()
    assert client.disconnected is True
    assert sessions[session.session_id].agent_message == "完了"


@pytest.mark.asyncio
async def test_claude_finished_task_send_message_resumes_with_retained_result(tmp_path: pathlib.Path) -> None:
    """所有タスク終了後の継続入力は保持済み結果を退避して会話を再開する。"""
    manager = subject.AgentsServerManager()
    clients = [
        FailingClaudeClient([]),
        FakeClaudeClient([[SystemMessage("claude-failed")]]),
    ]

    def client_factory(_options: Any) -> FakeClaudeClient:
        return clients.pop(0)

    backend = claude_backend.ClaudeServerManager(
        manager.sessions,
        manager._condition,
        client_factory=client_factory,
        expire_session=manager._expire_session,
    )
    manager._claude = backend
    session = await backend.start("調査", str(tmp_path))
    for _ in range(20):
        if session.result_available and session.session_id not in backend._channels:
            break
        await asyncio.sleep(0.01)

    result = await manager.wait(session.session_id, timeout=0)
    assert result["error"] == {"message": "stream failed"}
    response = await manager.send_message(session.session_id, "続行")

    assert response == {
        "delivery": "reply_started",
        "session_id": "claude-failed",
        "engine": "claude",
        "status": "running",
        "progress": "",
        "previous_result": {
            "session_id": "claude-failed",
            "engine": "claude",
            "status": "failed",
            "agent_message": "",
            "error": {"message": "stream failed"},
        },
    }
    assert not manager.expired_sessions
    assert manager.sessions[session.session_id].status == "running"
    await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
async def test_expired_session_wait_is_rejected_by_shared_manager(engine: str, tmp_path: pathlib.Path) -> None:
    """両engineで期限切れ結果を削除し、waitへ同じ理由を返す。"""
    manager, _ = _manager_with_fake(engine)
    session = subject.SessionState("expired", str(tmp_path), engine=engine)
    _complete(session)
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session
    for _ in range(2):
        with pytest.raises(ValueError, match="session retention expired: expired"):
            await manager.wait(session.session_id, timeout=0)
    assert "expired" not in manager.sessions
    assert manager.expired_sessions["expired"].session_id == "expired"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["wait", "send_message"])
async def test_unknown_session_is_distinct_from_expired_session(
    operation: str,
) -> None:
    """未登録の識別子を期限切れ識別子と区別する。"""
    manager, _ = _manager_with_fake("codex")
    with pytest.raises(ValueError, match="unknown session: missing"):
        if operation == "wait":
            await manager.wait("missing", timeout=0)
        else:
            await manager.send_message("missing", "続行")


@pytest.mark.parametrize("cwd", ["", "relative/path"])
def test_validate_cwd_rejects_empty_and_relative_paths(cwd: str) -> None:
    """cwd検証は空文字列と相対パスを拒否する。"""
    with pytest.raises(ValueError, match="cwd must be a non-empty absolute path"):
        subject._validate_cwd(cwd)


def test_validate_cwd_rejects_missing_absolute_path(tmp_path: pathlib.Path) -> None:
    """cwd検証は存在しない絶対パスを拒否する。"""
    with pytest.raises(ValueError, match="cwd is not an existing directory"):
        subject._validate_cwd(str(tmp_path / "missing"))


@pytest.mark.parametrize(
    ("model", "effort"),
    [("model", None), (None, "high"), ("", "high"), ("model", "")],
)
def test_validate_model_effort_rejects_incomplete_values(model: str | None, effort: str | None) -> None:
    """modelとeffortの片側指定及び空文字列を拒否する。"""
    with pytest.raises(ValueError, match="model and effort must"):
        subject._validate_model_effort(model, effort)
