"""UserPromptSubmitとPostToolUseを経由して自動名の保存・手動名の保持を確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import posttooluse, user_prompt_submit


@pytest.mark.parametrize(
    "skill,arguments,kind",
    [
        ("process-wi", "", "process-wi"),
        ("process-wi", "single-lane 追記", "single-lane"),
        ("process-wi", "all-repos", "all-repos"),
        ("process-wi", "single-lanex", "process-wi"),
        ("add-awi-by-user", "", "add-awi"),
    ],
)
@pytest.mark.parametrize("manual", [False, True])
def test_command_hooks_name_session(
    skill: str,
    arguments: str,
    kind: str,
    manual: bool,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """機能を省くFunctionイベントを使わず、種別・計画名と手動名を引き継ぐ。"""
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    session = "title-test"
    session_state.update_state(session, lambda state: {**state, "current_plan_file_path": "C:\\plans\\18-1921_画像整理.md"})
    if not manual:
        posttooluse.main(
            json.dumps(
                {
                    "session_id": session,
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Skill",
                    "tool_input": {"skill": f"agent-toolkit:{skill}", "args": arguments},
                }
            )
        )
        capsys.readouterr()
    # 一時ディレクトリが変わる再開でも種別を保持する。
    (tmp_path / "after-restart").mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "after-restart"))
    payload = {"session_id": session, "prompt": f"/agent-toolkit:{skill} {arguments}" if manual else "続ける"}
    user_prompt_submit.main(json.dumps(payload))
    title = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["sessionTitle"]
    assert title == f"{kind} 画像整理"
    user_prompt_submit.main(json.dumps({**payload, "session_title": "手動の名前"}))
    output = capsys.readouterr().out
    assert not output or "sessionTitle" not in json.loads(output)["hookSpecificOutput"]
    user_prompt_submit.main(json.dumps({**payload, "session_title": title}))
    output = capsys.readouterr().out
    assert not output or "sessionTitle" not in json.loads(output)["hookSpecificOutput"]


@pytest.mark.parametrize("loop", [False, True])
def test_plan_writes_update_automatic_title(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], loop: bool
) -> None:
    """Skillと計画Writeの公開hookを順に通し、自動名の更新と手動名の保持を確かめる。"""
    session = "plan-title"
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", raising=False)
    if loop:
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", session)
    posttooluse.main(
        json.dumps({"session_id": session, "tool_name": "Skill", "tool_input": {"skill": "agent-toolkit:add-awi-by-user"}})
    )
    capsys.readouterr()
    plans = pathlib.Path.home() / ".claude/plans"
    plans.mkdir(parents=True, exist_ok=True)
    current = ""
    for name in ("21-1234_最初の計画.md", "21-1235_更新した計画.md"):
        plan = plans / name
        plan.write_text("# 計画\n", encoding="utf-8")
        posttooluse.main(
            json.dumps(
                {
                    "session_id": session,
                    "cwd": str(tmp_path),
                    "tool_name": "Write",
                    "tool_input": {"file_path": str(plan), "content": "# 計画\n"},
                }
            )
        )
        capsys.readouterr()
        user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "続ける", "session_title": current}))
        current = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["sessionTitle"]
        assert current == ("process-wi" if loop else "add-awi") + " " + name.split("_", 1)[1].removesuffix(".md")
    user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "続ける", "session_title": "手動名"}))
    output = capsys.readouterr().out
    assert not output or "sessionTitle" not in json.loads(output)["hookSpecificOutput"]
