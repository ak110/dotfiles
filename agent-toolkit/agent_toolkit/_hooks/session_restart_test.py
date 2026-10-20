"""tempdir変更後のPreToolUseが起動済みスキルと委譲先の状態を引き継ぐことを確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import posttooluse, pretooluse


@pytest.mark.parametrize("operation", ["send_message", "kill"])
@pytest.mark.parametrize("legacy", [False, True])
def test_restart_keeps_delegation_state(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: str, legacy: bool
) -> None:
    """実際のSkillとstart応答を供給し、変更後の一時領域から追送・停止を許可する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(before))
    base = {"session_id": "restart-test", "cwd": str(tmp_path), "hook_event_name": "PostToolUse"}
    posttooluse.main(json.dumps({**base, "tool_name": "Skill", "tool_input": {"skill": "agent-toolkit:delegation"}}))
    posttooluse.main(
        json.dumps(
            {
                **base,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"mode": "explore", "prompt": "調査する", "cwd": str(tmp_path)},
                "tool_response": {"structuredContent": {"session_id": "child", "status": "running"}},
            }
        )
    )
    capsys.readouterr()
    if legacy:
        # 旧版が同じJSONをtempdirへ保存した状態から、hook自身の移行と所有確認を通す。
        current = session_state.state_path("restart-test")
        old = before / current.name
        old.write_text(current.read_text(encoding="utf-8"), encoding="utf-8")
        current.unlink()
        assert (
            pretooluse.main(
                json.dumps(
                    {
                        **base,
                        "hook_event_name": "PreToolUse",
                        "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                        "tool_input": {"session_id": "child", "prompt": "続行する"},
                    }
                )
            )
            == 0
        )
        assert not old.exists()
        assert current.exists()
        capsys.readouterr()
    monkeypatch.setattr("tempfile.tempdir", str(after))
    assert (
        pretooluse.main(
            json.dumps(
                {
                    **base,
                    "hook_event_name": "PreToolUse",
                    "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                    "tool_input": {"session_id": "child", "prompt": "続行する"},
                }
            )
        )
        == 0
    )
    result = capsys.readouterr()
    assert not result.err
