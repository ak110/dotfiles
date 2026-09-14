"""Claude backendと共有する自動再開状態の契約を検証する。"""

import asyncio
import os
import pathlib
import subprocess
import sys
import types
from collections.abc import AsyncIterator
from typing import Any

import pytest

from agent_toolkit._agents_server import claude
from agent_toolkit._agents_server import state as shared_state


class _SilentClient:
    """接続を保ったままinitメッセージを送らないSDKクライアントの検体。

    切断でCLIの子プロセスを終了する実クライアントの契約を模す。
    実クライアントは取り消された実行での切断で当該終了処理へ到達しないため、
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
    monkeypatch.setattr(claude._plan_file, "resolve_owner_session_id", lambda: None)  # pylint: disable=protected-access

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
    monkeypatch.setattr(claude._plan_file, "resolve_owner_session_id", lambda: None)  # pylint: disable=protected-access
    return captured


@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
def test_build_options_keeps_every_launch_out_of_bypass_modes(
    monkeypatch: pytest.MonkeyPatch,
    launch_kind: str,
) -> None:
    """起動区分によらずbypass系以外の権限モードで起動する。

    bypass系の受信側はセッション間メッセージを保留するため、起動したセッションが初期化を完了できない。
    """
    captured = _capture_options(monkeypatch)

    claude._build_options("/tmp", "model", "medium", launch_kind=launch_kind)  # type: ignore[arg-type]  # pylint: disable=protected-access

    assert captured["permission_mode"] == "auto"


def test_build_options_passes_debug_file_to_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """診断記録の保存先を委譲先CLIの引数として渡す。"""
    captured = _capture_options(monkeypatch)
    debug_file = tmp_path / "delegate.log"

    claude._build_options("/tmp", "model", "medium", debug_file=debug_file)  # pylint: disable=protected-access

    assert captured["extra_args"] == {"debug-file": str(debug_file)}


def test_build_options_omits_debug_file_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """保存先を指定しない起動では診断記録の引数を渡さない。"""
    captured = _capture_options(monkeypatch)

    claude._build_options("/tmp", "model", "medium")  # pylint: disable=protected-access

    assert "extra_args" not in captured


def test_prepare_debug_file_drops_records_beyond_retention(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保持世代を超えた古い診断記録を自動的に削除する。"""
    monkeypatch.setattr(claude.logging_config, "state_dir", lambda: tmp_path)
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


def test_initialization_diagnostic_identifies_received_messages() -> None:
    """初期化診断は受信メッセージを種別だけでなく内容で識別できる形で保持する。"""
    diagnostic = claude._InitializationDiagnostic()  # pylint: disable=protected-access

    diagnostic.record_message(types.SimpleNamespace(subtype="SessionStart"))
    diagnostic.record_message(types.SimpleNamespace(subtype="PreToolUse"))

    public = diagnostic.public()
    assert public["received_messages"] == ["SimpleNamespace: SessionStart", "SimpleNamespace: PreToolUse"]
    assert public["received_message_count"] == 2
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
    monkeypatch.setattr(shared_state, "SESSION_INITIALIZATION_TIMEOUT", 0.05)
    monkeypatch.setattr(claude.logging_config, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(claude._plan_file, "resolve_owner_session_id", lambda: None)  # pylint: disable=protected-access
    client = _SilentClient()
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)

    with pytest.raises(shared_state.SessionInitializationTimeoutError):
        await manager.start("調査する", str(tmp_path))

    assert not manager.sessions
    await manager.close()
    assert client.terminated is True


@pytest.mark.asyncio
async def test_auto_resume_deadline_is_independent_from_result_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    """自動再開の待機上限を終端結果の保持期限から独立して算出する。"""
    monkeypatch.setattr(shared_state, "AUTO_RESUME_DEADLINE_SECONDS", 5.0)
    monkeypatch.setattr(shared_state, "RESULT_RETENTION_SECONDS", 0.0)
    session = shared_state.SessionState("claude-session", "/tmp", engine="claude")
    before = asyncio.get_running_loop().time()

    deadline = shared_state.begin_auto_resume_wait(
        session,
        {"status": "completed", "agent_message": "完了", "error": None},
    )

    assert before + 5.0 <= deadline <= asyncio.get_running_loop().time() + 5.0
    assert session.auto_resume_deadline == deadline
    assert session.awaiting_auto_resume is True
