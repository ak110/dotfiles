"""agents_serverの実backendによる背景作業完了後の自動再開を検証する。"""

import os
import pathlib
from collections.abc import Callable

import pytest

import agent_toolkit.agents_server_mcp as subject
from agent_toolkit._atk import config as _atk_config

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_TOOLKIT_LIVE_AGENTS_TEST") != "1",
    reason="実際のagents_serverを起動するテストは明示指定時だけ実行する",
)

_PROMPT = """Bashツールで`sleep 2`を背景実行し、待たずにturnを終えよ。
背景作業の完了通知で自動的に再開したturnでは、最終応答を`AUTO_RESUME_COMPLETED`だけにせよ。"""


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["delegate", "explore", "shell"])
async def test_live_launch_waits_for_automatic_resume(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """公開する起動ツール`start`の3つのmodeが、再開指示なしで背景作業完了後の結果を返す。"""
    manager = subject.AgentsServerManager()
    monkeypatch.setattr(subject, "_MANAGER", manager)
    cwd = str(pathlib.Path(__file__).parents[2])
    host = host_environ()
    for name in (
        "HOME",
        "USERPROFILE",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
    ):
        if name in host:
            monkeypatch.setenv(name, host[name])
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda _model_type: [("claude", "sonnet[1m]", "medium")],
    )
    try:
        if mode == "delegate":
            await subject.start(cwd, mode="delegate", prompt=_PROMPT, model_type="low_tier")
        elif mode == "explore":
            await subject.start(cwd, mode="explore", prompt=_PROMPT)
        else:
            await subject.start(
                cwd,
                mode="shell",
                command="sleep 2",
                summary_policy="Bashツールを背景実行し、完了通知で再開した後に`AUTO_RESUME_COMPLETED`だけを返す。",
            )

        result = await manager.wait()

        assert result["status"] == "completed", result
        assert result["agent_message"].strip() == "AUTO_RESUME_COMPLETED"
    finally:
        await manager.close()
