"""scripts/claude_hook_autonomous_exit.py のテスト。

dotfiles個人環境専用のStopフックのテスト。独立スクリプトなのでsubprocessで起動し
stdout（JSON）を検証する。判定分岐は環境変数未設定・`stop_hook_active`・
非同期待機中・`autonomous_exit_invoked`・block送出を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys

_SCRIPT = pathlib.Path(__file__).resolve().parent / "claude_hook_autonomous_exit.py"

_ENV_REQUIRED = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"


def _write_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _write_transcript(tmp_path: pathlib.Path, entries: list[dict]) -> pathlib.Path:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )
    return transcript


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
    autonomous_exit_required: bool = True,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    if autonomous_exit_required:
        env[_ENV_REQUIRED] = "1"
    else:
        env.pop(_ENV_REQUIRED, None)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _parse_decision(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


class TestApproveConditions:
    """approve条件: 環境変数未設定・構造的継続中・呼び出し済みのいずれか。"""

    def test_env_not_required_approves(self, tmp_path: pathlib.Path):
        """環境変数`DOTFILES_AUTONOMOUS_EXIT_REQUIRED`が未設定 → 無条件approve。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {"session_id": "no-env", "transcript_path": str(transcript)},
            state_dir=tmp_path,
            autonomous_exit_required=False,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_stop_hook_active_approves(self, tmp_path: pathlib.Path):
        """`stop_hook_active`が真 → 再帰呼び出し抑止のため即approve。"""
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

    def test_pending_async_work_approves(self, tmp_path: pathlib.Path):
        """直前ターンの最後のtool_useが非同期待機系 → approve。"""
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

    def test_autonomous_exit_invoked_approves(self, tmp_path: pathlib.Path):
        """`autonomous_exit_invoked`フラグが真 → approve。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(tmp_path, "exit-invoked", {"autonomous_exit_invoked": True})
        result = _run(
            {"session_id": "exit-invoked", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision


class TestBlockCondition:
    """block条件: 環境変数設定済み・構造的継続なし・未呼び出しの場合は毎回blockする。"""

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
        assert "exit-session" in reason
        assert "session-review-dotfiles" in reason

    def test_repeats_block_each_stop(self, tmp_path: pathlib.Path):
        """同一transcriptで2回連続Stopしても、未呼び出しなら毎回blockする。"""
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
        """transcript未指定でも`is_pending_async_work`判定をスキップしblockする。"""
        result = _run({"session_id": "no-transcript"}, state_dir=tmp_path)
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
