"""agent-toolkit/scripts/autonomous_exit.py のテスト。

agent-toolkit pluginが提供するStopフックを共通入口から起動し、環境変数・再帰呼び出し・
非同期待機・呼び出し済み状態・blockの各契約を検証する。
"""

import json
import os
import pathlib
import subprocess

import _fork_runner
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE, _write_transcript

_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"

_ENV_REQUIRED = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_LEGACY_ENV_REQUIRED = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"
_ENV_DELEGATED_SESSION = "AGENT_TOOLKIT_DELEGATED_SESSION"


def _write_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _assistant_with_async_tool(tool_name: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "作業を継続します。"},
                {"type": "tool_use", "id": "x", "name": tool_name, "input": {}},
            ],
            "stop_reason": "end_turn",
        },
    }


def _user_entry(text: str = "hello") -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _assistant_text_only(text: str = "作業を継続します。") -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        },
    }


def _run(
    payload: object,
    *,
    state_dir: pathlib.Path,
    required_env: str | None = _ENV_REQUIRED,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    env["HOME"] = str(state_dir)
    env["USERPROFILE"] = str(state_dir)
    env.pop(_ENV_REQUIRED, None)
    env.pop(_LEGACY_ENV_REQUIRED, None)
    env.pop(_ENV_DELEGATED_SESSION, None)
    if required_env is not None:
        env[required_env] = "1"
    if extra_env is not None:
        env.update(extra_env)
    return _fork_runner.run_script(_SCRIPT, argv=("autonomous_exit",), input=text, env=env)


def _parse_decision(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


class TestApproveConditions:
    """approve条件: 環境変数未設定・構造的継続中・呼び出し済みのいずれか。"""

    def test_env_not_required_approves(self, tmp_path: pathlib.Path):
        """環境変数`AGENT_TOOLKIT_PROCESS_LOOP_SESSION`が未設定ならapproveする。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {"session_id": "no-env", "transcript_path": str(transcript)},
            state_dir=tmp_path,
            required_env=None,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_stop_hook_active_approves(self, tmp_path: pathlib.Path):
        """`stop_hook_active`が真なら再帰呼び出し抑止のためapproveする。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {
                "session_id": "stop-hook-active",
                "transcript_path": str(transcript),
                "stop_hook_active": True,
            },
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_delegated_session_approves(self, tmp_path: pathlib.Path) -> None:
        """process-loop環境を継承した委譲先では終了工程を再促しない。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {"session_id": "delegated", "transcript_path": str(transcript)},
            state_dir=tmp_path,
            extra_env={_ENV_DELEGATED_SESSION: "1"},
        )
        assert "decision" not in _parse_decision(result)

    def test_pending_async_work_approves(self, tmp_path: pathlib.Path):
        """直前ターンの最後のtool_useが非同期待機系ならapproveする。"""
        transcript = _write_transcript(
            tmp_path,
            [_user_entry(), _assistant_with_async_tool("Agent")],
        )
        result = _run(
            {"session_id": "pending-async", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_background_tasks_payload_approves(self, tmp_path: pathlib.Path):
        """transcriptに起動痕跡が無くてもStop payloadのtaskが未完了ならapproveする。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {
                "session_id": "payload-pending",
                "transcript_path": str(transcript),
                "background_tasks": [{"type": "subagent", "id": "agent-restarted"}],
            },
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_empty_background_tasks_preserve_block_path(self, tmp_path: pathlib.Path):
        """空のStop payloadでは現行の終了工程再促へ戻る。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {
                "session_id": "payload-empty",
                "transcript_path": str(transcript),
                "background_tasks": [],
            },
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"

    def test_autonomous_exit_invoked_approves(self, tmp_path: pathlib.Path):
        """`autonomous_exit_invoked`フラグが真ならapproveする。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(tmp_path, "exit-invoked", {"autonomous_exit_invoked": True})
        result = _run(
            {"session_id": "exit-invoked", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision


class TestBlockCondition:
    """block条件: 環境変数設定済み・構造的継続なし・未呼び出しの場合。"""

    def test_not_invoked_blocks_with_reason(self, tmp_path: pathlib.Path):
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {"session_id": "not-invoked", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
        reason = decision.get("reason")
        assert isinstance(reason, str)
        assert "agent-toolkit:process-wi" in reason
        assert "agent-toolkit:completion-report" in reason
        assert "agent-toolkit:exit-session" in reason
        assert "Fix: " in reason
        assert "agent-toolkit/autonomous_exit" in reason

    def test_reason_body_orders_completion_report_before_exit_session(self, tmp_path: pathlib.Path) -> None:
        """完了報告を終える前にexit-sessionへ進まないよう順序を明示する。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {"session_id": "scope", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        reason = _parse_decision(result)["reason"]
        assert reason.index("agent-toolkit:completion-report") < reason.index("agent-toolkit:exit-session")

    def test_legacy_process_loop_env_blocks(self, tmp_path: pathlib.Path):
        """旧process-loopの移行互換名だけが設定された場合もblockする。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {"session_id": "legacy-env", "transcript_path": str(transcript)},
            state_dir=tmp_path,
            required_env=_LEGACY_ENV_REQUIRED,
        )
        assert _parse_decision(result).get("decision") == "block"

    def test_repeats_block_each_stop(self, tmp_path: pathlib.Path):
        """同一transcriptで2回連続Stopしても未呼び出しなら毎回blockする。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        first = _run(
            {"session_id": "repeat", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        second = _run(
            {"session_id": "repeat", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert _parse_decision(first).get("decision") == "block"
        assert _parse_decision(second).get("decision") == "block"


class TestEdgeCases:
    """エッジケース。"""

    def test_invalid_json_approves(self, tmp_path: pathlib.Path):
        result = _run("not json", state_dir=tmp_path)
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_empty_session_id_approves(self, tmp_path: pathlib.Path):
        result = _run({"session_id": "", "transcript_path": "/x"}, state_dir=tmp_path)
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_missing_transcript_still_blocks(self, tmp_path: pathlib.Path):
        """transcript未指定でも`is_pending_async_work`をスキップしてblockする。"""
        result = _run({"session_id": "no-transcript"}, state_dir=tmp_path)
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
