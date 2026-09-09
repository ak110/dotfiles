"""Claude backendと共有する自動再開状態の契約を検証する。"""

import asyncio
import sys
import types

import pytest

from agent_toolkit._agents_server import claude
from agent_toolkit._agents_server import state as shared_state


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
