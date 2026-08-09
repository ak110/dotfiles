"""agent-toolkit/scripts/user_prompt_submit.py のテスト。

subprocessで起動しexit code・状態ファイルの内容を検証する。
スラッシュコマンド起動時のセッション状態フラグ書き込みを網羅検証する。

"""

import json
import os
import pathlib
import subprocess

import _fork_runner

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
_SCRIPT = _SCRIPTS_DIR / "claude_hook.py"


def _run(
    payload: dict | str,
    *,
    state_dir: pathlib.Path,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    return _fork_runner.run_script(_SCRIPT, argv=("user_prompt_submit",), input=text, env=env)


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class TestSlashCommandDetection:
    """スラッシュコマンド起動時のセッション状態フラグ書き込み検証。"""

    def test_detects_full_skill_command_plan_mode(self, tmp_path: pathlib.Path):
        sid = "full-plan-mode"
        result = _run(
            {"session_id": sid, "prompt": "/agent-toolkit:plan-mode"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("plan_mode_skill_invoked") is True

    def test_detects_short_skill_command_plan_mode(self, tmp_path: pathlib.Path):
        sid = "short-plan-mode"
        result = _run(
            {"session_id": sid, "prompt": "/plan-mode"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("plan_mode_skill_invoked") is True

    def test_detects_short_skill_command_process_feedbacks(self, tmp_path: pathlib.Path):
        sid = "short-process-feedbacks"
        result = _run(
            {"session_id": sid, "prompt": "/process-feedbacks"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("process_feedbacks_skill_invoked") is True

    def test_detects_short_skill_command_session_review(self, tmp_path: pathlib.Path):
        """短縮名`/session-review`もフルスキル名キーで正規化して保存する。"""
        sid = "short-session-review"
        result = _run(
            {"session_id": sid, "prompt": "/session-review", "transcript_path": "/tmp/review.jsonl"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        invoked = _read_state(tmp_path, sid).get("session_review_invoked")
        assert isinstance(invoked, dict)
        assert invoked.get("agent-toolkit:session-review") is True
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        context = output["hookSpecificOutput"]["additionalContext"]
        assert context.startswith("[auto-generated: agent-toolkit/user-prompt-submit] Use these exact values")
        assert "session_id=short-session-review" in context
        assert "/tmp/review.jsonl" in context
        assert context.endswith(
            "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"
        )

    def test_full_session_review_command_preserves_transcript_path(self, tmp_path: pathlib.Path):
        transcript_path = "/tmp/会話 transcript.jsonl"
        result = _run(
            {
                "session_id": "full-session-review",
                "prompt": "/agent-toolkit:session-review",
                "transcript_path": transcript_path,
            },
            state_dir=tmp_path,
        )

        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert transcript_path in context

    def test_codex_full_session_review_command_records_state_and_context(self, tmp_path: pathlib.Path):
        sid = "codex-full-session-review"
        transcript_path = "/tmp/codex rollout.jsonl"
        result = _run(
            {
                "session_id": sid,
                "prompt": "$agent-toolkit:session-review",
                "transcript_path": transcript_path,
                "model": "gpt-5",
            },
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        assert _read_state(tmp_path, sid)["session_review_invoked"] == {"agent-toolkit:session-review": True}
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert f"session_id={sid}" in context
        assert f"transcript_path={transcript_path}" in context

    def test_codex_short_session_review_command_records_state(self, tmp_path: pathlib.Path):
        sid = "codex-short-session-review"
        result = _run(
            {
                "session_id": sid,
                "prompt": "$session-review",
                "transcript_path": "/tmp/review.jsonl",
                "model": "gpt-5",
            },
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        assert _read_state(tmp_path, sid)["session_review_invoked"] == {"agent-toolkit:session-review": True}

    def test_session_review_with_arguments_does_not_emit_context(self, tmp_path: pathlib.Path):
        result = _run(
            {
                "session_id": "session-review-arguments",
                "prompt": "/session-review extra",
                "transcript_path": "/tmp/review.jsonl",
            },
            state_dir=tmp_path,
        )

        assert result.stdout == ""

    def test_session_review_without_transcript_fails_open(self, tmp_path: pathlib.Path):
        result = _run(
            {"session_id": "session-review-no-path", "prompt": "/session-review"},
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "session_id=session-review-no-path" in context
        assert "transcript_path=" in context


class TestNonMatchingPrompts:
    """非スキル起動プロンプトで状態と追加出力が変わらないことの検証。"""

    def test_ignores_non_skill_prompt(self, tmp_path: pathlib.Path):
        sid = "non-skill"
        result = _run(
            {"session_id": sid, "prompt": "通常のユーザー発話です。"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert _read_state(tmp_path, sid) == {}

    def test_ignores_unrelated_slash(self, tmp_path: pathlib.Path):
        sid = "unrelated-slash"
        result = _run(
            {"session_id": sid, "prompt": "/help"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid) == {}

    def test_handles_empty_payload(self, tmp_path: pathlib.Path):
        """空入力・prompt欠落payloadでexit 0、状態不変。"""
        result = _run("", state_dir=tmp_path)
        assert result.returncode == 0
        sid = "no-prompt"
        result = _run({"session_id": sid}, state_dir=tmp_path)
        assert result.returncode == 0
        assert _read_state(tmp_path, sid) == {}

    def test_ignores_slash_in_middle_of_prompt(self, tmp_path: pathlib.Path):
        """先頭行以外にスラッシュコマンドがあっても対象外。"""
        sid = "slash-middle"
        result = _run(
            {
                "session_id": sid,
                "prompt": "この会話について書きます。\n/plan-mode\n(参考: 上のようにも書けます)",
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("plan_mode_skill_invoked") is None

    def test_codex_normal_prompt_keeps_session_review_state(self, tmp_path: pathlib.Path):
        sid = "codex-normal-keeps-review"
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state_path.write_text(
            json.dumps({"session_review_invoked": {"agent-toolkit:session-review": True}}),
            encoding="utf-8",
        )

        result = _run(
            {"session_id": sid, "prompt": "通常のユーザー発話です。", "model": "gpt-5"},
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        assert _read_state(tmp_path, sid)["session_review_invoked"]["agent-toolkit:session-review"] is True

    def test_codex_plan_mode_command_keeps_session_review_state(self, tmp_path: pathlib.Path):
        sid = "codex-plan-keeps-review"
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state_path.write_text(
            json.dumps({"session_review_invoked": {"agent-toolkit:session-review": True}}),
            encoding="utf-8",
        )

        result = _run(
            {"session_id": sid, "prompt": "$agent-toolkit:plan-mode", "model": "gpt-5"},
            state_dir=tmp_path,
        )

        state = _read_state(tmp_path, sid)
        assert result.returncode == 0
        assert state["plan_mode_skill_invoked"] is True
        assert state["session_review_invoked"]["agent-toolkit:session-review"] is True
