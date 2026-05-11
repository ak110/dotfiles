"""agent-toolkit/scripts/posttooluse.py のテスト。

subprocessで起動しexit code・状態ファイルの内容を検証する。
"""

import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import types

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "posttooluse.py"
_SKILL_MD = pathlib.Path(__file__).resolve().parents[1] / "skills" / "plan-mode" / "SKILL.md"
_PLAN_FILE_REF = pathlib.Path(__file__).resolve().parents[1] / "skills" / "plan-mode" / "references" / "plan-file-guidelines.md"


def _load_posttooluse_module() -> types.ModuleType:
    """`scripts/posttooluse.py`を`importlib`で動的にインポートする。

    本体スクリプトの定数（`_PLAN_REQUIRED_H2`等）をテストから直接参照し、
    順序ドリフトを防ぐ。
    """
    spec = importlib.util.spec_from_file_location("posttooluse", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_POSTTOOLUSE = _load_posttooluse_module()
# SSOT共有のためprotected memberを直接参照する。
_PLAN_REQUIRED_H2: tuple[str, ...] = _POSTTOOLUSE._PLAN_REQUIRED_H2  # noqa: SLF001  # pylint: disable=protected-access


def _run(
    payload: dict | str,
    *,
    state_dir: pathlib.Path | None = None,
    home_dir: pathlib.Path | None = None,
    plan_mode_skill_invoked: bool = False,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if state_dir is not None:
        env["TMPDIR"] = str(state_dir)
        env["TEMP"] = str(state_dir)
        env["TMP"] = str(state_dir)
    if home_dir is not None:
        env["HOME"] = str(home_dir)
    # plan file形式検査はplan_mode_skill_invokedが真の場合のみ実行されるため、
    # 形式検査を期待するテストでは事前に状態ファイルへ同フラグを書き込んでおく。
    if plan_mode_skill_invoked and state_dir is not None and isinstance(payload, dict):
        sid = payload.get("session_id", "")
        if isinstance(sid, str) and sid:
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / f"claude-agent-toolkit-{sid}.json").write_text(
                json.dumps({"plan_mode_skill_invoked": True}, ensure_ascii=False),
                encoding="utf-8",
            )
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
            # 直接実行系
            "pytest",
            "uv run pytest -v",
            "python -m pytest tests/",
            "pyfltr run-for-agent",
            "uv run pyfltr ci",
            "uv run pyfltr fast",
            "uv run pyfltr run-for-agent",
            "uvx pyfltr run-for-agent",
            "uvx pyfltr ci",
            "pre-commit run",
            "pre-commit run --all-files",
            "uvx pre-commit run -a",
            "cargo test",
            # タスクランナー経由（test / check / validateアクションを各ランナーで網羅）
            "make test",
            "make check",
            "make validate",
            "mise run test",
            "mise run check",
            "npm test",
            "npm run test",
            "pnpm test",
            "pnpm run test",
            "pnpm run check",
            "yarn test",
            "yarn run validate",
            "just test",
            "just check",
            "task test",
            "task validate",
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


class TestPlanModeSkillInvocation:
    """plan-mode スキル呼び出し検出 (Skill ツール)。"""

    @pytest.mark.parametrize("skill_name", ["agent-toolkit:plan-mode", "plan-mode"])
    def test_skill_invocation_sets_flag(self, tmp_path: pathlib.Path, skill_name: str):
        sid = "skill-flag"
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": skill_name},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("plan_mode_skill_invoked") is True

    def test_other_skill_does_not_set_flag(self, tmp_path: pathlib.Path):
        sid = "skill-other"
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:coding-standards"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("plan_mode_skill_invoked") is not True


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
        """PostToolUse は stdout に何も書き込まない。"""
        result = _run(
            {"session_id": "silent", "tool_input": {"command": "pytest"}},
            state_dir=tmp_path,
        )
        assert result.stdout == ""


class TestGitLogChecked:
    """git_log_checked 状態の管理。

    cwdを伴うpayloadではcwd別辞書`{cwd: True}`で記録する。
    cwd空文字列環境では旧形式の単一bool値で記録し後方互換を保つ。
    Write / Edit / MultiEditは編集の事実が裏で他コミットを動かしている可能性に備え、
    辞書全体をクリアする（cwd別の細粒度リセットは行わない）。
    """

    def test_git_log_sets_checked_dict_when_cwd_present(self, tmp_path: pathlib.Path):
        sid = "log-check"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline -5"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("git_log_checked") == {"/repo/a": True}

    def test_git_log_sets_legacy_bool_when_cwd_absent(self, tmp_path: pathlib.Path):
        """cwd未指定では旧形式の単一bool値で記録する（後方互換）。"""
        sid = "log-check-nocwd"
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline -5"}},
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("git_log_checked") is True

    @pytest.mark.parametrize(
        ("label", "reset_command", "reset_cwd"),
        [
            ("commit", "git commit -m 'x'", "/repo/a"),
            ("rebase", "GIT_SEQUENCE_EDITOR=: git rebase -i HEAD~2", "/repo/a"),
            ("push", "git push origin master", "/repo/a"),
        ],
    )
    def test_same_cwd_reset_removes_only_target_entry(
        self, tmp_path: pathlib.Path, label: str, reset_command: str, reset_cwd: str
    ):
        """同cwdでのcommit/rebase/pushは該当cwdのみリセットする。"""
        sid = f"log-reset-{label}"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline"},
                "cwd": "/repo/b",
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") == {"/repo/a": True, "/repo/b": True}
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": reset_command},
                "cwd": reset_cwd,
            },
            state_dir=tmp_path,
        )
        # `/repo/a`のみリセットされ、`/repo/b`のエントリは残る。
        assert _read_state(tmp_path, sid).get("git_log_checked") == {"/repo/b": True}

    def test_legacy_bool_reset_back_to_false(self, tmp_path: pathlib.Path):
        """旧形式bool値はcommit時に`False`へ戻す（従来挙動）。"""
        sid = "log-legacy-reset"
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

    @pytest.mark.parametrize(
        ("edit_payload"),
        [
            {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x.txt", "content": "x"}},
            {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x.txt"}},
        ],
    )
    def test_edit_resets_dict(self, tmp_path: pathlib.Path, edit_payload: dict):
        """Write/Editは辞書全体をクリアする。"""
        sid = "log-reset-edit"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        _run({"session_id": sid, **edit_payload}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get("git_log_checked") == {}

    def test_edit_resets_legacy_bool(self, tmp_path: pathlib.Path):
        """旧形式bool値の場合もWrite/Editでリセットする（後方互換）。"""
        sid = "log-reset-edit-legacy"
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") is True
        _run(
            {"session_id": sid, "tool_name": "Write", "tool_input": {"file_path": "/tmp/x.txt", "content": "x"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") is False

    def test_unrelated_bash_no_reset(self, tmp_path: pathlib.Path):
        sid = "log-no-reset"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") == {"/repo/a": True}


# plan file形式検査で使う各種Markdown断片。テスト全体で共用する。
# 本体スクリプトの`_PLAN_REQUIRED_H2`から自動生成し、定義順序のドリフトを防ぐ。
# `## 対応方針`配下には判断材料H3を含めて妥当なplan構造を再現する。
_PLAN_BODY: dict[str, str] = {
    "変更履歴": "- 初版",
    "背景": "説明。",
    "対応方針": "### ユーザー合意済み事項\n\n- a",
    "調査結果": "- x",
    "変更内容": "- y",
    "実行方法": "- w",
    "計画ファイル（本ファイル）のパス": "`~/.claude/plans/xxx.md`",
}


def _build_valid_plan(
    omit: tuple[str, ...] = (),
    *,
    overrides: dict[str, str] | None = None,
    prefix: str = "",
) -> str:
    """`_PLAN_REQUIRED_H2`の順序に従い妥当なplan file内容を生成する。

    - `omit`: 指定したH2セクションを省略する（必須セクション欠落の検証用）。
    - `overrides`: 指定したH2セクションの本文を差し替える
      （コードフェンス・HTMLコメントなど特定本文での無視判定検証用）。
    - `prefix`: 戻り値の先頭に連結する文字列（YAMLフロントマターなどの検証用）。
    """
    overrides = overrides or {}
    parts: list[str] = ["# タイトル", ""]
    for h2 in _PLAN_REQUIRED_H2:
        if h2 in omit:
            continue
        parts.append(f"## {h2}")
        parts.append("")
        parts.append(overrides.get(h2, _PLAN_BODY[h2]))
        parts.append("")
    return prefix + "\n".join(parts) + "\n"


_VALID_PLAN = _build_valid_plan()


def _prepare_plan_home(home_dir: pathlib.Path) -> pathlib.Path:
    """`<home>/.claude/plans`を作成してパスを返す。"""
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
    """plan file形式検査。"""

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
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_missing_required_section_is_warned(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 調査結果セクションを欠落させた変種。
        content = _build_valid_plan(omit=("調査結果",))
        plan = _write_plan(plans, "missing.md", content)
        result = _run(
            {
                "session_id": "plan-miss",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "missing required H2 sections" in msg
        assert "調査結果" in msg
        assert "[auto-generated: agent-toolkit/posttooluse][warn]" in msg

    def test_missing_response_policy_is_warned(self, tmp_path: pathlib.Path):
        """``対応方針`` セクション欠落も必須セクション違反として警告される。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(omit=("対応方針",))
        plan = _write_plan(plans, "missing-policy.md", content)
        result = _run(
            {
                "session_id": "plan-miss-policy",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "missing required H2 sections" in msg
        assert "対応方針" in msg

    def test_out_of_order_is_warned(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 変更内容と調査結果を入れ替える。
        content = (
            "# タイトル\n\n"
            "## 背景\n\n説明。\n\n"
            "## 対応方針\n\n- a\n\n"
            "## 変更内容\n\n- y\n\n"
            "## 調査結果\n\n- x\n\n"
            "## 実行方法\n\n- w\n"
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
            plan_mode_skill_invoked=True,
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
            plan_mode_skill_invoked=True,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "unexpected H2" in msg
        assert "備考" in msg

    def test_history_not_at_top_is_warned(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 変更履歴を中間に置いた変種。
        content = (
            "# タイトル\n\n"
            "## 背景\n\n説明。\n\n"
            "## 対応方針\n\n- a\n\n"
            "## 調査結果\n\n- x\n\n"
            "## 変更履歴\n\n1. 仮\n\n"
            "## 変更内容\n\n- y\n\n"
            "## 実行方法\n\n- w\n"
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
            plan_mode_skill_invoked=True,
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
            plan_mode_skill_invoked=True,
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
            plan_mode_skill_invoked=True,
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
            plan_mode_skill_invoked=True,
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
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    def test_edit_tool_triggers_check(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 崩れたplanを生成し、EditツールからのHook通知を検証する。
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
            plan_mode_skill_invoked=True,
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
            plan_mode_skill_invoked=True,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_code_fence_h2_is_ignored(self, tmp_path: pathlib.Path):
        """コードフェンス内の `## 見出し` は見出しとしてカウントしない。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(
            overrides={
                "背景": "```markdown\n## 予期せぬ見出し\n```",
            }
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
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize(
        ("outer", "inner"),
        [
            ("````", "```"),  # バックティック同士 (長さ違い)
            ("~~~~", "```"),  # 外側チルダ・内側バックティック (字種一致チェックの回帰)
        ],
    )
    def test_nested_code_fence_h2_is_ignored(self, tmp_path: pathlib.Path, outer: str, inner: str):
        """外側フェンスが同字種・同長以上でのみ閉じ、内部の `##` を見出し扱いしない。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(
            overrides={
                "背景": (f"{outer}markdown\n{inner}markdown\n## 予期せぬ見出し\n{inner}\n{outer}"),
            }
        )
        plan = _write_plan(plans, "nested-fence.md", content)
        result = _run(
            {
                "session_id": "plan-nested-fence",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    def test_html_comment_h2_is_ignored(self, tmp_path: pathlib.Path):
        """複数行 HTML コメント内の `## 見出し` は見出しとしてカウントしない。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(
            overrides={
                "背景": ("<!--\n## ダミー\nコメントなので無視される想定。\n-->"),
            }
        )
        plan = _write_plan(plans, "html-comment.md", content)
        result = _run(
            {
                "session_id": "plan-html-comment",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize("closer", ["---", "..."])
    def test_frontmatter_h2_is_ignored(self, tmp_path: pathlib.Path, closer: str):
        """ファイル先頭 YAML フロントマター内の `## 見出し` は見出しとしてカウントしない。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(prefix=f"---\ntitle: sample\nnote: |\n  ## ダミー\n{closer}\n\n")
        plan = _write_plan(plans, "frontmatter.md", content)
        result = _run(
            {
                "session_id": "plan-frontmatter",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    def test_skipped_when_skill_not_invoked(self, tmp_path: pathlib.Path):
        """``plan_mode_skill_invoked`` 未設定時は plan file の構造検査をスキップする。

        PreToolUse 側で plan-mode スキル先行呼び出しが既に促されているため、
        構造検査の二重警告を避ける。
        """
        home, plans = self._home(tmp_path)
        # 必須セクションが欠落した plan を書いても、フラグ未設定なら警告しない。
        content = "# タイトル\n\n## 背景\n\n説明。\n"
        plan = _write_plan(plans, "no-skill.md", content)
        result = _run(
            {
                "session_id": "plan-no-skill",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        assert result.returncode == 0
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
    """期待セクション一覧が`plan-mode/references/plan-file-guidelines.md`に全て登場することを検査する。"""

    def test_required_and_optional_h2_appear_in_plan_file_ref(self):
        text = _PLAN_FILE_REF.read_text(encoding="utf-8")
        # 必須H2は全てplan-file-guidelines.md内の記述例とセクション定義に登場する。
        for heading in _PLAN_REQUIRED_H2:
            assert f"## {heading}" in text, f"plan-file-guidelines.md に `## {heading}` が無い"

    def test_section_definition_order_matches_required_h2(self):
        """`plan-file-guidelines.md`のセクション定義H3と`_PLAN_REQUIRED_H2`の順序が一致することを検査する。

        セクション定義H3は`### XXX（`## YYY`）`形式で記述されており、
        バッククォート内のH2名（YYY）が登場順に`_PLAN_REQUIRED_H2`と完全一致するべき。
        記述例コードブロック内のH2や、サブH3定義（`### XXX（`### YYY`）`形式）は
        パターン上マッチしないため誤検出しない。
        """
        text = _PLAN_FILE_REF.read_text(encoding="utf-8")
        # 行頭H3のうち、丸括弧内のインラインコードがH2（`## ...`）形式のものだけ抽出。
        pattern = re.compile(r"^### .+?（`## ([^`]+)`）", re.MULTILINE)
        defined_h2 = tuple(pattern.findall(text))
        assert defined_h2 == _PLAN_REQUIRED_H2, (
            f"plan-file-guidelines.md のセクション定義順 {defined_h2} が _PLAN_REQUIRED_H2 {_PLAN_REQUIRED_H2} と一致しない"
        )
