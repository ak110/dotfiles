"""公開された計画ファイル作成コマンドのPostToolUse記録を検証する。"""

import json
import pathlib

from agent_toolkit._common import session_state
from agent_toolkit._hooks import posttooluse


def test_run_script_plan_create_records_current_plan_file_path(
    monkeypatch,
    tmp_path: pathlib.Path,
) -> None:
    """`atk run-script plan-create`の標準出力から現在の計画パスを記録し、計画の工程の開始とする。

    前の計画のパスは新しい計画のパスで置き換え、セッション名へ前の計画名を出し続けない。
    """
    plan_path = tmp_path / ".claude" / "plans" / "15-0713_process-wi_レーン01.md"
    plan_path.parent.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    sid = "plan-path-run-script"
    previous = tmp_path / ".claude" / "plans" / "14-0900_前の計画.md"
    session_state.update_state(sid, lambda state: {**state, "current_plan_file_path": str(previous)})
    assert (
        posttooluse.main(
            json.dumps(
                {
                    "session_id": sid,
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "atk run-script plan-create -- --main-source /tmp/main.md --lane lane-01"},
                    "tool_response": {"stdout": f"{plan_path}\n", "stderr": "", "exit_code": 0},
                }
            )
        )
        == 0
    )

    state = session_state.read_state(sid)
    assert state["current_plan_file_path"] == str(plan_path)
    assert state["plan_mode_skill_invoked"] is True
