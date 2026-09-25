# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-position,undefined-variable
"""agent-toolkit/agent_toolkit/_hooks/pretooluse/agent_checks.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

import ast
import json
import os
import pathlib
import subprocess
import tempfile
import textwrap
import time
from collections.abc import Callable

import pytest
from pyfltr.colloquial import check as _colloquial_check

from agent_toolkit import hook
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._hooks.pretooluse.test_support_test import *  # noqa: F403
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE


@pytest.mark.parametrize("tool_input", [{"session_id": "owned"}, {"prompt": "続行する"}])
def test_agents_server_missing_required_input_warns(tool_input: dict[str, str], tmp_path: pathlib.Path) -> None:
    """必須値を欠く継続入力はツールの検証へ渡し、遮断しない。"""
    result = _run(
        {
            "tool_name": "mcp__agents_server__send_message",
            "tool_input": tool_input,
            "session_id": "missing-input",
            "cwd": str(tmp_path),
        }
    )
    assert result.returncode == 0
    assert "空でない" in _agent_messages(result)


class TestAgentNameParameterAccepted:
    """`name`引数を伴うAgent/Task起動が委譲ゲートで拒否されないことを保証する（`name`禁止規程撤回の受理契約）。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    @pytest.mark.parametrize("name_value", ["impl-1", "", None])
    def test_name_parameter_does_not_block(self, tmp_path: pathlib.Path, tool_name: str, name_value: str | None) -> None:
        """`name`キーの値によらず、他の委譲ゲートを満たす起動はブロックされない。"""
        sid = f"agent-name-accept-{tool_name.lower()}-{name_value!r}"
        result = _run(
            {
                "tool_name": tool_name,
                "tool_input": {"subagent_type": "claude", "name": name_value, "prompt": "調査してください。"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert "`name`" not in result.stderr


class TestTaskStopBlock:
    """`TaskStop`を自セッションの所有記録又は停滞検知完了記録へ限定する。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    @staticmethod
    def _invoke(session_id: str, env: dict[str, str], tool_input: dict | None = None) -> subprocess.CompletedProcess[str]:
        payload = {
            "tool_name": "TaskStop",
            "tool_input": tool_input if tool_input is not None else {},
            "session_id": session_id,
        }
        return _run(payload, env_overrides=env)

    def test_unowned_target_is_blocked_on_every_call(self, state_dir: dict[str, str]) -> None:
        """所有記録が無い対象は再実行しても通さない。"""
        first = self._invoke("task-stop-unowned", state_dir, {"task_id": "other-task"})
        second = self._invoke("task-stop-unowned", state_dir, {"task_id": "other-task"})

        assert first.returncode == 2
        assert second.returncode == 2
        assert first.stderr == second.stderr
        assert "所有記録に一致する識別子" in first.stderr
        assert "対象別の停滞検知完了記録を作成" in first.stderr
        assert "再実行すると続行できる" not in first.stderr

    def test_block_message_states_the_stop_conditions(self, state_dir: dict[str, str]) -> None:
        """遮断文面が停止の根拠、その完了条件の所在、不十分な理由、確認手段、再実行方法を示す。"""
        stderr = self._invoke("task-stop-message", state_dir).stderr
        assert "現在のセッションには" in stderr
        assert "所有記録も停滞検知完了記録も無い" in stderr
        assert "明示的な即時停止要求" in stderr
        assert "停滞検知の手順" in stderr
        assert "進行が遅い" in stderr
        assert "AskUserQuestionで確認" in stderr
        assert "references/waiting-and-monitoring.md" in stderr
        assert "「停滞の検知と巻き取り」節" in stderr

    def test_block_message_defaults_to_additional_instructions_and_limits_stopping(self, state_dir: dict[str, str]) -> None:
        """遮断文面が利用者介入時の追加指示既定と停止限定条件を示す。"""
        stderr = self._invoke("task-stop-message-route", state_dir).stderr
        assert "既定では稼働中の委譲先へ追加指示" in stderr
        assert "委譲範囲または前提を無効" in stderr
        assert "継続すると誤った成果物が確定" in stderr
        assert "`agent-toolkit:delegation`「継続と新規起動」" in stderr

    @pytest.mark.parametrize(
        ("label", "tool_input"),
        [
            ("empty", {}),
            ("task-id", {"task_id": "task-1"}),
            ("shell-id", {"shell_id": "shell-1"}),
        ],
    )
    def test_result_does_not_depend_on_stop_target(
        self,
        state_dir: dict[str, str],
        label: str,
        tool_input: dict,
    ) -> None:
        """自セッションの起動記録が無い停止は、識別子の有無と値によらず繰り返し遮断する。"""
        session_id = f"task-stop-input-{label}"
        assert self._invoke(session_id, state_dir, tool_input).returncode == 2
        assert self._invoke(session_id, state_dir, tool_input).returncode == 2

    @pytest.mark.parametrize(
        ("label", "tool_input"),
        [
            ("task-id", {"task_id": "bg-task-1"}),
            ("shell-id", {"shell_id": "bg-task-1"}),
        ],
    )
    def test_self_started_background_task_passes_without_block(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        label: str,
        tool_input: dict,
    ) -> None:
        """自セッションが起動した背景タスクの停止は初回から通す。"""
        session_id = f"task-stop-self-{label}"
        _write_session_state(tmp_path, session_id, {"background_task_ids": ["bg-task-1"]})
        assert self._invoke(session_id, state_dir, tool_input).returncode == 0

    @pytest.mark.parametrize("event_name", ["PostToolUse", "PostToolUseFailure"])
    def test_structured_background_response_allows_only_its_task_stop(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        event_name: str,
    ) -> None:
        """構造化応答から記録した所有IDだけを同じセッションの停止対象として通す。"""
        session_id = f"task-stop-structured-{event_name}"
        recorded = _run_posttooluse(
            {
                "session_id": session_id,
                "hook_event_name": event_name,
                "tool_name": "Bash",
                "tool_input": {"command": "sleep 120", "run_in_background": True},
                "tool_response": {
                    "backgroundTaskId": "bg-task-1",
                    "interrupted": False,
                    "isImage": False,
                    "noOutputExpected": False,
                    "stderr": "",
                    "stdout": "",
                },
            },
            state_dir,
        )

        assert recorded.returncode == 0
        assert _read_session_state(tmp_path, session_id).get("background_task_ids") == ["bg-task-1"]
        assert self._invoke(session_id, state_dir, {"task_id": "bg-task-2"}).returncode == 2
        assert self._invoke(session_id, state_dir, {"task_id": "bg-task-1"}).returncode == 0

    def test_timeout_notice_allows_only_its_task_stop(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """実行上限による背景移行通知を所有記録から停止許可まで渡す。"""
        session_id = "task-stop-timeout-notice"
        notice = "Command did not complete within its 15s timeout and was moved to the background (ID: bgm3jt6xn)."
        recorded = _run_posttooluse(
            {
                "session_id": session_id,
                "tool_name": "Bash",
                "tool_input": {"command": "sleep 120"},
                "tool_response": notice,
            },
            state_dir,
        )
        assert recorded.returncode == 0
        assert _read_session_state(tmp_path, session_id).get("background_task_ids") == ["bgm3jt6xn"]
        assert self._invoke(session_id, state_dir, {"task_id": "other-task"}).returncode == 2
        assert self._invoke(session_id, state_dir, {"task_id": "bgm3jt6xn"}).returncode == 0

    def test_other_task_is_blocked_even_with_recorded_background_tasks(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """記録に無いタスクの停止は、他の背景タスクを記録済みでも遮断する。"""
        session_id = "task-stop-other-target"
        _write_session_state(tmp_path, session_id, {"background_task_ids": ["bg-task-1"]})
        assert self._invoke(session_id, state_dir, {"task_id": "bg-task-2"}).returncode == 2

    def test_recent_stall_detection_allows_only_matching_task(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """5分以内の停滞検知完了記録は一致する対象だけを初回から通す。"""
        session_id = "task-stop-stall-match"
        _write_session_state(
            tmp_path,
            session_id,
            {"stall_detection_completed_at_by_task": {"bg-task-1": time.time()}},
        )
        assert self._invoke(session_id, state_dir, {"task_id": "bg-task-1"}).returncode == 0
        assert self._invoke(session_id, state_dir, {"task_id": "bg-task-2"}).returncode == 2

    def test_stale_stall_detection_does_not_allow_task(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """5分を超えた停滞検知完了記録は停止根拠として使わない。"""
        session_id = "task-stop-stall-stale"
        _write_session_state(
            tmp_path,
            session_id,
            {"stall_detection_completed_at_by_task": {"bg-task-1": time.time() - 600}},
        )
        assert self._invoke(session_id, state_dir, {"task_id": "bg-task-1"}).returncode == 2
        assert "stall_detection_completed_at_by_task" not in _read_session_state(tmp_path, session_id)


class TestExecuteReviewAlternateRouteAllowed:
    """`execute_review_model`が指すengineによらず実行レビューのAgent起動が通過する。"""

    @pytest.mark.parametrize("task_name", _EXECUTE_REVIEW_TASK_NAMES)
    def test_codex_setting_allows_sidechain_agent(self, tmp_path: pathlib.Path, task_name: str) -> None:
        """可用性起因の代替としてClaude経路へ切り替えた実行レビュー起動を遮断しない。"""
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": f"{task_name}を読んでレビューする。"},
                "session_id": "execute-review-codex",
                "isSidechain": True,
            },
            env_overrides=_stage_model_env(tmp_path, "codex:gpt-5.6-sol/high"),
        )
        assert result.returncode == 0
        assert "blocked:" not in result.stderr
        assert "execute_review_model" not in result.stderr

    def test_claude_setting_allows_sidechain_agent(self, tmp_path: pathlib.Path) -> None:
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "prompt": "exec-review.subagent.mdを読んでレビューする。",
                },
                "session_id": "execute-review-claude",
                "isSidechain": True,
            },
            env_overrides=_stage_model_env(tmp_path, "claude:sonnet/medium"),
        )
        assert result.returncode == 0

    def test_codex_setting_allows_main_session_agent(self, tmp_path: pathlib.Path) -> None:
        session_id = "execute-review-main"
        env = _stage_model_env(tmp_path, "codex:gpt-5.6-sol/medium")
        env.update(_plan_file_state_env(tmp_path))
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "prompt": "exec-review.subagent.mdを読んでレビューする。",
                },
                "session_id": session_id,
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_guarded_task_references_exist(self) -> None:
        """回帰検査が与えるタスク文書名の実在を確認し、改名による空振りを検出する。"""
        for task_name in _EXECUTE_REVIEW_TASK_NAMES:
            assert (_SHARE_DIR / task_name).is_file()


class TestPlanFileDoesNotRequireSelfPath:
    """計画自身のパス照合が撤去済みであることを検証する。"""

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    @staticmethod
    def _prior_flags(tmp_path: pathlib.Path, session_id: str, _content: str) -> None:
        _write_session_state(
            tmp_path,
            session_id,
            {
                "plan_mode_skill_invoked": True,
            },
        )

    def test_recorded_path_difference_does_not_warn(self, tmp_path: pathlib.Path):
        """記録パス値とWrite先が異なっても警告しない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-mismatch"
        wrong_path = str(tmp_path / "scratchpad" / "other.md")
        content = _path_section_build_content(wrong_path)
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "trailing path section" not in result.stderr

    def test_allows_when_recorded_path_matches(self, tmp_path: pathlib.Path):
        """記録パス値とWrite先のfile_pathが一致する場合は通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-match"
        content = _path_section_build_content(str(plan))
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_non_plan_file_is_skipped(self, tmp_path: pathlib.Path):
        """plan fileでないパスへの書き込みは検査対象外。"""
        content = _path_section_build_content("/tmp/x.md")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": content},
                "session_id": "path-nonplan",
                "permission_mode": "default",
            },
        )
        assert result.returncode == 0

    def test_allows_when_recorded_value_is_placeholder(self, tmp_path: pathlib.Path):
        """パス節配下の値が絶対パス表記でない（`/`・`~`で始まらない）場合はプレースホルダーとみなし通過する。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-placeholder"
        content = _path_section_build_content("plan-path-here")
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0

    def test_allows_when_section_body_absent(self, tmp_path: pathlib.Path):
        """パス節が本文に存在しない場合は本検査の対象外として通過する（他検査でブロックされ得る）。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "path-nosection"
        content = (
            "## 概要\n\nx\n\n"
            "## 実装資料\n\n### 変更説明\n\nREADMEを更新する。\n\n"
            "## 完了条件\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\n\n"
        )
        self._prior_flags(tmp_path, sid, content)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        # 本検査は「該当節本文が空」の場合は対象外として通過する
        assert "trailing path section" not in result.stderr


class TestAgentTaskLaunchIndependence:
    """Agent／Task起動が委譲スキル状態から独立していることを確認する。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_main_launch_without_skill_is_allowed(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        sid = f"missing-{tool_name.lower()}"
        plan = _make_plan_file(tmp_path / "home", f"{tool_name.lower()}-missing.md")
        env = {**_plan_file_state_env(tmp_path), **_process_loop_log_env(tmp_path)}
        result = _run(
            {
                "session_id": sid,
                "tool_name": tool_name,
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "prompt": f"計画ファイル `{plan}` を実装する。",
                },
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert "agent-toolkit:delegation" not in result.stderr

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_main_launch_with_delegation_or_sidechain_passes(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        allowed = _run({"session_id": "ready", "tool_name": tool_name, "tool_input": {}}, env_overrides=env)
        sidechain = _run(
            {"session_id": "sidechain", "tool_name": tool_name, "tool_input": {}, "isSidechain": True},
            env_overrides=env,
        )
        assert allowed.returncode == 0
        assert sidechain.returncode == 0

    def test_claude_code_guide_without_delegation_passes(self, tmp_path: pathlib.Path) -> None:
        """公式資料照会専用エージェントも独立した入力検査後に許可する。"""
        result = _run(
            {
                "session_id": "guide-without-delegation",
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude-code-guide", "prompt": "公式資料を確認する。"},
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0


class TestWorkflowSkillInvocation:
    """工程スキル起動が委譲スキルの状態記録又は事前案内を発生させないことを確認する。"""

    @pytest.mark.parametrize(
        "skill_name",
        [
            "agent-toolkit:plan-mode",
            "plan-mode",
            "agent-toolkit:process-wi",
            "process-wi",
            "agent-toolkit:session-review",
            "session-review",
            "agent-toolkit:bugfix",
            "bugfix",
        ],
    )
    def test_no_delegation_notice_for_workflow_skill(self, tmp_path: pathlib.Path, skill_name: str) -> None:
        """工程スキルを起動しても委譲に関する追加出力を返さない。"""
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": skill_name},
                "session_id": "reminder-target",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_notice_for_other_skill(self, tmp_path: pathlib.Path) -> None:
        """委譲工程を定めないスキルも追加出力を返さない。"""
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:writing-standards"},
                "session_id": "reminder-other",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_no_notice_in_sidechain(self, tmp_path: pathlib.Path) -> None:
        """サイドチェーンでは案内を返さない。"""
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:plan-mode"},
                "session_id": "reminder-sidechain",
                "isSidechain": True,
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_agent_launch_after_workflow_skill_is_allowed(self, tmp_path: pathlib.Path) -> None:
        """工程スキル起動後のAgent起動も委譲スキル状態に依存しない。"""
        env = _plan_file_state_env(tmp_path)
        skill_result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:plan-mode"},
                "session_id": "reminder-then-agent",
            },
            env_overrides=env,
        )
        agent_result = _run(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": "調査する。"},
                "session_id": "reminder-then-agent",
            },
            env_overrides=env,
        )
        assert skill_result.stdout == ""
        assert agent_result.returncode == 0
        assert "agent-toolkit:delegation" not in agent_result.stderr
