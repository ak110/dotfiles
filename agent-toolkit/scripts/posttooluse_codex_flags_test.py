"""codex-exec経路と計画レビュー完了状態のPostToolUseテスト。"""

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


@pytest.mark.parametrize("tool_name", ["Agent", "Task"])
def test_plan_review_completed_from_strict_trailing_block(tmp_path: pathlib.Path, tool_name: str) -> None:
    """finalizerの末尾構造化欄が両条件を満たす場合だけ完了を記録する。"""
    sid = tool_name.lower()
    _run(
        {
            "session_id": sid,
            "tool_name": tool_name,
            "tool_input": {"subagent_type": "agent-toolkit:plan-file-finalizer"},
            "tool_response": {"result": "summary\nstatus: completed\nreview_completed: true"},
        },
        state_dir=tmp_path,
    )
    assert _read_state(tmp_path, sid)["plan_review_completed"] is True


@pytest.mark.parametrize(
    "completion",
    [
        "status: needs_escalation\nreview_completed: true",
        "status: completed\nreview_completed: false",
        "status: completed\nreview_completed: true\nartifact: /tmp/result.md",
        "本文にstatus: completedとreview_completed: trueを引用した。",
    ],
)
def test_plan_review_completed_rejects_invalid_or_non_trailing_text(tmp_path: pathlib.Path, completion: str) -> None:
    """未完了値、引用、末尾以外の記述による偽陽性を防ぐ。"""
    sid = "negative"
    _run(
        {
            "session_id": sid,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "plan-file-finalizer"},
            "tool_response": {"result": completion},
        },
        state_dir=tmp_path,
    )
    assert _read_state(tmp_path, sid).get("plan_review_completed") is not True


def test_plan_review_completed_accepts_closing_fence(tmp_path: pathlib.Path) -> None:
    """構造化欄の直後にフェンス閉じ行がある完了報告を受理する。"""
    sid = "closing-fence"
    _run(
        {
            "session_id": sid,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "plan-file-finalizer"},
            "tool_response": {"result": "```text\nstatus: completed\nreview_completed: true\n```"},
        },
        state_dir=tmp_path,
    )
    assert _read_state(tmp_path, sid)["plan_review_completed"] is True


@pytest.mark.parametrize(
    ("completion", "expected"),
    [
        ("escalation_points: なし\n\nstatus: completed\n\nreview_completed: true\n", True),
        ("status: completed\n\n\nreview_completed: true\n", True),
        ("status: completed\n補足の地の文\nreview_completed: true\n", False),
        ("status: completed\nreview_completed: true\n\n補足の地の文\n", False),
    ],
)
def test_plan_review_completed_skips_blank_lines_between_record_lines(
    tmp_path: pathlib.Path, completion: str, expected: bool
) -> None:
    """記録行の間の空行を無視し、それ以外の行では抽出を終了する。"""
    sid = "blank-lines"
    _run(
        {
            "session_id": sid,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "plan-file-finalizer"},
            "tool_response": {"result": completion},
        },
        state_dir=tmp_path,
    )
    assert (_read_state(tmp_path, sid).get("plan_review_completed") is True) is expected


def test_background_finalizer_registers_only_agent_id(tmp_path: pathlib.Path) -> None:
    """完了報告本文を取得できない背景実行ではagentIdだけを登録する。"""
    sid = "background-finalizer"
    _run(
        {
            "session_id": sid,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "plan-file-finalizer"},
            "tool_response": {"agentId": "finalizer-123"},
        },
        state_dir=tmp_path,
    )
    state = _read_state(tmp_path, sid)
    assert "finalizer-123" in state["plan_file_finalizer_active_subagent_sessions"]
    assert state.get("plan_review_completed") is not True


def test_synchronous_finalizer_does_not_register_agent_id(tmp_path: pathlib.Path) -> None:
    """完了報告本文を取得できた同期完了では活動中agentIdを残さない。"""
    sid = "synchronous-finalizer"
    _run(
        {
            "session_id": sid,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "plan-file-finalizer"},
            "tool_response": {
                "agentId": "finalizer-456",
                "result": "status: completed\nreview_completed: true",
            },
        },
        state_dir=tmp_path,
    )
    state = _read_state(tmp_path, sid)
    assert not state.get("plan_file_finalizer_active_subagent_sessions")
    assert state["plan_review_completed"] is True


def test_plan_mode_invocation_resets_finalizer_tracking(tmp_path: pathlib.Path) -> None:
    """新しい計画作業の開始時に完了状態とfinalizer活動中辞書をリセットする。"""
    sid = "reset-finalizer"
    state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
    state_path.write_text(
        json.dumps(
            {
                "plan_mode_skill_invoked": True,
                "plan_review_completed": True,
                "plan_file_finalizer_active_subagent_sessions": {"stale": {"started_at": 0.0}},
            }
        ),
        encoding="utf-8",
    )
    _run(
        {
            "session_id": sid,
            "tool_name": "Skill",
            "tool_input": {"skill": "agent-toolkit:plan-mode"},
        },
        state_dir=tmp_path,
    )
    state = _read_state(tmp_path, sid)
    assert state["plan_review_completed"] is False
    assert not state["plan_file_finalizer_active_subagent_sessions"]


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
    assert "plan_review_completed" not in state
    assert removed_flag not in state


@pytest.mark.parametrize("subagent_type", ["plan-impl-executor", "agent-toolkit:plan-impl-executor"])
def test_plan_impl_executor_registers_active_session(tmp_path: pathlib.Path, subagent_type: str) -> None:
    """executorのサブセッションIDを完了報告検査用に記録する。"""
    sid = subagent_type.replace(":", "-")
    _run(
        {
            "session_id": sid,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": subagent_type},
            "tool_response": {"agentId": "sub-session-123"},
        },
        state_dir=tmp_path,
    )
    active = _read_state(tmp_path, sid)["plan_impl_executor_active_subagent_sessions"]
    assert active["sub-session-123"]["subagent_type"] == subagent_type
