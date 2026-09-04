"""agents_serverの実backendによる背景作業完了後の自動再開を検証する。"""

import os
import pathlib
from collections.abc import Callable

import _atk_config
import agents_server_mcp as subject
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_TOOLKIT_LIVE_AGENTS_TEST") != "1",
    reason="ライブのagents_server検査は明示指定時だけ実行する",
)

_PROMPT = """Bashツールで`sleep 2`を背景実行し、待たずにturnを終えよ。
背景作業の完了通知で自動的に再開したturnでは、最終応答を`AUTO_RESUME_COMPLETED`だけにせよ。"""


@pytest.mark.asyncio
@pytest.mark.parametrize("launch_kind", ["start", "start_explore", "start_shell"])
async def test_live_launch_waits_for_automatic_resume(
    launch_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """3つの公開起動経路が再開指示なしで背景作業完了後の結果を返す。"""
    manager = subject.AgentsServerManager()
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
        "resolve_model_candidates",
        lambda _model_type: [("claude", "sonnet[1m]", "medium")],
    )
    try:
        if launch_kind == "start":
            started = await manager.start("explore_fast", _PROMPT, cwd)
        elif launch_kind == "start_explore":
            started = await manager.start_explore(True, _PROMPT, cwd)
        else:
            started = await manager.start_shell(
                "sleep 2",
                cwd,
                "Bashツールを背景実行し、完了通知で再開した後に`AUTO_RESUME_COMPLETED`だけを返す。",
            )

        result = await manager.wait(started["session_id"], timeout=180)

        assert result["status"] == "completed", result
        assert result["agent_message"].strip() == "AUTO_RESUME_COMPLETED"
    finally:
        await manager.close()
