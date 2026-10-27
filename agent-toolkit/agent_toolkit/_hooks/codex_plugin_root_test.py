"""開始・再開時の資料rootを、異配置hookの案内と取得記録まで接続する。"""

import json
import pathlib
import shlex
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import codex, plugin_root, state
from agent_toolkit._common import delegated_session, read_triggers
from agent_toolkit._hooks import notice, plugin_resources, posttooluse, rules_context
from agent_toolkit._hooks.pretooluse import dispatch, read_delivery
from agent_toolkit._testing.agents_server_support import FakeCodexClient


@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_start_resume_root_reaches_foreign_hook_and_acquisition(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """同名の異なる資料を持つhook配置でも、起動側が選んだ実体だけを案内・記録する。"""
    selected = tmp_path / "selected/agent-toolkit"
    skill = selected / "skills/sample/SKILL.md"
    skill.parent.mkdir(parents=True)
    (selected / ".claude-plugin").mkdir()
    (selected / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    skill.write_text(
        "# 技能\n| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        "| 判断前 | `reference.md` | 操作 | `tool:Judge` |\n",
        encoding="utf-8",
    )
    reference = skill.parent / "reference.md"
    reference.write_text("# 選択版の資料\n選択版本文\n", encoding="utf-8")
    foreign = tmp_path / "foreign/agent-toolkit"
    alternate = foreign / "skills/sample/reference.md"
    alternate.parent.mkdir(parents=True)
    alternate.write_text("# 別配置の資料\n異版本文\n", encoding="utf-8")
    (alternate.parent / "SKILL.md").write_text(skill.read_text(encoding="utf-8"), encoding="utf-8")
    (foreign / ".claude-plugin").mkdir()
    (foreign / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    monkeypatch.setattr(plugin_resources, "_PLUGIN_ROOT", foreign)
    monkeypatch.setattr(plugin_root, "SERVER_PLUGIN_ROOT", selected)
    monkeypatch.setattr(read_triggers, "document_sources", lambda root, _cwd: sorted((root / "skills").rglob("*.md")))
    client = FakeCodexClient()
    async with codex.AppServerManager(root_session_id="owner") as manager:
        monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
        await manager.start("開始", str(tmp_path))
        await manager.resume("thread-codex", state.ResumePrompt("再開"), str(tmp_path))
        requests = [params for method, params in client.requests if method in {"thread/start", "thread/resume"}]
        assert len(requests) == 2
        for index, params in enumerate(requests):
            environment = params["config"]["mcp_servers"]["agents_server"]["env"]
            monkeypatch.setenv(delegated_session.PLUGIN_ROOT_ENV, environment[delegated_session.PLUGIN_ROOT_ENV])
            assert plugin_resources.plugin_root() == selected
            for source in ("startup", "compact"):
                with notice.notice_cycle(f"root-{index}-{source}", "session-start", "main"):
                    context = rules_context.compose_session_start(source, delegated=True, host="codex")
                assert isinstance(context, str)
                context_text = str(context)
                assert str(selected / "scripts/atk") in context_text
                assert str(foreign) not in context_text
            session_id = "selected-root"
            payload = {
                "session_id": session_id,
                "turn_id": "turn",
                "cwd": str(tmp_path),
                "tool_name": "Judge",
                "tool_input": {},
            }
            assert dispatch.main(json.dumps(payload)) == 0
            first = capsys.readouterr()
            assert str(reference) in first.out and str(foreign) not in first.out
            read = {**payload, "tool_name": "Bash", "tool_input": {"command": "cat -- " + shlex.quote(str(reference))}}
            assert dispatch.main(json.dumps(read)) == 0
            capsys.readouterr()
            assert (
                posttooluse.main(
                    json.dumps(
                        {**read, "hook_event_name": "PostToolUse", "tool_response": reference.read_text(encoding="utf-8")}
                    )
                )
                == 0
            )
            capsys.readouterr()
            records = read_delivery.read_state(session_id)[rules_context.OPERATION_SKILL_READY_KEY]
            assert read_delivery.target_ready(records, read_delivery.read_triggers.ReadTarget(reference), "main")
            assert dispatch.main(json.dumps(payload)) == 0
            assert str(reference) not in capsys.readouterr().out
            # 次の開始・再開条件でも取得前から確認する。
            read_delivery.update_state(session_id, lambda _state: {})
