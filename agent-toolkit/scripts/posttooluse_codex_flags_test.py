"""codex-exec経路とexecutor状態のPostToolUseテスト。"""

import json
import os
import pathlib
import subprocess

import _fork_runner
import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook.py"


def _run(payload: dict, *, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """PostToolUse hookを実行して結果を返す。"""
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    result = _fork_runner.run_script(
        _SCRIPT,
        argv=("posttooluse",),
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return result


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    """テスト用のセッション状態を読み込む。"""
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("skill_name", ["codex-exec", "agent-toolkit:codex-exec"])
def test_codex_exec_skill_invocation_is_recorded(tmp_path: pathlib.Path, skill_name: str) -> None:
    """codex-execの短縮名と完全修飾名を同じフラグへ記録する。"""
    sid = skill_name.replace(":", "-")
    _run(
        {"session_id": sid, "tool_name": "Skill", "tool_input": {"skill": skill_name}},
        state_dir=tmp_path,
    )
    assert _read_state(tmp_path, sid)["codex_exec_skill_invoked"] is True


def test_removed_agent_does_not_change_state(tmp_path: pathlib.Path) -> None:
    """廃止したエージェント名を受け取っても旧状態を作成しない。"""
    sid = "removed"
    removed_agent = "-".join(("plan", "reviewer"))
    removed_flag = "_".join(("plan", "reviewer", "invoked"))
    _run(
        {
            "session_id": sid,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": removed_agent},
            "tool_response": {"result": "status: completed\nreview_completed: true"},
        },
        state_dir=tmp_path,
    )
    state = _read_state(tmp_path, sid)
    assert removed_flag not in state
