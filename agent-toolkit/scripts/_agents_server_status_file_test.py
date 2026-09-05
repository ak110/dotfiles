"""agents_serverのstatusline向け状態ファイル契約を検証する。"""

# テストでは共有managerと状態モデルの内部境界も直接検証する。
# pylint: disable=protected-access

import asyncio
import datetime
import json
import pathlib

import _agents_server_state as state
import _agents_server_status_file as subject
import agents_server_mcp
import pytest


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        (
            {"CLAUDE_CODE_SESSION_ID": "root-session"},
            subject.StatusFileIdentity("root-session", "root.json", None),
        ),
        (
            {
                "AGENT_TOOLKIT_OWNER_SESSION": "owner",
                "AGENT_TOOLKIT_DELEGATED_SESSION": "1",
                "CLAUDE_CODE_SESSION_ID": "claude-child",
                "CODEX_THREAD_ID": "ignored-codex-child",
            },
            subject.StatusFileIdentity("owner", "claude-child.json", "claude-child"),
        ),
        (
            {
                "AGENT_TOOLKIT_OWNER_SESSION": "owner",
                "CLAUDE_CODE_SESSION_ID": "root-session",
                "CODEX_THREAD_ID": "codex-child",
            },
            subject.StatusFileIdentity("owner", "codex-child.json", "codex-child"),
        ),
        ({}, None),
        ({"CLAUDE_CODE_SESSION_ID": "../invalid"}, None),
        (
            {
                "AGENT_TOOLKIT_OWNER_SESSION": "owner",
                "CODEX_THREAD_ID": "invalid/child",
            },
            None,
        ),
    ],
)
def test_resolve_status_file_identity(environment: dict[str, str], expected: subject.StatusFileIdentity | None) -> None:
    """ルート・Claude委譲先・Codex委譲先と不正識別子を区別する。"""
    assert subject.resolve_status_file_identity(environment) == expected


def test_status_directory_uses_platform_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """状態ディレクトリをatk configと同じXDG規則から解決する。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert subject.status_directory("root") == tmp_path / "agent-toolkit" / "agents-server" / "root"


def test_status_directory_rejects_relative_xdg_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """相対XDG_STATE_HOMEではatk configと同じHOME配下へ状態を書き込む。"""
    monkeypatch.setenv("XDG_STATE_HOME", "relative-state")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert subject.status_directory("root") == (
        tmp_path / "home" / ".local" / "state" / "agent-toolkit" / "agents-server" / "root"
    )


@pytest.mark.asyncio
async def test_writer_serializes_announced_sessions_and_removes_delivered(
    tmp_path: pathlib.Path,
) -> None:
    """公開済みで未回収のsessionだけを状態ファイルへ書く。"""
    sessions: dict[str, state.SessionState] = {}
    writer = subject.StatusFileWriter(
        sessions,
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    hidden = state.SessionState("hidden", str(tmp_path), announced=False)
    visible = state.SessionState(
        "visible",
        str(tmp_path),
        engine="claude",
        model="sonnet[1m]",
        effort="low",
        model_type="execute",
        launch_kind="delegate",
        label="実装",
        announced=True,
    )
    sessions.update(hidden=hidden, visible=visible)
    visible.set_progress("進捗")
    writer.flush()

    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["host_session_id"] is None
    assert [item["session_id"] for item in payload["sessions"]] == ["visible"]
    assert payload["sessions"][0]["progress"] == "進捗"
    datetime.datetime.fromisoformat(payload["sessions"][0]["started_at"])

    visible.result_delivered = True
    visible.touch()
    writer.flush()
    assert not json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    writer.deactivate()
    assert not writer.path.parent.exists()


@pytest.mark.asyncio
async def test_writer_removes_session_at_retention_deadline(tmp_path: pathlib.Path) -> None:
    """期限前のsessionを表示し、call_atで期限到達後に再出力して除く。"""
    session = state.SessionState("retained", str(tmp_path), announced=True)
    session.status = "completed"
    session.agent_message = "完了"
    session.turn_completed = True
    session.touch()
    session.retention_deadline = asyncio.get_running_loop().time() + 0.03
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()

    assert json.loads(writer.path.read_text(encoding="utf-8"))["sessions"][0]["session_id"] == "retained"
    assert writer._retention_handle is not None
    await asyncio.sleep(0.05)
    assert not json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    writer.deactivate()


@pytest.mark.asyncio
async def test_writer_excludes_already_expired_session(tmp_path: pathlib.Path) -> None:
    """再出力時点で保持期限を過ぎたsessionを表示対象から除く。"""
    session = state.SessionState("expired", str(tmp_path), announced=True)
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    assert not json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    writer.deactivate()


@pytest.mark.asyncio
async def test_root_writer_removes_stale_files_on_activate(tmp_path: pathlib.Path) -> None:
    """ルートwriterは前回のJSONと一時ファイルを除いて新しいroot.jsonだけを書く。"""
    directory = subject.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    (directory / "stale.json").write_text("{}", encoding="utf-8")
    (directory / ".stale.json.token.tmp").write_text("temporary", encoding="utf-8")
    writer = subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )

    writer.activate()
    assert [path.name for path in directory.iterdir()] == ["root.json"]
    writer.deactivate()
    assert not directory.exists()


@pytest.mark.asyncio
async def test_nested_writer_preserves_root_file_on_deactivate(tmp_path: pathlib.Path) -> None:
    """入れ子の書込主体は自身のファイルだけを回収する。"""
    directory = subject.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    root_file = directory / "root.json"
    root_file.write_text("{}", encoding="utf-8")
    writer = subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "child.json", "child"),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    writer.deactivate()
    assert root_file.exists()
    assert not writer.path.exists()


@pytest.mark.asyncio
async def test_manager_writes_three_launch_kinds_and_removes_waited_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """3つの起動入口と結果回収を状態ファイルへ反映する。"""
    writer = subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = agents_server_mcp.AgentsServerManager(writer)
    backend = _FakeStatusBackend(manager.sessions)
    manager._codex = backend
    monkeypatch.setattr(
        agents_server_mcp._atk_config,
        "resolve_model_candidates",
        lambda _model_type: [("codex", "gpt-5.6-terra", "medium")],
    )
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()

    started = await manager.start("execute", "\n  実装を開始\n続き", str(tmp_path))
    await manager.start_explore(True, "調査する", str(tmp_path))
    await manager.start_shell("pytest -q", str(tmp_path), "結果を要約")
    writer.flush()
    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    assert [item["launch_kind"] for item in payload["sessions"]] == ["delegate", "explore", "shell"]
    assert [item["label"] for item in payload["sessions"]] == ["実装を開始", "調査する", "pytest -q"]

    session = manager.sessions[started["session_id"]]
    session.status = "completed"
    session.agent_message = "完了"
    session.turn_completed = True
    session.touch()
    await manager.wait(session.session_id, timeout=0)
    writer.flush()
    remaining = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert session.session_id not in {item["session_id"] for item in remaining}
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed", [False, True])
async def test_manager_writes_only_announced_candidate_after_fallback(
    delayed: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """候補切替で除外した試行を隠し、呼出元へ返したsessionだけを書く。"""
    writer = _status_writer(tmp_path)
    manager = agents_server_mcp.AgentsServerManager(writer)
    backend: _FakeStatusBackend = (
        _DelayedUnavailableStatusBackend(
            manager.sessions,
            condition=manager._condition,
            unavailable_models={"first"},
        )
        if delayed
        else _UnavailableStatusBackend(manager.sessions, unavailable_models={"first"})
    )
    manager._codex = backend
    monkeypatch.setattr(
        agents_server_mcp._atk_config,
        "resolve_model_candidates",
        lambda _model_type: [("codex", "first", "high"), ("codex", "second", "high")],
    )
    monkeypatch.setattr(agents_server_mcp, "START_AVAILABILITY_TIMEOUT", 0.01)
    writer.activate()

    response = await manager.start("execute", "実装", str(tmp_path))
    writer.flush()

    sessions = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert [item["session_id"] for item in sessions] == [response["session_id"]]
    assert sessions[0]["model"] == "second"
    await manager.close()


@pytest.mark.asyncio
async def test_manager_writes_only_last_failure_when_all_candidates_are_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """全候補が利用不能なら、応答へ載せた最後の失敗sessionだけを書く。"""
    writer = _status_writer(tmp_path)
    manager = agents_server_mcp.AgentsServerManager(writer)
    backend = _UnavailableStatusBackend(manager.sessions, unavailable_models={"first", "second"})
    manager._codex = backend
    monkeypatch.setattr(
        agents_server_mcp._atk_config,
        "resolve_model_candidates",
        lambda _model_type: [("codex", "first", "high"), ("codex", "second", "high")],
    )
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()

    response = await manager.start("execute", "実装", str(tmp_path))
    writer.flush()

    sessions = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert [item["session_id"] for item in sessions] == [response["session_id"]]
    assert sessions[0]["model"] == "second"
    assert sessions[0]["status"] == "failed"
    await manager.close()


@pytest.mark.asyncio
async def test_manager_removes_kill_result_but_keeps_uncollected_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """killで返した結果だけを除き、未回収の終端結果は表示に残す。"""
    writer = _status_writer(tmp_path)
    manager = agents_server_mcp.AgentsServerManager(writer)
    backend = _FakeStatusBackend(manager.sessions)
    manager._codex = backend
    monkeypatch.setattr(
        agents_server_mcp._atk_config,
        "resolve_model_candidates",
        lambda _model_type: [("codex", "model", "medium")],
    )
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()
    killed = await manager.start("execute", "kill対象", str(tmp_path))
    uncollected = await manager.start("execute", "未回収", str(tmp_path))
    for session_id in (killed["session_id"], uncollected["session_id"]):
        session = manager.sessions[session_id]
        session.status = "completed"
        session.agent_message = "完了"
        session.turn_completed = True
        session.touch()

    response = await manager.kill(killed["session_id"], timeout=0)
    writer.flush()

    assert response["agent_message"] == "完了"
    sessions = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert [item["session_id"] for item in sessions] == [uncollected["session_id"]]
    await manager.close()


@pytest.mark.asyncio
async def test_manager_without_writer_does_not_create_status_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """書込主体が無効なmanagerはsession開始後も状態ファイルを作成しない。"""
    manager = agents_server_mcp.AgentsServerManager(None)
    manager._codex = _FakeStatusBackend(manager.sessions)
    monkeypatch.setattr(
        agents_server_mcp._atk_config,
        "resolve_model_candidates",
        lambda _model_type: [("codex", "model", "medium")],
    )
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))

    await manager.start("execute", "実装", str(tmp_path))

    assert not list(tmp_path.rglob("*.json"))
    await manager.close()


def _status_writer(tmp_path: pathlib.Path) -> subject.StatusFileWriter:
    """公開manager経路用のルートwriterを返す。"""
    return subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )


class _FakeStatusBackend:
    """状態ファイルの公開フローだけを通す偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState]) -> None:
        self.sessions = sessions
        self.count = 0

    async def start(
        self,
        _prompt: str,
        cwd: str,
        model: str,
        effort: str,
        *,
        model_type: str,
        launch_kind: state.LaunchKind,
        excluded_candidates: frozenset[state.ModelCandidate],
    ) -> state.SessionState:
        self.count += 1
        session = state.SessionState(
            f"session-{self.count}",
            cwd,
            model=model,
            effort=effort,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
        )
        self.sessions[session.session_id] = session
        state._initialize_turn(session)
        return session

    async def close(self) -> None:
        """外部資源を持たないため何もしない。"""


class _UnavailableStatusBackend(_FakeStatusBackend):
    """指定モデルをengine利用不能として終端させる偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], *, unavailable_models: set[str]) -> None:
        super().__init__(sessions)
        self._unavailable_models = unavailable_models

    async def start(
        self,
        _prompt: str,
        cwd: str,
        model: str,
        effort: str,
        *,
        model_type: str,
        launch_kind: state.LaunchKind,
        excluded_candidates: frozenset[state.ModelCandidate],
    ) -> state.SessionState:
        session = await super().start(
            _prompt,
            cwd,
            model,
            effort,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
        )
        if session.model in self._unavailable_models:
            session.status = "failed"
            session.error = {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}
            session.turn_completed = True
            session.touch()
        return session


class _DelayedUnavailableStatusBackend(_FakeStatusBackend):
    """起動応答後に指定モデルを利用不能として終端させる偽バックエンド。"""

    def __init__(
        self,
        sessions: dict[str, state.SessionState],
        *,
        condition: asyncio.Condition,
        unavailable_models: set[str],
    ) -> None:
        super().__init__(sessions)
        self._condition = condition
        self._unavailable_models = unavailable_models
        self._pending: list[asyncio.Task[None]] = []

    async def start(
        self,
        _prompt: str,
        cwd: str,
        model: str,
        effort: str,
        *,
        model_type: str,
        launch_kind: state.LaunchKind,
        excluded_candidates: frozenset[state.ModelCandidate],
    ) -> state.SessionState:
        session = await super().start(
            _prompt,
            cwd,
            model,
            effort,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
        )
        if session.model in self._unavailable_models:
            self._pending.append(asyncio.create_task(self._fail_after_response(session)))
        return session

    async def _fail_after_response(self, session: state.SessionState) -> None:
        await asyncio.sleep(0)
        session.status = "failed"
        session.error = {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}
        session.turn_completed = True
        session.touch()
        async with self._condition:
            self._condition.notify_all()

    async def close(self) -> None:
        await asyncio.gather(*self._pending)
