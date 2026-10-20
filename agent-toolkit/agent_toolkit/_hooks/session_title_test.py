"""UserPromptSubmitとPostToolUseを経由して自動名の保存・手動名の保持を確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import posttooluse, user_prompt_submit


@pytest.mark.parametrize(
    "skill,kind", [("process-wi", "process-wi"), ("single-lane-process", "single-lane"), ("add-awi-by-user", "add-awi")]
)
@pytest.mark.parametrize("manual", [False, True])
def test_command_hooks_name_session(
    skill: str,
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
                    "tool_input": {"skill": f"agent-toolkit:{skill}"},
                }
            )
        )
        capsys.readouterr()
    # 一時ディレクトリが変わる再開でも種別を保持する。
    (tmp_path / "after-restart").mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path / "after-restart"))
    payload = {"session_id": session, "prompt": f"/agent-toolkit:{skill}" if manual else "続ける"}
    user_prompt_submit.main(json.dumps(payload))
    title = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["sessionTitle"]
    assert title == f"{kind} 画像整理"
    user_prompt_submit.main(json.dumps({**payload, "session_title": "手動の名前"}))
    output = capsys.readouterr().out
    assert not output or "sessionTitle" not in json.loads(output)["hookSpecificOutput"]
    user_prompt_submit.main(json.dumps({**payload, "session_title": title}))
    output = capsys.readouterr().out
    assert not output or "sessionTitle" not in json.loads(output)["hookSpecificOutput"]
