"""Claude backendと共有する自動再開状態の契約を検証する。"""

import asyncio

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import os
import pathlib
import subprocess
import sys
import types
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from agent_toolkit._agents_server import (
    claude,
    launch_prompts,
    resume_waits,
    session_errors,
    state,
)
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import state_paths
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    BlockingContinuationClaudeClient,
    DelayedClaudeClient,
    ErrorAssistantMessage,
    FailingClaudeClient,
    FakeClaudeClient,
    InterruptAwareClaudeClient,
    MultipleBlockAssistantMessage,
    ResultMessage,
    StreamEvent,
    SynchronizedFailingClaudeClient,
    SystemMessage,
    _assert_no_forbidden_keys,
    _complete,
    _start_claude_until_available,
    _without_root,
    install_backend,
)


def test_debug_file_name_carries_session_id_after_initialization(tmp_path: pathlib.Path) -> None:
    """診断ログの名前にsession識別子を含め、時刻を比較せずに対応するsessionを判別できる状態にする。"""
    debug_file = tmp_path / "20260916T000000000000-delegate.log"
    debug_file.write_text("diagnostic", encoding="utf-8")

    renamed = claude.rename_debug_file_for_session(debug_file, "session-1", "delegate")

    assert renamed.name == "20260916T000000000000-session-1-delegate.log"
    assert renamed.read_text(encoding="utf-8") == "diagnostic"
    assert not debug_file.exists()


def test_debug_file_keeps_its_name_when_renaming_is_rejected(tmp_path: pathlib.Path) -> None:
    """改名できない実行環境では元の名前を保ち、後続の記録先を失わない。"""
    debug_file = tmp_path / "missing" / "20260916T000000000000-delegate.log"

    renamed = claude.rename_debug_file_for_session(debug_file, "session-1", "delegate")

    assert renamed == debug_file


class _SilentClient:
    """接続を保ったままinitメッセージを送らないSDKクライアントのスタブ。

    切断でCLIの子プロセスを終了する実クライアントの契約を模す。
    実クライアントは取り消された実行での切断では子プロセスの終了処理へ到達しないため、
    取り消し要求が残るtaskからの切断では、終了を記録せず例外を送出する。
    """

    def __init__(self) -> None:
        self.terminated = False

    async def connect(self) -> None:
        return None

    async def query(self, prompt: str) -> None:
        del prompt  # noqa

    async def receive_messages(self) -> AsyncIterator[Any]:
        await asyncio.Event().wait()
        yield None  # pragma: no cover - 待機が解けないことを表す到達不能の分岐

    async def disconnect(self) -> None:
        task = asyncio.current_task()
        assert task is not None
        if task.cancelling() > 0:
            raise asyncio.CancelledError
        await asyncio.sleep(0)
        self.terminated = True


@pytest.mark.parametrize(
    ("cmdline", "expected"),
    [
        (b"claude\0--settings=/tmp/settings.json\0", "/tmp/settings.json"),
        (b"claude\0--settings\0/tmp/settings.json\0", "/tmp/settings.json"),
        (b"claude\0--verbose\0", None),
        (b"claude\0--settings=\0", None),
        (b"claude\0--settings", None),
    ],
)
def test_settings_from_cmdline(cmdline: bytes, expected: str | None) -> None:
    """親プロセスの2種類のsettings引数だけから非空値を検出する。"""
    assert claude._settings_from_cmdline(cmdline) == expected  # pylint: disable=protected-access


@pytest.mark.parametrize("settings", [None, "/tmp/settings.json"])
def test_build_options_inherits_parent_settings(
    monkeypatch: pytest.MonkeyPatch,
    settings: str | None,
) -> None:
    """親cmdlineのsettings検出時だけSDKオプションへ同値を渡す。

    未検出時はsettingsキーを渡さず、従来のsetting_sourcesによる解決を維持する。
    """
    captured: dict[str, object] = {}

    class Options:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", types.SimpleNamespace(ClaudeAgentOptions=Options))
    monkeypatch.setattr(claude, "_parent_settings", lambda: settings)

    claude._build_options("/tmp", "model", "medium")  # pylint: disable=protected-access

    assert captured.get("settings") == settings
    assert ("settings" in captured) is (settings is not None)


def _capture_options(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """`_build_options`がSDKへ渡す引数を捕捉する。"""
    captured: dict[str, object] = {}

    class Options:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", types.SimpleNamespace(ClaudeAgentOptions=Options))
    monkeypatch.setattr(claude, "_parent_settings", lambda: None)
    return captured


def test_build_options_keeps_every_launch_out_of_bypass_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動区分によらずbypass系以外の権限モードで起動する。

    bypass系の受信側はセッション間メッセージを保留するため、起動したセッションが初期化を完了できない。
    """
    captured = _capture_options(monkeypatch)

    # 受理しない値を意図的に渡すテストのため、静的な型判定の対象から外す。
    claude._build_options("/tmp", "model", "medium", launch_kind="delegate")  # pylint: disable=protected-access

    assert captured["permission_mode"] == "auto"


@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
def test_build_options_loads_user_hooks_for_every_launch(
    monkeypatch: pytest.MonkeyPatch,
    launch_kind: claude.LaunchKind,
) -> None:
    """全起動区分でユーザー設定のplugin hookを読み、軽量起動の道具を限定し、部分出力を受け取る。

    部分出力を受け取らないと、`start`の可用性待機を打ち切るAPIの応答開始が届かない。
    """
    captured = _capture_options(monkeypatch)

    claude._build_options(  # pylint: disable=protected-access
        "/tmp", "model", "medium", launch_kind=launch_kind
    )

    assert captured["setting_sources"] == (["user", "project"] if launch_kind == "delegate" else ["user"])
    assert captured["include_partial_messages"] is True
    if launch_kind != "delegate":
        assert captured["skills"] == []
        assert captured["allowed_tools"] == claude._LAUNCH_ALLOWED_TOOLS[launch_kind]  # pylint: disable=protected-access


def test_build_options_passes_debug_file_to_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """診断ログの保存先を委譲先CLIの引数として渡す。"""
    captured = _capture_options(monkeypatch)
    debug_file = tmp_path / "delegate.log"

    claude._build_options("/tmp", "model", "medium", debug_file=debug_file)  # pylint: disable=protected-access

    assert captured["extra_args"] == {"debug-file": str(debug_file)}


@pytest.mark.parametrize("cli_path", ["/opt/claude/bin/claude", None])
def test_build_options_resolves_cli_from_path(monkeypatch: pytest.MonkeyPatch, cli_path: str | None) -> None:
    """PATHで見つかったCLIだけをSDKへ渡す。"""
    captured = _capture_options(monkeypatch)
    monkeypatch.setattr(claude.shutil, "which", lambda _name: cli_path)

    claude._build_options("/tmp", "model", "medium")  # pylint: disable=protected-access

    assert captured.get("cli_path") == cli_path
    assert ("cli_path" in captured) is (cli_path is not None)


def test_build_options_omits_debug_file_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """保存先を指定しない起動では診断ログの引数を渡さない。"""
    captured = _capture_options(monkeypatch)

    claude._build_options("/tmp", "model", "medium")  # pylint: disable=protected-access

    assert "extra_args" not in captured


def test_prepare_debug_file_drops_records_beyond_retention(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保持世代を超えた古い診断ログを自動的に削除する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    directory = tmp_path / claude._DEBUG_LOG_DIR_NAME  # pylint: disable=protected-access
    directory.mkdir(parents=True)
    retention = claude._DEBUG_LOG_RETENTION  # pylint: disable=protected-access
    existing = []
    for index in range(retention + 5):
        path = directory / f"{index:04d}.log"
        path.write_text("記録", encoding="utf-8")
        os.utime(path, (index, index))
        existing.append(path)

    created = claude._prepare_debug_file("delegate")  # pylint: disable=protected-access
    created.write_text("記録", encoding="utf-8")

    assert created.parent == directory
    assert len(sorted(directory.glob("*.log"))) == retention
    assert not existing[0].exists()
    assert existing[-1].exists()


def test_assistant_message_without_text_advances_activity_time() -> None:
    """ツール呼び出しだけのassistantメッセージでも活動時刻を進め、テキスト出力時刻は進めない。"""
    session = state.SessionState("session-1", "/tmp")
    session.updated_at = "2000-01-01T00:00:00+00:00"
    message = types.SimpleNamespace(
        content=[types.SimpleNamespace(id="toolu_1", name="Bash", input={"command": "ls"})],
    )

    claude.consume_assistant_message(session, message)

    assert session.updated_at != "2000-01-01T00:00:00+00:00"
    assert session.output_updated_at is None
    assert session.active_tool_uses()[0]["name"] == "Bash"
    assert session.active_tool_uses()[0]["detail"] == "command=ls"
    assert session.last_action == "Bash: command=ls"


def test_assistant_message_with_text_updates_both_activity_and_output() -> None:
    """テキストを持つassistantメッセージは活動時刻とテキスト出力時刻の双方を進める。"""
    session = state.SessionState("session-1", "/tmp")
    session.updated_at = "2000-01-01T00:00:00+00:00"
    message = types.SimpleNamespace(content=[types.SimpleNamespace(text="調査を続ける")])

    claude.consume_assistant_message(session, message)

    assert session.updated_at != "2000-01-01T00:00:00+00:00"
    assert session.output_updated_at is not None
    assert session.agent_message == "調査を続ける"
    assert session.last_action == "調査を続ける"


def test_only_assistant_messages_without_api_error_count_as_model_output() -> None:
    """API失敗の合成メッセージではモデル出力を観測済みにせず、通常のassistantメッセージで観測済みにする。"""
    session = state.SessionState("session-1", "/tmp")
    api_error = types.SimpleNamespace(
        content=[types.SimpleNamespace(text="API Error: 429 rate limit")],
        error="rate_limit",
    )

    claude.consume_assistant_message(session, api_error)
    after_error = session.model_output_observed
    claude.consume_assistant_message(session, types.SimpleNamespace(content=[], error=None))

    assert not after_error
    assert session.model_output_observed


def test_api_error_does_not_advance_activity() -> None:
    """API失敗の再試行はモデル活動でなく、正常メッセージで失敗記録が消える。"""
    session = state.SessionState("session-1", "/tmp")
    session.updated_at = "2000-01-01T00:00:00+00:00"
    failure = types.SimpleNamespace(
        content=[types.SimpleNamespace(text="API Error: 429 rate limit")],
        error="rate_limit",
    )

    claude.consume_assistant_message(session, failure)
    claude.consume_assistant_message(session, failure)

    assert session.updated_at == "2000-01-01T00:00:00+00:00"
    assert session.output_updated_at is None
    assert session.api_error is not None
    assert session.api_error["count"] == 2
    assert session.api_error["type"] == "rate_limit_error"
    assert session.api_error["http_status"] == 429

    claude.consume_assistant_message(session, types.SimpleNamespace(content=[], error="rate_limit"))
    assert session.api_error["http_status"] == 429
    assert session.api_error["count"] == 3

    claude.consume_assistant_message(session, types.SimpleNamespace(content=[], error=None))

    assert session.api_error is None
    assert session.updated_at != "2000-01-01T00:00:00+00:00"


def test_initialization_diagnostic_identifies_received_messages() -> None:
    """初期化診断は受信メッセージを種別だけでなく内容で識別できる形で保持する。"""
    diagnostic = claude._InitializationDiagnostic()  # pylint: disable=protected-access

    diagnostic.record_message(types.SimpleNamespace(subtype="SessionStart"))
    diagnostic.record_message(types.SimpleNamespace(subtype="PreToolUse"))

    public = diagnostic.public()
    assert public["received_messages"] == ["SimpleNamespace: SessionStart", "SimpleNamespace: PreToolUse"]
    assert public["received_message_types"] == {"SimpleNamespace": 2}
    assert "received_message_count" not in public
    assert not public["child_processes"]


def test_initialization_diagnostic_lists_child_processes() -> None:
    """初期化診断は委譲先プロセスが起動した子プロセスを列挙する。"""
    diagnostic = claude._InitializationDiagnostic()  # pylint: disable=protected-access
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]) as child:
        try:
            diagnostic.child_pid = os.getpid()
            children = diagnostic.public()["child_processes"]
        finally:
            child.terminate()
    assert any(entry.startswith(f"{child.pid}: ") for entry in children), children


@pytest.mark.asyncio
async def test_start_aborts_when_init_message_never_arrives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """initへ到達しないsessionを上限で打ち切り、子プロセスを終了させて例外で返す。"""
    monkeypatch.setattr(state, "SESSION_INITIALIZATION_TIMEOUT", 0.05)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    client = _SilentClient()
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)

    with pytest.raises(session_errors.SessionInitializationTimeoutError):
        await manager.start("調査する", str(tmp_path))

    assert not manager.sessions
    await manager.close()
    assert client.terminated is True


@pytest.mark.asyncio
async def test_auto_resume_deadline_is_independent_from_result_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    """自動再開の待機上限を終端結果の保持期限から独立して算出する。"""
    monkeypatch.setattr(resume_waits, "AUTO_RESUME_DEADLINE_SECONDS", 5.0)
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.0)
    session = state.SessionState("claude-session", "/tmp", engine="claude")
    before = asyncio.get_running_loop().time()

    deadline = resume_waits.begin_auto_resume_wait(
        session,
        {"status": "completed", "agent_message": "完了", "error": None},
    )

    assert before + 5.0 <= deadline <= asyncio.get_running_loop().time() + 5.0
    assert session.auto_resume_deadline == deadline
    assert session.awaiting_auto_resume is True


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_command_channel_resolves_pending_requests_when_closed() -> None:
    """チャネル閉鎖時に滞留要求を解決し、閉鎖後の受理を拒否する。"""
    channel = claude._CommandChannel()
    future = channel.send("prompt", "続行")

    channel.close()

    with pytest.raises(session_errors.SessionOwnerGoneError, match="session owner task has ended"):
        await asyncio.wait_for(future, timeout=0.1)
    with pytest.raises(session_errors.SessionOwnerGoneError, match="session owner task has ended"):
        channel.send("prompt", "閉鎖後").cancel()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_command_classification_uses_state_when_dequeued(tmp_path: pathlib.Path) -> None:
    """投入後に終端した継続入力をreplyとして処理し、直前結果を退避する。"""
    client = FakeClaudeClient([[AssistantMessage("reply中"), ResultMessage("reply結果")]])
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    session = state.SessionState("claude-race", str(tmp_path), engine="claude")
    channel = claude._CommandChannel()
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
            "status": "completed",
            "agent_message": "直前結果",
        },
    }
    assert iterator is not None
    assert client.queries == ["続行"]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_message_stream_failure_resolves_retrieved_command(tmp_path: pathlib.Path) -> None:
    """message stream例外と同じ待機バッチの継続要求を所有タスク終了で解決する。"""
    client = SynchronizedFailingClaudeClient()
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    try:
        session = await manager.start("調査", str(tmp_path))
        await asyncio.wait_for(client.message_waiting.wait(), timeout=0.1)
        channel = manager._channels[session.session_id]
        future = channel.send("prompt", "同時要求")
        client.release_message.set()

        with pytest.raises(session_errors.SessionOwnerGoneError, match="session owner task has ended"):
            await asyncio.wait_for(future, timeout=0.1)
    finally:
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_owner_cancellation_resolves_active_command(tmp_path: pathlib.Path) -> None:
    """所有タスク取り消し時に処理中の継続要求を所有タスク終了で解決する。"""
    client = BlockingContinuationClaudeClient()
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    session = await manager.start("調査", str(tmp_path))
    await asyncio.wait_for(client.message_waiting.wait(), timeout=0.1)
    send_task = asyncio.create_task(manager.send_message(session, "継続"))
    await asyncio.wait_for(client.query_started.wait(), timeout=0.1)

    await manager.close()

    with pytest.raises(session_errors.SessionOwnerGoneError, match="session owner task has ended"):
        await asyncio.wait_for(send_task, timeout=0.1)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_timed_out_queued_prompt_is_not_delivered(tmp_path: pathlib.Path) -> None:
    """打ち切り済みで未処理の継続要求を所有タスクが後から配送しない。"""
    client = FakeClaudeClient([])
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    session = state.SessionState("claude-queued", str(tmp_path), engine="claude")
    channel = claude._CommandChannel()
    manager.sessions[session.session_id] = session
    manager._channels[session.session_id] = channel

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(manager.send_message(session, "打ち切り"), timeout=0.01)

    command = await channel.get()
    await manager._handle_command(client, session, command, None)

    assert not client.queries


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.usefixtures("_owner_session_environment")
async def test_claude_options_use_claude_code_preset(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude Agent SDKへClaude Code presetと設定読込元、解決した所有セッションを渡す。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "owner-session")

    options = claude._build_options(str(tmp_path), "model", "high", root_session_id="owner-session")
    assert options.system_prompt == {
        "type": "preset",
        "preset": "claude_code",
        "append": f"{launch_prompts.CLAUDE_DELEGATE_SYSTEM_PROMPT}\n{launch_prompts.AUTO_RESUME_NOTICE}",
    }
    assert options.setting_sources == ["user", "project"]
    assert options.permission_mode == "auto"
    assert options.env == {
        "AGENT_TOOLKIT_DELEGATED_SESSION": "1",
        "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1",
        "AGENT_TOOLKIT_OWNER_SESSION": "owner-session",
        "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h",
    }


@pytest.mark.usefixtures("agents_server_isolation")
def test_claude_options_accept_saved_session_id(tmp_path: pathlib.Path) -> None:
    """Claude SDK optionsへ保存済みsession IDをresumeとして渡す。"""
    options = claude._build_options(str(tmp_path), "model", "high", "claude-saved")
    assert options.resume == "claude-saved"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.usefixtures("_owner_session_environment")
def test_claude_explore_options_reduce_instruction_sources_and_keep_tools(tmp_path: pathlib.Path) -> None:
    """Claude探索起動はユーザー設定のhookを読み、探索用toolと指示を明示する。

    所有セッションを解決できない環境ではそのキーを設定しない。
    """
    options = claude._build_options(str(tmp_path), "model", "high", launch_kind="explore")
    assert options.setting_sources == ["user"]
    assert options.skills == []
    assert options.env == {
        "AGENT_TOOLKIT_DELEGATED_SESSION": "1",
        "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_PROMPT_CACHE_TTL": "5m",
    }
    assert options.system_prompt == f"{launch_prompts.EXPLORE_SYSTEM_PROMPT}\n{launch_prompts.AUTO_RESUME_NOTICE}"
    assert options.tools == {"type": "preset", "preset": "claude_code"}
    assert set(options.allowed_tools) == {"Read", "Glob", "Grep", "Bash", "WebSearch", "WebFetch"}


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.usefixtures("_owner_session_environment")
def test_claude_write_options_limit_lightweight_session_to_file_edits(tmp_path: pathlib.Path) -> None:
    """Claude軽量書込は汎用コマンドを許可せず、固定プロンプトと編集toolだけを使う。"""
    options = claude._build_options(str(tmp_path), "model", "high", launch_kind="write")
    assert options.setting_sources == ["user"]
    assert options.skills == []
    assert options.system_prompt == f"{launch_prompts.WRITE_SYSTEM_PROMPT}\n{launch_prompts.AUTO_RESUME_NOTICE}"
    assert set(options.allowed_tools) == {"Read", "Glob", "Grep", "Write", "Edit"}


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.usefixtures("_owner_session_environment")
def test_claude_shell_options_share_lightweight_launch_with_command_tools(tmp_path: pathlib.Path) -> None:
    """Claudeのシェル実行起動は探索と同じ軽量条件を共有し、実行用toolと指示を選ぶ。"""
    options = claude._build_options(str(tmp_path), "model", "high", launch_kind="shell")
    assert options.setting_sources == ["user"]
    assert options.skills == []
    assert options.env == {
        "AGENT_TOOLKIT_DELEGATED_SESSION": "1",
        "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_PROMPT_CACHE_TTL": "5m",
    }
    assert options.system_prompt == f"{launch_prompts.SHELL_SYSTEM_PROMPT}\n{launch_prompts.AUTO_RESUME_NOTICE}"
    assert set(options.allowed_tools) == {"Bash", "Read"}


@pytest.mark.usefixtures("agents_server_isolation")
def test_claude_dependency_check_builds_options_without_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """依存の確認処理は現在の作業ディレクトリでoptionsだけを構築する。"""
    calls: list[tuple[str, str | None, str | None]] = []

    def fake_build_options(cwd: str, model: str | None, effort: str | None) -> object:
        calls.append((cwd, model, effort))
        return object()

    monkeypatch.setattr(claude, "_build_options", fake_build_options)
    claude.check_dependencies()

    assert calls == [(str(pathlib.Path.cwd()), None, None)]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_start_result_wait_and_reply(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Claude init・結果受信・終端後replyを1長命タスクで処理する。"""
    client = FakeClaudeClient(
        [
            [SystemMessage("claude-session"), AssistantMessage("途中経過"), ResultMessage("Claude結果")],
            [SystemMessage("claude-session"), AssistantMessage("reply中"), ResultMessage("reply結果")],
        ]
    )
    options = SimpleNamespace(system_prompt={"type": "preset", "preset": "claude_code"})
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: options)
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


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_resources_remain_identical_when_init_is_resent_per_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """initの再送時も所有タスクのキューと状態を再生成しない。"""
    client = FakeClaudeClient(
        [
            [SystemMessage("claude-stable"), ResultMessage("初回結果")],
            [SystemMessage("claude-stable"), ResultMessage("1回目のreply結果")],
            [SystemMessage("claude-stable"), ResultMessage("2回目のreply結果")],
        ]
    )
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    try:
        session = await manager.start("調査", str(tmp_path))
        channel = manager._channels[session.session_id]
        for prompt, expected in (("続行1", "1回目のreply結果"), ("続行2", "2回目のreply結果")):
            for _ in range(200):
                if session.result_available:
                    break
                await asyncio.sleep(0.01)
            assert session.result_available is True
            reply = await asyncio.wait_for(manager.send_message(session, prompt), timeout=5)
            assert reply["delivery"] == "reply_started"
            for _ in range(200):
                if session.agent_message == expected:
                    break
                await asyncio.sleep(0.01)
            assert session.agent_message == expected
            assert manager._channels[session.session_id] is channel
            assert manager.sessions[session.session_id] is session
    finally:
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_kill_uses_owner_task_interrupt_and_maps_terminal_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Claudeのkillは所有タスクからinterruptを呼び、中断理由を状態へ写像する。"""
    client = InterruptAwareClaudeClient()
    manager = server_manager.AgentsServerManager()
    backend = claude.ClaudeServerManager(manager.sessions, manager._condition, client_factory=lambda _options: client)
    install_backend(manager, "claude", backend)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())

    try:
        monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: [("claude", "model", "high")])
        start_response = await manager.start("plan", "調査", str(tmp_path))
        response = await manager.kill(start_response["session_id"], timeout=1)
        assert response["status"] == "interrupted"
        assert response["kill_requested"] is True
        assert response["agent_message"] == "中断結果"
        assert client.interrupts == 1
    finally:
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_message_gap_does_not_cancel_stream(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """通常のメッセージ間隔が旧poll間隔を超えてもstreamを失敗扱いにしない。"""
    client = DelayedClaudeClient([[SystemMessage("claude-delayed"), AssistantMessage("途中"), ResultMessage("完了")]])
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
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


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_start_returns_on_message_start(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """APIの応答開始を受信した時点で、完成したメッセージを待たずに`start`の可用性待機を終える。"""
    response, elapsed, observed = await _start_claude_until_available(
        monkeypatch, tmp_path, [SystemMessage("claude-streaming"), StreamEvent("message_start")], 5.0
    )

    assert response["session_id"] == "claude-streaming"
    assert elapsed < 2.0
    assert observed


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_start_ignores_non_start_stream_events(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """応答開始以外の部分出力とAPI失敗のメッセージでは、可用性待機を上限まで続ける。"""
    messages = [
        SystemMessage("claude-unstarted"),
        StreamEvent("content_block_delta"),
        ErrorAssistantMessage("API Error: 429 rate limit"),
    ]
    response, elapsed, observed = await _start_claude_until_available(monkeypatch, tmp_path, messages, 0.3)

    assert response["session_id"] == "claude-unstarted"
    assert elapsed >= 0.3
    assert not observed


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_concatenates_multiple_text_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Claudeの複数TextBlockを改行区切りで連結して進捗へ保持する。"""
    assistant_message = AssistantMessage("一")
    assistant_message.content = MultipleBlockAssistantMessage("一", "二").content
    client = FakeClaudeClient([[SystemMessage("claude-blocks"), assistant_message, ResultMessage("完了")]])
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    try:
        session = await manager.start("調査", str(tmp_path))
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert session.progress == "一 二"
    finally:
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_task_exception_disconnects_and_retains_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """message task例外時に切断し、failed結果を共有sessionへ保持する。"""
    client = FailingClaudeClient([])
    sessions: dict[str, state.SessionState] = {}
    manager = claude.ClaudeServerManager(sessions, client_factory=lambda _options: client)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    session = await manager.start("調査", str(tmp_path))
    for _ in range(20):
        if client.disconnected:
            break
        await asyncio.sleep(0.01)
    assert client.disconnected is True
    assert sessions[session.session_id] is session
    assert session.status == "failed"
    # 初期化の完了後の失敗は、原因の特定に要する診断を終端結果へ添える。
    assert session.error["message"] == "stream failed"
    assert set(session.error) == {"message", "engine", "model", "stderr", "childPid", "debugFile"}
    assert session.error["engine"] == "claude"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_failure_keeps_last_nonempty_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """空の後続本文と失敗結果は最後の非空assistant本文を失わない。"""
    result = ResultMessage("")
    result.is_error = True
    client = FakeClaudeClient([[SystemMessage("claude-failed"), AssistantMessage("途中経過"), AssistantMessage(""), result]])
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    try:
        session = await manager.start("調査", str(tmp_path))
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert session.status == "failed"
        assert session.agent_message == "途中経過"
        assert session.progress == "途中経過"
    finally:
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_empty_result_is_not_completed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """非空assistant本文を伴わないResultMessageは正常完了にしない。"""
    client = FakeClaudeClient([[SystemMessage("claude-empty"), AssistantMessage(""), ResultMessage("")]])
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    try:
        session = await manager.start("調査", str(tmp_path))
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert session.status == "failed"
        assert session.error == {"message": "Claude Agent SDK returned no assistant output"}
    finally:
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_retention_expiry_disconnects_and_retains_result_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保持期限経過時にSDKを切断し、未回収結果を伴う再開状態を退避する。"""
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.01)
    client = FakeClaudeClient([[SystemMessage("claude-expired"), ResultMessage("完了")]])
    sessions: dict[str, state.SessionState] = {}
    manager = server_manager.AgentsServerManager()
    manager.sessions = sessions
    backend = claude.ClaudeServerManager(
        sessions,
        manager._condition,
        client_factory=lambda _options: client,
        expire_session=manager._expire_session,
    )
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
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
            label=session.label,
            created_at=session.created_at,
            started_at=session.started_at,
            updated_at=session.updated_at,
            turn_seq=session.turn_seq,
            status="completed",
            agent_message="完了",
            finalized_at=session.finalized_at,
            result_delivered=False,
            retention_deadline=session.retention_deadline,
        )
    }
    response = await manager.wait()
    assert response == {"session_id": session.session_id, "status": "completed", "agent_message": "完了"}
    assert await manager.wait() == {"status": "expired"}


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_server_close_disconnects_and_retains_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """サーバー終了時にSDKを切断し、取得済み結果recordは保持する。"""
    client = FakeClaudeClient([[SystemMessage("claude-close"), ResultMessage("完了")]])
    sessions: dict[str, state.SessionState] = {}
    backend = claude.ClaudeServerManager(sessions, client_factory=lambda _options: client)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    session = await backend.start("調査", str(tmp_path))
    for _ in range(20):
        if session.result_available:
            break
        await asyncio.sleep(0.01)
    await backend.close()
    assert client.disconnected is True
    assert sessions[session.session_id].agent_message == "完了"


async def _finished_failed_claude_session(
    tmp_path: pathlib.Path,
) -> tuple[server_manager.AgentsServerManager, claude.ClaudeServerManager, state.SessionState]:
    """所有タスクが失敗で終わったClaude sessionと、そのbackendを保持するmanagerを返す。

    同じsessionへの次のturnは、失敗しないクライアントで始まる。
    """
    manager = server_manager.AgentsServerManager()
    clients = [
        FailingClaudeClient([]),
        FakeClaudeClient([[SystemMessage("claude-failed")]]),
    ]

    def client_factory(_options: Any) -> FakeClaudeClient:
        return clients.pop(0)

    backend = claude.ClaudeServerManager(
        manager.sessions,
        manager._condition,
        client_factory=client_factory,
        expire_session=manager._expire_session,
    )
    install_backend(manager, "claude", backend)
    session = await backend.start("調査", str(tmp_path))
    for _ in range(20):
        if session.result_available and session.session_id not in backend._channels:
            break
        await asyncio.sleep(0.01)
    return manager, backend, session


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_finished_task_send_message_omits_previous_result_after_wait(tmp_path: pathlib.Path) -> None:
    """所有タスク終了後もwaitで回収済みの結果本文を再送しない。"""
    manager, backend, session = await _finished_failed_claude_session(tmp_path)

    result = await manager.wait()
    assert result["error"]["message"] == "stream failed"
    assert result["error"]["engine"] == "claude"
    response = await manager.send_message(session.session_id, "続行")

    assert _without_root(response) == {"delivery": "reply_started", "label": ""}
    assert not manager.expired_sessions
    assert manager.sessions[session.session_id].status == "running"
    await backend.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_finished_task_send_message_keeps_previous_result_without_wait(tmp_path: pathlib.Path) -> None:
    """所有タスク終了後も未回収の結果本文を退避して会話を再開する。"""
    manager, backend, session = await _finished_failed_claude_session(tmp_path)

    response = await manager.send_message(session.session_id, "続行")

    previous_error = response["previous_result"].pop("error")
    assert previous_error["message"] == "stream failed"
    assert previous_error["engine"] == "claude"
    assert _without_root(response) == {
        "delivery": "reply_started",
        "label": "",
        "previous_result": {
            "status": "failed",
            "agent_message": "",
        },
    }
    assert not manager.expired_sessions
    assert manager.sessions[session.session_id].status == "running"
    await backend.close()
