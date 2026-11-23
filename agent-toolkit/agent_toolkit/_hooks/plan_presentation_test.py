import json
import subprocess

from agent_toolkit._common.session_state import update_state
from agent_toolkit._hooks import plan_presentation
from agent_toolkit._hooks.pretooluse import dispatch


def test_exit_plan_mode_checks_current_known_plan_once_per_version(tmp_path, monkeypatch, capsys):
    path = tmp_path / "plan.md"
    path.write_text("未検査の本文\n", encoding="utf-8")
    sid = "present-plan-test"
    update_state(sid, lambda state: {**state, "current_plan_file_path": str(path), "plan_mode_skill_invoked": True})
    monkeypatch.setattr(plan_presentation, "is_plan_main_file", lambda name: name == str(path))
    calls = []
    original = subprocess.run

    def run(argv, **_kwargs):
        if "plan-check" not in argv:
            return original(argv, **_kwargs)
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if len(calls) == 1 else 0, "", "検査の診断")

    monkeypatch.setattr(plan_presentation.subprocess, "run", run)

    def invoke(name):
        payload = {"session_id": sid, "cwd": str(tmp_path), "tool_name": name, "tool_input": {}}
        assert dispatch.main(json.dumps(payload)) == 0
        return capsys.readouterr().out

    invoke("Read")
    assert not calls
    assert "計画の構造検査が終了1" in invoke("ExitPlanMode")
    assert calls[0][-3:] == [str(path), "--work-dir", str(tmp_path)]
    path.write_text("是正した本文\n", encoding="utf-8")
    assert "計画の構造検査" not in invoke("ExitPlanMode")
    assert "計画の構造検査" not in invoke("ExitPlanMode")
    assert len(calls) == 2
    path.write_text("見直した本文\n", encoding="utf-8")
    assert "計画の構造検査" not in invoke("ExitPlanMode")
    assert len(calls) == 3
