"""agent-toolkit/scripts/user_prompt_submit.py のテスト。

subprocessで起動しexit code・状態ファイルの内容を検証する。
スラッシュコマンド起動時のセッション状態フラグ書き込みを網羅検証する。

"""

import json
import os
import pathlib
import subprocess
import tempfile
import threading
import time
from typing import Any

import _fork_runner
import _session_state
import pytest
import user_prompt_submit
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE, _read_state

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
_SCRIPT = _SCRIPTS_DIR / "claude_hook.py"


def _run(
    payload: dict | str,
    *,
    state_dir: pathlib.Path,
    home_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run_subcommand("user_prompt_submit", payload, state_dir=state_dir, home_dir=home_dir)


def _run_subcommand(
    subcommand: str,
    payload: dict | str,
    *,
    state_dir: pathlib.Path,
    home_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    if home_dir is not None:
        env["HOME"] = str(home_dir)
    return _fork_runner.run_script(_SCRIPT, argv=(subcommand,), input=text, env=env)


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

    @pytest.mark.parametrize(
        ("prompt", "flag"),
        [
            ("/agent-toolkit:plan-and-add-feedback", "plan_and_add_feedback_skill_invoked"),
            ("/plan-and-add-feedback", "plan_and_add_feedback_skill_invoked"),
            ("$agent-toolkit:plan-and-add-feedback", "plan_and_add_feedback_skill_invoked"),
            ("$plan-and-add-feedback", "plan_and_add_feedback_skill_invoked"),
            ("/agent-toolkit:add-feedback", "add_feedback_skill_invoked"),
            ("/add-feedback", "add_feedback_skill_invoked"),
            ("$agent-toolkit:add-feedback", "add_feedback_skill_invoked"),
            ("$add-feedback", "add_feedback_skill_invoked"),
        ],
    )
    def test_detects_feedback_submission_skill_commands(
        self,
        tmp_path: pathlib.Path,
        prompt: str,
        flag: str,
    ) -> None:
        sid = f"feedback-command-{prompt[0]}-{flag}"
        payload = {"session_id": sid, "prompt": prompt}
        if prompt.startswith("$"):
            payload["model"] = "gpt-5"
        result = _run(payload, state_dir=tmp_path)

        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get(flag) is True

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

    def test_claude_ignores_codex_skill_command(self, tmp_path: pathlib.Path):
        sid = "claude-dollar-command"
        result = _run(
            {"session_id": sid, "prompt": "$agent-toolkit:session-review", "transcript_path": "/tmp/review.jsonl"},
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        assert result.stdout == ""
        assert _read_state(tmp_path, sid) == {}

    def test_codex_ignores_claude_skill_command(self, tmp_path: pathlib.Path):
        sid = "codex-slash-command"
        result = _run(
            {
                "session_id": sid,
                "prompt": "/agent-toolkit:session-review",
                "transcript_path": "/tmp/review.jsonl",
                "model": "gpt-5",
            },
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        assert result.stdout == ""
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
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
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
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
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


class TestClaudePlanSessionTitle:
    """Claude Codeの計画ファイルstemとsessionTitleの同期契約を検証する。"""

    @staticmethod
    def _state_path(state_dir: pathlib.Path, session_id: str) -> pathlib.Path:
        return state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)

    def _prepare_plan(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        session_id: str,
        name: str = "draft-plan.md",
        **state_values: object,
    ) -> pathlib.Path:
        home = tmp_path / "home"
        plans = home / ".claude" / "plans"
        plans.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        plan = plans / name
        plan.write_text("# 計画\n", encoding="utf-8")
        state = {"current_plan_file_path": str(plan), **state_values}
        self._state_path(tmp_path, session_id).write_text(json.dumps(state), encoding="utf-8")
        return plan

    def test_official_payload_without_current_title_receives_current_plan_stem(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = "plan-title-initial"
        self._prepare_plan(tmp_path, monkeypatch, sid, "feedback-batch.md")

        result = _run(
            {"session_id": sid, "prompt": "計画を続けます", "hook_event_name": "UserPromptSubmit"},
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["sessionTitle"] == "feedback-batch"
        assert _read_state(tmp_path, sid)["last_hook_session_title"] == "feedback-batch"

    def test_same_session_does_not_emit_title_again(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sid = "plan-title-repeat"
        self._prepare_plan(tmp_path, monkeypatch, sid, "feedback-batch.md")

        first = _run({"session_id": sid, "prompt": "最初の入力"}, state_dir=tmp_path)
        assert first.returncode == 0
        assert json.loads(first.stdout)["hookSpecificOutput"]["sessionTitle"] == "feedback-batch"

        result = _run({"session_id": sid, "prompt": "通常の入力"}, state_dir=tmp_path)

        assert result.returncode == 0
        assert result.stdout == ""

    def test_later_plan_edit_does_not_emit_title_again_in_same_session(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = "plan-title-update"
        old_plan = self._prepare_plan(
            tmp_path,
            monkeypatch,
            sid,
            "old-plan.md",
            last_hook_session_title="old-plan",
        )
        new_plan = old_plan.parent / "new-plan.md"
        new_plan.write_text("# 新計画\n", encoding="utf-8")
        state_path = self._state_path(tmp_path, sid)
        state = _read_state(tmp_path, sid)
        state["current_plan_file_path"] = str(new_plan)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        result = _run({"session_id": sid, "prompt": "計画を更新"}, state_dir=tmp_path)

        assert result.returncode == 0
        assert result.stdout == ""
        assert _read_state(tmp_path, sid)["last_hook_session_title"] == "old-plan"

    def test_expired_state_resume_and_plan_edit_do_not_emit_title_again(self, tmp_path: pathlib.Path) -> None:
        """期限回収後に同じsession_idを再開して計画を編集しても再出力しない。"""
        sid = "plan-title-expired-resume"
        cleanup_sid = "plan-title-cleanup"
        home = tmp_path / "home"
        plans = home / ".claude" / "plans"
        plans.mkdir(parents=True)
        original_plan = plans / "original-plan.md"
        original_plan.write_text("# 元の計画\n", encoding="utf-8")

        recorded = _run_subcommand(
            "posttooluse",
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(original_plan), "content": "# 元の計画\n"},
            },
            state_dir=tmp_path,
            home_dir=home,
        )
        assert recorded.returncode == 0

        first = _run(
            {"session_id": sid, "prompt": "最初の入力"},
            state_dir=tmp_path,
            home_dir=home,
        )
        assert first.returncode == 0
        assert json.loads(first.stdout)["hookSpecificOutput"]["sessionTitle"] == "original-plan"

        state_path = self._state_path(tmp_path, sid)
        lock_path = state_path.with_name(state_path.name + ".lock")
        stale_time = time.time() - (14 * 24 * 60 * 60 + 60)
        os.utime(state_path, (stale_time, stale_time))
        os.utime(lock_path, (stale_time, stale_time))

        cleanup = _run_subcommand(
            "session_end_cleanup",
            {"hook_event_name": "SessionEnd", "session_id": cleanup_sid, "reason": "logout"},
            state_dir=tmp_path,
        )
        assert cleanup.returncode == 0
        assert _read_state(tmp_path, sid) == {"last_hook_session_title": "original-plan"}

        resumed_plan = plans / "resumed-plan.md"
        resumed_plan.write_text("# 再開後の計画\n", encoding="utf-8")
        resumed_edit = _run_subcommand(
            "posttooluse",
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(resumed_plan), "content": "# 再開後の計画\n"},
            },
            state_dir=tmp_path,
            home_dir=home,
        )
        assert resumed_edit.returncode == 0
        assert _read_state(tmp_path, sid)["current_plan_file_path"] == str(resumed_plan)

        next_prompt = _run(
            {"session_id": sid, "prompt": "再開後の入力"},
            state_dir=tmp_path,
            home_dir=home,
        )
        assert next_prompt.returncode == 0
        assert next_prompt.stdout == ""
        assert _read_state(tmp_path, sid)["last_hook_session_title"] == "original-plan"

    def test_sweep_rechecks_expiry_after_concurrent_title_record(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """期限判定後に計画名が記録されても、再開後の二回目の計画名を出力しない。"""
        sid = "plan-title-sweep-race"
        home = tmp_path / "home"
        plans = home / ".claude" / "plans"
        plans.mkdir(parents=True)
        plan = plans / "original-plan.md"
        plan.write_text("# 元の計画\n", encoding="utf-8")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
        state_path.write_text(json.dumps({"current_plan_file_path": str(plan)}), encoding="utf-8")
        stale_time = time.time() - (14 * 24 * 60 * 60 + 60)
        os.utime(state_path, (stale_time, stale_time))

        # UserPromptSubmitの状態更新を原子的置換の直前で停止し、期限判定後の競合順序を固定する。
        atomic_write_started = threading.Event()
        allow_atomic_write = threading.Event()
        phase_condition = threading.Condition()
        sweep_phase: list[str] = []
        original_atomic_write = vars(_session_state)["_atomic_write"]

        def _atomic_write(path: pathlib.Path, content: str) -> None:
            if path == state_path and not atomic_write_started.is_set():
                atomic_write_started.set()
                assert allow_atomic_write.wait(timeout=5)
            original_atomic_write(path, content)

        monkeypatch.setattr("_session_state._atomic_write", _atomic_write)
        original_acquire_lock = vars(_session_state)["_acquire_lock"]

        def _acquire_lock(lock_file: Any) -> None:
            if atomic_write_started.is_set():
                with phase_condition:
                    if not sweep_phase:
                        sweep_phase.append("lock")
                        phase_condition.notify()
            original_acquire_lock(lock_file)

        monkeypatch.setattr("_session_state._acquire_lock", _acquire_lock)
        original_retain_session_title = vars(_session_state)["_retain_session_title"]

        def _retain_session_title(path: pathlib.Path) -> bool:
            if path != state_path or not atomic_write_started.is_set():
                return original_retain_session_title(path)
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            with phase_condition:
                if not sweep_phase:
                    sweep_phase.append("read")
                    phase_condition.notify()
            assert allow_atomic_write.wait(timeout=5)
            return bool(snapshot.get("last_hook_session_title"))

        monkeypatch.setattr("_session_state._retain_session_title", _retain_session_title)
        sweep_result: list[int] = []

        prompt_result: list[int] = []

        def _submit_prompt() -> None:
            prompt_result.append(user_prompt_submit.main(json.dumps({"session_id": sid, "prompt": "再開後の入力"})))

        prompt_thread = threading.Thread(target=_submit_prompt)
        prompt_thread.start()
        assert atomic_write_started.wait(timeout=5)

        def _sweep() -> None:
            sweep_result.append(_session_state.sweep_stale_states())

        sweep_thread = threading.Thread(target=_sweep)
        sweep_thread.start()
        with phase_condition:
            assert phase_condition.wait_for(lambda: bool(sweep_phase), timeout=5)
        allow_atomic_write.set()
        prompt_thread.join(timeout=5)
        sweep_thread.join(timeout=5)
        assert not prompt_thread.is_alive()
        assert not sweep_thread.is_alive()
        assert prompt_result == [0]
        assert sweep_result == [0]
        first_output = json.loads(capsys.readouterr().out)
        assert first_output["hookSpecificOutput"]["sessionTitle"] == "original-plan"

        resumed_plan = plans / "resumed-plan.md"
        resumed_plan.write_text("# 再開後の計画\n", encoding="utf-8")
        assert (
            _session_state.update_state(
                sid,
                lambda current: {**current, "current_plan_file_path": str(resumed_plan)},
            )
            is True
        )

        assert user_prompt_submit.main(json.dumps({"session_id": sid, "prompt": "再開後の二回目の入力"})) == 0
        assert capsys.readouterr().out == ""
        assert _read_state(tmp_path, sid)["last_hook_session_title"] == "original-plan"

    @pytest.mark.parametrize("invalid_path", [None, 42, "relative.md", "/tmp/not-a-plan.md"])
    def test_invalid_or_missing_plan_path_fails_open(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        invalid_path: object,
    ) -> None:
        sid = f"plan-title-invalid-{type(invalid_path).__name__}"
        home = tmp_path / "home"
        (home / ".claude" / "plans").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        self._state_path(tmp_path, sid).write_text(
            json.dumps({"current_plan_file_path": invalid_path}),
            encoding="utf-8",
        )

        result = _run({"session_id": sid, "prompt": "通常の入力"}, state_dir=tmp_path)

        assert result.returncode == 0
        assert result.stdout == ""
        assert "last_hook_session_title" not in _read_state(tmp_path, sid)

    def test_codex_payload_does_not_emit_plan_title(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        sid = "plan-title-codex"
        self._prepare_plan(tmp_path, monkeypatch, sid, "codex-plan.md")

        result = _run(
            {"session_id": sid, "prompt": "通常の入力", "model": "gpt-5"},
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        assert result.stdout == ""
        assert "last_hook_session_title" not in _read_state(tmp_path, sid)

    def test_session_review_context_and_plan_title_share_one_json_response(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sid = "plan-title-and-review"
        self._prepare_plan(tmp_path, monkeypatch, sid, "review-plan.md")

        result = _run(
            {
                "session_id": sid,
                "prompt": "/session-review",
                "transcript_path": "/tmp/review.jsonl",
            },
            state_dir=tmp_path,
        )

        output = json.loads(result.stdout)
        hook_output = output["hookSpecificOutput"]
        assert hook_output["sessionTitle"] == "review-plan"
        assert "session_id=plan-title-and-review" in hook_output["additionalContext"]
