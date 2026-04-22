"""plugins/agent-toolkit/scripts/posttooluse.py のテスト。

PostToolUse セッション状態記録のテスト。
subprocess で起動し exit code・状態ファイルの内容を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "posttooluse.py"
_SKILL_MD = pathlib.Path(__file__).resolve().parents[1] / "skills" / "plan-mode" / "SKILL.md"


def _run(
    payload: object,
    *,
    state_dir: pathlib.Path | None = None,
    home_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if state_dir is not None:
        env["TMPDIR"] = str(state_dir)
        env["TEMP"] = str(state_dir)
        env["TMP"] = str(state_dir)
    if home_dir is not None:
        env["HOME"] = str(home_dir)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class TestTestExecution:
    """テスト実行検出。"""

    @pytest.mark.parametrize(
        "command",
        [
            "pytest",
            "uv run pytest -v",
            "python -m pytest tests/",
            "make test",
            "pyfltr run-for-agent",
            "uv run pyfltr ci",
            "uv run pyfltr fast",
            "uv run pyfltr run-for-agent",
            "npm test",
            "pnpm test",
            "pnpm run test",
            "cargo test",
        ],
    )
    def test_test_commands_detected(self, tmp_path: pathlib.Path, command: str):
        sid = "test-exec-detect"
        result = _run(
            {"session_id": sid, "tool_input": {"command": command}},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state.get("test_executed") is True, f"command={command!r} not detected"

    def test_unrelated_command_no_change(self, tmp_path: pathlib.Path):
        sid = "test-unrelated"
        _run(
            {"session_id": sid, "tool_input": {"command": "echo hello"}},
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("test_executed") is not True


class TestGitStatusCheck:
    """Git 状態確認検出。"""

    @pytest.mark.parametrize("command", ["git status", "git log --decorate --oneline -5", "git diff"])
    def test_git_commands_detected(self, tmp_path: pathlib.Path, command: str):
        sid = "test-git-status"
        _run(
            {"session_id": sid, "tool_input": {"command": command}},
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("git_status_checked") is True


class TestCodexResume:
    """codex exec resume 検出。"""

    def test_resume_increments_count(self, tmp_path: pathlib.Path):
        sid = "test-codex-resume"
        for _ in range(3):
            _run(
                {"session_id": sid, "tool_input": {"command": "codex exec resume --dangerously-bypass abc123 prompt"}},
                state_dir=tmp_path,
            )
        state = _read_state(tmp_path, sid)
        assert state.get("codex_resume_count") == 3

    def test_initial_exec_not_counted(self, tmp_path: pathlib.Path):
        sid = "test-codex-initial"
        _run(
            {"session_id": sid, "tool_input": {"command": "codex exec --dangerously-bypass plan.md prompt"}},
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("codex_resume_count", 0) == 0


class TestEdgeCases:
    """エッジケース。"""

    def test_invalid_json_exits_zero(self, tmp_path: pathlib.Path):
        result = _run("not json", state_dir=tmp_path)
        assert result.returncode == 0

    def test_missing_session_id(self, tmp_path: pathlib.Path):
        result = _run({"tool_input": {"command": "pytest"}}, state_dir=tmp_path)
        assert result.returncode == 0

    def test_missing_command(self, tmp_path: pathlib.Path):
        result = _run({"session_id": "x", "tool_input": {}}, state_dir=tmp_path)
        assert result.returncode == 0

    def test_silent_output(self, tmp_path: pathlib.Path):
        """PostToolUse は stdout に何も出さない。"""
        result = _run(
            {"session_id": "silent", "tool_input": {"command": "pytest"}},
            state_dir=tmp_path,
        )
        assert result.stdout == ""


class TestGitLogChecked:
    """git_log_checked 状態の管理。"""

    def test_git_log_sets_checked(self, tmp_path: pathlib.Path):
        sid = "log-check"
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline -5"}},
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("git_log_checked") is True

    def test_git_commit_resets_checked(self, tmp_path: pathlib.Path):
        sid = "log-reset-commit"
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") is True
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") is False

    def test_git_rebase_resets_checked(self, tmp_path: pathlib.Path):
        sid = "log-reset-rebase"
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}},
            state_dir=tmp_path,
        )
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "GIT_SEQUENCE_EDITOR=: git rebase -i HEAD~2"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") is False

    def test_git_push_resets_checked(self, tmp_path: pathlib.Path):
        sid = "log-reset-push"
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}},
            state_dir=tmp_path,
        )
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git push origin master"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") is False

    def test_write_resets_checked(self, tmp_path: pathlib.Path):
        sid = "log-reset-write"
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}},
            state_dir=tmp_path,
        )
        _run(
            {"session_id": sid, "tool_name": "Write", "tool_input": {"file_path": "/tmp/x.txt", "content": "x"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") is False

    def test_edit_resets_checked(self, tmp_path: pathlib.Path):
        sid = "log-reset-edit"
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}},
            state_dir=tmp_path,
        )
        _run(
            {"session_id": sid, "tool_name": "Edit", "tool_input": {"file_path": "/tmp/x.txt"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") is False

    def test_unrelated_bash_no_reset(self, tmp_path: pathlib.Path):
        sid = "log-no-reset"
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}},
            state_dir=tmp_path,
        )
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "echo hello"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") is True


# plan file 形式検査で使う各種 Markdown 断片。テスト全体で共用する。
_VALID_PLAN = (
    "# タイトル\n\n"
    "## 背景\n\n説明。\n\n"
    "## 対応方針\n\n"
    "### ユーザー合意済み事項\n\n- a\n\n"
    "## 調査結果\n\n- x\n\n"
    "## 変更内容\n\n- y\n\n"
    "## 実装・検証・レビュー\n\n- w\n\n"
    "## 変更履歴\n\n- 初版\n\n"
    "## 計画ファイル\n\n`~/.claude/plans/xxx.md`\n\n"
)


def _prepare_plan_home(home_dir: pathlib.Path) -> pathlib.Path:
    """``<home>/.claude/plans`` を作成してそのパスを返す。"""
    plans = home_dir / ".claude" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    return plans


def _write_plan(plans_dir: pathlib.Path, name: str, content: str) -> pathlib.Path:
    path = plans_dir / name
    path.write_text(content, encoding="utf-8")
    return path


def _parse_hook_output(stdout: str) -> dict | None:
    stdout = stdout.strip()
    if not stdout:
        return None
    return json.loads(stdout)


class TestPlanFormatCheck:
    """plan file 形式検査。"""

    def _home(self, tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        return home, plans

    def test_valid_plan_passes_silently(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        plan = _write_plan(plans, "sample.md", _VALID_PLAN)
        result = _run(
            {
                "session_id": "plan-ok",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": _VALID_PLAN},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_missing_required_section_is_warned(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 調査結果セクションを欠落させた変種
        content = _VALID_PLAN.replace("## 調査結果\n\n- x\n\n", "")
        plan = _write_plan(plans, "missing.md", content)
        result = _run(
            {
                "session_id": "plan-miss",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "missing required H2 sections" in msg
        assert "調査結果" in msg
        assert "[auto-generated: agent-toolkit/posttooluse][warn]" in msg

    def test_out_of_order_is_warned(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 変更内容 と 調査結果 を入れ替える
        content = (
            "# タイトル\n\n"
            "## 背景\n\n説明。\n\n"
            "## 対応方針\n\n- a\n\n"
            "## 変更内容\n\n- y\n\n"
            "## 調査結果\n\n- x\n\n"
            "## 実装・検証・レビュー\n\n- w\n"
        )
        plan = _write_plan(plans, "order.md", content)
        result = _run(
            {
                "session_id": "plan-order",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "out of order" in msg

    def test_unexpected_section_is_warned(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        content = _VALID_PLAN + "\n## 備考\n\n自由記述。\n"
        plan = _write_plan(plans, "extra.md", content)
        result = _run(
            {
                "session_id": "plan-extra",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "unexpected H2" in msg
        assert "備考" in msg

    def test_history_not_at_end_is_warned(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 変更履歴を中間に置いた変種
        content = (
            "# タイトル\n\n"
            "## 背景\n\n説明。\n\n"
            "## 対応方針\n\n- a\n\n"
            "## 調査結果\n\n- x\n\n"
            "## 変更履歴\n\n1. 仮\n\n"
            "## 変更内容\n\n- y\n\n"
            "## 実装・検証・レビュー\n\n- w\n"
        )
        plan = _write_plan(plans, "hist.md", content)
        result = _run(
            {
                "session_id": "plan-hist-mid",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "変更履歴" in msg

    def test_review_md_is_skipped(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        content = "# レビュー\n\nなにか書く。\n"
        plan = _write_plan(plans, "sample.review.md", content)
        result = _run(
            {
                "session_id": "plan-review",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        assert result.stdout.strip() == ""

    def test_codex_log_is_skipped(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        log_path = plans / "sample.codex.log"
        log_path.write_text("codex output...", encoding="utf-8")
        result = _run(
            {
                "session_id": "plan-log",
                "tool_name": "Write",
                "tool_input": {"file_path": str(log_path), "content": "codex output..."},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        assert result.stdout.strip() == ""

    def test_non_plans_path_is_skipped(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        home.mkdir()
        other = tmp_path / "other.md"
        other.write_text("# 無関係\n", encoding="utf-8")
        result = _run(
            {
                "session_id": "plan-other",
                "tool_name": "Write",
                "tool_input": {"file_path": str(other), "content": "# 無関係\n"},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        assert result.stdout.strip() == ""

    def test_subdirectory_plan_is_skipped(self, tmp_path: pathlib.Path):
        """`~/.claude/plans/` のサブディレクトリ配下は対象外 (直下のみ検査)。"""
        home, plans = self._home(tmp_path)
        sub = plans / "archive"
        sub.mkdir()
        plan = sub / "old.md"
        plan.write_text("# 古い計画\n", encoding="utf-8")
        result = _run(
            {
                "session_id": "plan-sub",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": "# 古い計画\n"},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        assert result.stdout.strip() == ""

    def test_edit_tool_triggers_check(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 先に崩れた plan を作り、Edit ツールからのフック通知を検証する
        content = "# タイトル\n\n## 背景\n\nx\n"
        plan = _write_plan(plans, "edit.md", content)
        result = _run(
            {
                "session_id": "plan-edit",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(plan), "old_string": "x", "new_string": "y"},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_multiedit_tool_triggers_check(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        content = "# タイトル\n"
        plan = _write_plan(plans, "multi.md", content)
        result = _run(
            {
                "session_id": "plan-multi",
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(plan),
                    "edits": [{"old_string": "foo", "new_string": "bar"}],
                },
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_code_fence_h2_is_ignored(self, tmp_path: pathlib.Path):
        """コードフェンス内の `## 見出し` は見出しとしてカウントしない。"""
        home, plans = self._home(tmp_path)
        content = (
            "# タイトル\n\n"
            "## 背景\n\n"
            "```markdown\n"
            "## 予期せぬ見出し\n"
            "```\n\n"
            "## 対応方針\n\n- a\n\n"
            "## 調査結果\n\n- x\n\n"
            "## 変更内容\n\n- y\n\n"
            "## 実装・検証・レビュー\n\n- w\n\n"
            "## 変更履歴\n\n- w\n\n"
            "## 計画ファイル\n\n- w\n\n"
        )
        plan = _write_plan(plans, "fence.md", content)
        result = _run(
            {
                "session_id": "plan-fence",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        assert result.stdout.strip() == ""

    def test_bash_does_not_emit_plan_check(self, tmp_path: pathlib.Path):
        """Bash ツールでは plan check が実行されず stdout が空のまま。"""
        result = _run(
            {
                "session_id": "bash-silent",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest"},
            },
            state_dir=tmp_path,
        )
        assert result.stdout == ""


class TestPlanFormatSsot:
    """期待セクション一覧が `plan-mode/SKILL.md` に全て登場することを検査する。"""

    def test_required_and_optional_h2_appear_in_skill(self):
        text = _SKILL_MD.read_text(encoding="utf-8")
        # 必須 H2 は全て SKILL.md 内の該当構造定義に登場する
        for heading in ("背景", "対応方針", "調査結果", "変更内容", "実装・検証・レビュー", "変更履歴", "計画ファイル"):
            assert f"## {heading}" in text, f"SKILL.md に `## {heading}` が無い"
