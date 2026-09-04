"""agent-toolkit/scripts/user_prompt_submit.py のテスト。

subprocessで起動しexit code・状態ファイルの内容を検証する。
スラッシュコマンド起動時のセッション状態フラグ書き込みを網羅検証する。

"""

import json
import os
import pathlib
import subprocess
import threading
import time

import _fork_runner
import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE, _read_state

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
_SCRIPT = _SCRIPTS_DIR / "hook.py"


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

    def test_detects_short_skill_command_process_wi(self, tmp_path: pathlib.Path):
        sid = "short-process-wi"
        result = _run(
            {"session_id": sid, "prompt": "/process-wi"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("process_wi_skill_invoked") is True


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
            {"session_id": sid, "prompt": "$agent-toolkit:process-wi"},
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
                "prompt": "/agent-toolkit:process-wi",
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

    def test_codex_normal_prompt_keeps_unrelated_state(self, tmp_path: pathlib.Path):
        sid = "codex-normal-keeps-unrelated"
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
        state_path.write_text(
            json.dumps({"plan_mode_skill_invoked": True}),
            encoding="utf-8",
        )

        result = _run(
            {"session_id": sid, "prompt": "通常のユーザー発話です。", "model": "gpt-5"},
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        assert _read_state(tmp_path, sid)["plan_mode_skill_invoked"] is True

    def test_codex_process_wi_command_keeps_unrelated_state(self, tmp_path: pathlib.Path):
        sid = "codex-process-wi-keeps-unrelated"
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
        state_path.write_text(
            json.dumps({"plan_mode_skill_invoked": True}),
            encoding="utf-8",
        )

        result = _run(
            {"session_id": sid, "prompt": "$agent-toolkit:process-wi", "model": "gpt-5"},
            state_dir=tmp_path,
        )

        state = _read_state(tmp_path, sid)
        assert result.returncode == 0
        assert state["process_wi_skill_invoked"] is True
        assert state["plan_mode_skill_invoked"] is True


class TestClaudePlanSessionTitle:
    """Claude Codeの計画ファイルstemとsessionTitleの同期契約を検証する。"""

    @staticmethod
    def _state_path(state_dir: pathlib.Path, session_id: str) -> pathlib.Path:
        return state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)

    @staticmethod
    def _title_state_path(state_dir: pathlib.Path, session_id: str) -> pathlib.Path:
        return state_dir / "claude-agent-toolkit-session-title" / f"{session_id}.json"

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
        self._prepare_plan(tmp_path, monkeypatch, sid, "awi-batch.md")

        result = _run(
            {"session_id": sid, "prompt": "計画を続けます", "hook_event_name": "UserPromptSubmit"},
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["sessionTitle"] == "awi-batch"
        assert "last_hook_session_title" not in _read_state(tmp_path, sid)
        title_state = json.loads(self._title_state_path(tmp_path, sid).read_text(encoding="utf-8"))
        assert title_state == {"last_hook_session_title": "awi-batch"}

    def test_private_notes_plan_receives_current_plan_stem(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """新しいprivate-notes計画rootのメインも計画タイトルへ同期する。"""
        sid = "private-notes-plan-title"
        home = tmp_path / "home"
        plan = home / "private-notes" / "plans" / "2026" / "08" / "30-計画保存先移行-a1b2.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("# 計画\n", encoding="utf-8")
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(home / "private-notes"))
        self._state_path(tmp_path, sid).write_text(
            json.dumps({"current_plan_file_path": str(plan)}),
            encoding="utf-8",
        )

        result = _run(
            {"session_id": sid, "prompt": "計画を続けます", "hook_event_name": "UserPromptSubmit"},
            state_dir=tmp_path,
            home_dir=home,
        )

        assert result.returncode == 0
        assert json.loads(result.stdout)["hookSpecificOutput"]["sessionTitle"] == "30-計画保存先移行-a1b2"

    def test_same_session_does_not_emit_title_again(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sid = "plan-title-repeat"
        self._prepare_plan(tmp_path, monkeypatch, sid, "awi-batch.md")

        first = _run({"session_id": sid, "prompt": "最初の入力"}, state_dir=tmp_path)
        assert first.returncode == 0
        assert json.loads(first.stdout)["hookSpecificOutput"]["sessionTitle"] == "awi-batch"

        result = _run({"session_id": sid, "prompt": "通常の入力"}, state_dir=tmp_path)

        assert result.returncode == 0
        assert result.stdout == ""

    def test_later_plan_edit_does_not_emit_title_again_in_same_session(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = "plan-title-update"
        old_plan = self._prepare_plan(tmp_path, monkeypatch, sid, "old-plan.md")
        first = _run({"session_id": sid, "prompt": "最初の入力"}, state_dir=tmp_path)
        assert first.returncode == 0
        assert json.loads(first.stdout)["hookSpecificOutput"]["sessionTitle"] == "old-plan"
        new_plan = old_plan.parent / "new-plan.md"
        new_plan.write_text("# 新計画\n", encoding="utf-8")
        state_path = self._state_path(tmp_path, sid)
        state = _read_state(tmp_path, sid)
        state["current_plan_file_path"] = str(new_plan)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        result = _run({"session_id": sid, "prompt": "計画を更新"}, state_dir=tmp_path)

        assert result.returncode == 0
        assert result.stdout == ""
        title_state = json.loads(self._title_state_path(tmp_path, sid).read_text(encoding="utf-8"))
        assert title_state == {"last_hook_session_title": "old-plan"}

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
        assert _read_state(tmp_path, sid) == {}
        title_state_path = self._title_state_path(tmp_path, sid)
        assert json.loads(title_state_path.read_text(encoding="utf-8")) == {"last_hook_session_title": "original-plan"}

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
        assert json.loads(title_state_path.read_text(encoding="utf-8")) == {"last_hook_session_title": "original-plan"}

    def test_concurrent_prompts_emit_title_once(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """同一セッションの並行入力では一方だけが計画名を出力する。"""
        sid = "plan-title-concurrent"
        self._prepare_plan(tmp_path, monkeypatch, sid, "concurrent-plan.md")
        results: list[subprocess.CompletedProcess[str]] = []

        def _submit() -> None:
            results.append(_run({"session_id": sid, "prompt": "並行入力"}, state_dir=tmp_path))

        threads = [threading.Thread(target=_submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert all(not thread.is_alive() for thread in threads)
        assert all(result.returncode == 0 for result in results)
        outputs = [result.stdout for result in results if result.stdout]
        assert len(outputs) == 1
        assert json.loads(outputs[0])["hookSpecificOutput"]["sessionTitle"] == "concurrent-plan"

    def test_corrupt_title_record_suppresses_output(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """再出力抑止記録が破損している場合は計画名を出力しない。"""
        sid = "plan-title-corrupt"
        self._prepare_plan(tmp_path, monkeypatch, sid, "corrupt-plan.md")
        title_path = self._title_state_path(tmp_path, sid)
        title_path.parent.mkdir(parents=True)
        title_path.write_text("{", encoding="utf-8")

        result = _run({"session_id": sid, "prompt": "通常の入力"}, state_dir=tmp_path)

        assert result.returncode == 0
        assert result.stdout == ""
        assert title_path.read_text(encoding="utf-8") == "{"

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
        assert not self._title_state_path(tmp_path, sid).exists()

    def test_codex_payload_does_not_emit_plan_title(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        sid = "plan-title-codex"
        self._prepare_plan(tmp_path, monkeypatch, sid, "codex-plan.md")

        result = _run(
            {"session_id": sid, "prompt": "通常の入力", "model": "gpt-5"},
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        assert result.stdout == ""
        assert not self._title_state_path(tmp_path, sid).exists()
