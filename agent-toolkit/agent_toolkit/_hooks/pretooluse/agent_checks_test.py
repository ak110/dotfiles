"""agent-toolkit/agent_toolkit/_hooks/pretooluse/agent_checks.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

import json
import pathlib
import subprocess
import time

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import work_contract
from agent_toolkit._hooks.pretooluse import agent_checks
from agent_toolkit._testing.pretooluse_support import (
    _EXECUTE_REVIEW_TASK_NAMES,
    _additional_context,
    _agent_messages,
    _make_plan_file,
    _path_section_build_content,
    _plan_file_state_env,
    _process_loop_log_env,
    _read_session_state,
    _run,
    _run_posttooluse,
    _stage_model_env,
    _write_session_state,
    run_after_read_delivery,
)


@pytest.fixture(autouse=True)
def _existing_work_contract(tmp_path, monkeypatch):
    """エージェント操作固有の通知を、作業契約を作成済みの状態で比較する。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: エージェント操作の検証\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args, **_kwargs: path)


def _run_after_skill_acquisition(
    payload: dict, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """操作前の基準を共通配送で取得し、agent_checksが判定した再発行の結果を返す。"""
    return run_after_read_delivery(payload, env_overrides)


def _run_after_generic_reissue(payload: dict, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """汎用定義の初回拒否を確認し、同じ主体と入力で再発行した結果を返す。"""
    first = _run_after_skill_acquisition(payload, env_overrides)
    assert "組み込みの汎用定義" in _denial_reason(first)
    return _run(payload, env_overrides=env_overrides)


def _denial_reason(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    reason = output["permissionDecisionReason"]
    assert isinstance(reason, str) and reason
    return reason


@pytest.mark.parametrize(
    ("operation", "tool_input"),
    [
        ("send_message", {"session_id": "owned"}),
        ("send_message", {"prompt": "続行する"}),
        ("send_message", {"session_id": "", "prompt": "続行する"}),
        ("kill", {}),
    ],
)
def test_agents_server_format_errors_pass_without_warning(operation: str, tool_input: dict, tmp_path: pathlib.Path) -> None:
    """入力形式の不成立はhookが警告せずツールの入力検証へ渡す。

    hookが独自の必須条件で警告すると、ツールが受理する入力形式（定型継続など）を追加したときに
    正当な入力へ誤った修正案内を返す。原因と次の操作はツールのエラー応答が返す。
    """
    _write_session_state(tmp_path, "format-error", {"agents_server_cwd_by_session": {"owned": str(tmp_path)}})
    result = _run_after_skill_acquisition(
        {
            "tool_name": f"mcp__agents_server__{operation}",
            "tool_input": tool_input,
            "session_id": "format-error",
            "cwd": str(tmp_path),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    messages = _agent_messages(result)
    assert "空でない" not in messages
    assert "warn:" not in messages


@pytest.mark.parametrize(
    ("operation", "tool_input"),
    [
        ("send_message", {"session_id": "unsaved", "prompt": "続行する"}),
        ("send_message", {"session_id": "unsaved", "instruction": "保存開始", "extra_params": {"要求識別子": "r-1"}}),
        ("send_message", {"session_id": "unsaved", "instruction": "再レビューせよ", "extra_params": {"round": "2"}}),
        ("send_message", {"session_id": "unsaved"}),
        ("kill", {"session_id": "unsaved"}),
    ],
)
def test_agents_server_continuation_without_saved_cwd_is_blocked(
    operation: str, tool_input: dict, tmp_path: pathlib.Path
) -> None:
    """呼出元に対象の絶対cwdが保存されていない継続と中断は、入力形式によらず遮断する。"""
    _write_session_state(tmp_path, "unsaved-target", {"agents_server_cwd_by_session": {"other": str(tmp_path)}})
    result = _run_after_skill_acquisition(
        {
            "tool_name": f"mcp__agents_server__{operation}",
            "tool_input": tool_input,
            "session_id": "unsaved-target",
            "cwd": str(tmp_path),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    reason = output["permissionDecisionReason"]
    assert f"blocked: {operation}は、`session_id`に対応する絶対`cwd`が保存されていないため続行できない。" in reason
    assert "show" in reason
    assert "空でない" not in reason


@pytest.mark.parametrize("cwd", ["", "relative"])
def test_agents_server_invalid_saved_cwd_is_blocked(tmp_path: pathlib.Path, cwd: str) -> None:
    """空または相対cwdの記録を所有証拠として継続せず、show照会を案内する。"""
    _write_session_state(tmp_path, "invalid-cwd", {"agents_server_cwd_by_session": {"child": cwd}})
    result = _run(
        {"session_id": "invalid-cwd", "tool_name": "mcp__agents_server__kill", "tool_input": {"session_id": "child"}},
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "show" in output["permissionDecisionReason"]


class TestAgentNameParameterAccepted:
    """`name`引数を伴うAgent/Task起動が委譲の可否判定で拒否されないことを保証する（`name`の指定を禁じた規程を撤回した後の受理契約）。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    @pytest.mark.parametrize("name_value", ["impl-1", "", None])
    def test_name_parameter_does_not_block(self, tmp_path: pathlib.Path, tool_name: str, name_value: str | None) -> None:
        """`name`キーの値によらず、委譲に必要な他の条件を満たす起動はブロックされない。"""
        sid = f"agent-name-accept-{tool_name.lower()}-{name_value!r}"
        result = _run_after_generic_reissue(
            {
                "tool_name": tool_name,
                "tool_input": {"subagent_type": "claude", "name": name_value, "prompt": "調査してください。"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert "`name`" not in result.stderr


class TestTaskStopBlock:
    """`TaskStop`を自セッションのバックグラウンドタスクの所有記録または停滞検知完了記録へ限定する。"""

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
        """バックグラウンドタスクの所有記録が無い対象は再実行しても通さない。"""
        first = self._invoke("task-stop-unowned", state_dir, {"task_id": "other-task"})
        second = self._invoke("task-stop-unowned", state_dir, {"task_id": "other-task"})

        reason = _denial_reason(first)
        assert reason == _denial_reason(second)
        assert "バックグラウンドタスクの所有記録に一致する識別子" in reason
        assert "対象別の停滞検知完了記録を作成" in reason
        assert "再実行すると続行できる" not in reason

    def test_block_message_states_the_stop_conditions(self, state_dir: dict[str, str]) -> None:
        """遮断文面が停止の根拠、その完了条件の所在、不十分な理由、確認手段、再実行方法を示す。"""
        reason = _denial_reason(self._invoke("task-stop-message", state_dir))
        assert "現在のセッションには" in reason
        assert "バックグラウンドタスクの所有記録も停滞検知完了記録も無い" in reason
        assert "明示的な即時停止要求" in reason
        assert "停滞検知の手順" in reason
        assert "references/waiting-and-monitoring.md" in reason
        assert "「停滞の検知と巻き取り」節" in reason

    def test_block_message_refers_user_intervention_to_owner_section(self, state_dir: dict[str, str]) -> None:
        """遮断文面がユーザー介入時の扱いを所有者の節（delegation「継続と新規起動」）へ委ねる。"""
        reason = _denial_reason(self._invoke("task-stop-message-route", state_dir))
        assert "ユーザーの介入があった場合の扱いは`agent-toolkit:delegation`「継続と新規起動」が定める" in reason

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
        _denial_reason(self._invoke(session_id, state_dir, tool_input))
        _denial_reason(self._invoke(session_id, state_dir, tool_input))

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
        """自セッションが起動したバックグラウンドタスクの停止は初回から通す。"""
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
        _denial_reason(self._invoke(session_id, state_dir, {"task_id": "bg-task-2"}))
        assert self._invoke(session_id, state_dir, {"task_id": "bg-task-1"}).returncode == 0

    @pytest.mark.parametrize(
        ("label", "phrase"),
        [
            ("mcp-notice", "moved to the background as task {task_id} and keeps running"),
            ("timed-out", "Command timed out and is now running in the background. ID: {task_id}"),
            (
                "did-not-complete",
                "Command did not complete within its 15s timeout and was moved to the background (ID: {task_id}).",
            ),
        ],
    )
    @pytest.mark.parametrize("source", ["bash-stdout", "mcp-grep", "agents-server"])
    def test_foreground_notice_text_does_not_allow_task_stop(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        source: str,
        label: str,
        phrase: str,
    ) -> None:
        """前景の出力本文の途中に移行通知の文言を含む応答のIDは所有記録へ追加せず、そのIDへの停止を遮断する。"""
        session_id = f"task-stop-foreground-{source}-{label}"
        quoted = f"background_tasks_test.py:1: {phrase.format(task_id='quoted-task-1')}"
        if source == "bash-stdout":
            tool_name = "Bash"
            tool_input: dict = {"command": "rg -n background background_tasks_test.py"}
            tool_response: object = {
                "stdout": quoted,
                "stderr": "",
                "interrupted": False,
                "isImage": False,
                "noOutputExpected": False,
            }
        else:
            tool_name = (
                "mcp__plugin_agent-toolkit_pyfltr__grep"
                if source == "mcp-grep"
                else "mcp__plugin_agent-toolkit_agents_server__show"
            )
            tool_input = {"session_id": "remote-1"} if source == "agents-server" else {"pattern": "background"}
            tool_response = [{"type": "text", "text": quoted}]
        recorded = _run_posttooluse(
            {
                "session_id": session_id,
                "hook_event_name": "PostToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_response": tool_response,
            },
            state_dir,
        )

        assert recorded.returncode == 0
        state_file = session_state.state_path(session_id)
        state = _read_session_state(tmp_path, session_id) if state_file.exists() else {}
        assert "quoted-task-1" not in (state.get("background_task_ids") or [])
        _denial_reason(self._invoke(session_id, state_dir, {"task_id": "quoted-task-1"}))

    def test_host_mcp_notice_allows_only_its_task_stop(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """ホストのMCP移行通知で始まる応答のIDは所有記録へ追加し、そのIDの停止だけを通す。"""
        session_id = "task-stop-host-mcp-notice"
        notice = (
            'MCP tool "mcp__plugin_agent-toolkit_agents_server__wait" is still running after 120s.'
            " It was moved to the background as task mcp-task-1 and keeps running;"
        )
        recorded = _run_posttooluse(
            {
                "session_id": session_id,
                "hook_event_name": "PostToolUse",
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__wait",
                "tool_input": {},
                "tool_response": [{"type": "text", "text": notice}],
            },
            state_dir,
        )
        assert recorded.returncode == 0
        assert _read_session_state(tmp_path, session_id).get("background_task_ids") == ["mcp-task-1"]
        _denial_reason(self._invoke(session_id, state_dir, {"task_id": "other-task"}))
        assert self._invoke(session_id, state_dir, {"task_id": "mcp-task-1"}).returncode == 0

    def test_structured_timeout_response_allows_only_its_task_stop(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """`run_in_background`なしのBashが時間切れで背景へ移った構造化応答もバックグラウンドタスクの所有記録へ渡す。"""
        session_id = "task-stop-structured-timeout"
        recorded = _run_posttooluse(
            {
                "session_id": session_id,
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "rg -c pattern | sort"},
                "tool_response": {
                    "backgroundTaskId": "bo9oa1l0a",
                    "timedOutAfterMs": 120000,
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
        assert _read_session_state(tmp_path, session_id).get("background_task_ids") == ["bo9oa1l0a"]
        _denial_reason(self._invoke(session_id, state_dir, {"task_id": "other-task"}))
        assert self._invoke(session_id, state_dir, {"task_id": "bo9oa1l0a"}).returncode == 0

    def test_agent_async_launch_allows_only_its_task_stop(
        self,
        state_dir: dict[str, str],
    ) -> None:
        """Agentの背景起動が返した`agentId`の停止だけを通し、記録の無い`agentId`は遮断する。"""
        session_id = "task-stop-agent-async"
        recorded = _run_posttooluse(
            {
                "session_id": session_id,
                "hook_event_name": "PostToolUse",
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": "調査する", "run_in_background": True},
                "tool_response": {"isAsync": True, "status": "async_launched", "agentId": "a8d4542d5607ca48d"},
            },
            state_dir,
        )

        assert recorded.returncode == 0
        _denial_reason(self._invoke(session_id, state_dir, {"task_id": "b0000000000000000"}))
        assert self._invoke(session_id, state_dir, {"task_id": "a8d4542d5607ca48d"}).returncode == 0

    def test_foreground_output_mentioning_background_id_is_not_ownership(
        self,
        state_dir: dict[str, str],
    ) -> None:
        """前景実行の出力本文に背景起動の文言が現れてもバックグラウンドタスクの所有記録にしない。"""
        session_id = "task-stop-foreground-text"
        recorded = _run_posttooluse(
            {
                "session_id": session_id,
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "cat log.txt"},
                "tool_response": {"stdout": "Command running in background with ID: other-task", "stderr": ""},
            },
            state_dir,
        )

        assert recorded.returncode == 0
        _denial_reason(self._invoke(session_id, state_dir, {"task_id": "other-task"}))

    def test_other_task_is_blocked_even_with_recorded_background_tasks(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
    ) -> None:
        """記録に無いタスクの停止は、他のバックグラウンドタスクを記録済みでも遮断する。"""
        session_id = "task-stop-other-target"
        _write_session_state(tmp_path, session_id, {"background_task_ids": ["bg-task-1"]})
        _denial_reason(self._invoke(session_id, state_dir, {"task_id": "bg-task-2"}))

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
        _denial_reason(self._invoke(session_id, state_dir, {"task_id": "bg-task-2"}))

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
        _denial_reason(self._invoke(session_id, state_dir, {"task_id": "bg-task-1"}))
        assert "stall_detection_completed_at_by_task" not in _read_session_state(tmp_path, session_id)


class TestExecuteReviewAlternateRouteAllowed:
    """`review_engine`が指すengineによらず実行レビューのAgent起動が通過する。"""

    @pytest.mark.parametrize("task_name", _EXECUTE_REVIEW_TASK_NAMES)
    def test_codex_setting_allows_sidechain_agent(self, tmp_path: pathlib.Path, task_name: str) -> None:
        """可用性起因の代替としてClaudeによる実行へ切り替えた実行レビュー起動を遮断しない。"""
        result = _run_after_generic_reissue(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": f"{task_name}を読んでレビューする。"},
                "session_id": "execute-review-codex",
                "isSidechain": True,
            },
            env_overrides=_stage_model_env(tmp_path, "codex,claude"),
        )
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert "blocked:" not in result.stderr
        assert "review_engine" not in result.stderr

    def test_claude_setting_allows_sidechain_agent(self, tmp_path: pathlib.Path) -> None:
        result = _run_after_generic_reissue(
            {
                "tool_name": "Agent",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "prompt": "exec-review.subagent.mdを読んでレビューする。",
                },
                "session_id": "execute-review-claude",
                "isSidechain": True,
            },
            env_overrides=_stage_model_env(tmp_path, "claude,codex"),
        )
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"

    def test_codex_setting_allows_main_session_agent(self, tmp_path: pathlib.Path) -> None:
        session_id = "execute-review-main"
        env = _stage_model_env(tmp_path, "codex")
        env.update(_plan_file_state_env(tmp_path))
        result = _run_after_generic_reissue(
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
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"


class TestPlanFileDoesNotRequireSelfPath:
    """計画自身のパスとの一致を判定しないことを検証する。"""

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

    @pytest.mark.parametrize("recorded", ["different", "matching", "placeholder"])
    def test_recorded_path_value_does_not_block(self, tmp_path: pathlib.Path, recorded: str):
        """記録パスの一致・不一致・プレースホルダーで計画編集を妨げない。"""
        home = tmp_path / "home"
        plan = self._make_plan(home)
        sid = "path-value"
        value = {"different": str(tmp_path / "scratchpad/other.md"), "matching": str(plan), "placeholder": "plan-path-here"}[
            recorded
        ]
        content = _path_section_build_content(value)
        self._prior_flags(tmp_path, sid, content)
        result = _run_after_skill_acquisition(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=self._state_env(tmp_path, home),
        )
        assert result.returncode == 0 and not result.stderr
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert "trailing path section" not in _agent_messages(result)

    def test_non_plan_file_is_skipped(self, tmp_path: pathlib.Path):
        """plan fileでないパスへ書き込む場合は判定しない。"""
        content = _path_section_build_content("/tmp/x.md")
        self._prior_flags(tmp_path, "path-nonplan", content)
        result = _run_after_skill_acquisition(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": content},
                "session_id": "path-nonplan",
                "permission_mode": "default",
            },
            env_overrides=self._state_env(tmp_path),
        )
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"

    def test_allows_when_section_body_absent(self, tmp_path: pathlib.Path):
        """パス節が本文に存在しない場合はこの判定では通過する（他の条件でブロックされ得る）。"""
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
        result = _run_after_skill_acquisition(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        # 該当節本文が空の場合は、この判定では通過する
        assert "trailing path section" not in result.stderr


class TestAgentTaskLaunchIndependence:
    """Agent／Task起動が基準の配送後に許可されることを確認する。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_main_launch_after_delivery_is_allowed(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        sid = f"missing-{tool_name.lower()}"
        plan = _make_plan_file(tmp_path / "home", f"{tool_name.lower()}-missing.md")
        env = {**_plan_file_state_env(tmp_path), **_process_loop_log_env(tmp_path)}
        result = _run_after_generic_reissue(
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
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert "agent-toolkit:delegation" not in result.stderr

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_main_launch_with_delegation_or_sidechain_passes(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        env = {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
        allowed = _run_after_generic_reissue(
            {"session_id": "ready", "tool_name": tool_name, "tool_input": {}}, env_overrides=env
        )
        sidechain = _run_after_generic_reissue(
            {"session_id": "sidechain", "tool_name": tool_name, "tool_input": {}, "isSidechain": True},
            env_overrides=env,
        )
        assert allowed.returncode == 0
        specific = json.loads(allowed.stdout).get("hookSpecificOutput", {}) if allowed.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert sidechain.returncode == 0
        specific = json.loads(sidechain.stdout).get("hookSpecificOutput", {}) if sidechain.stdout else {}
        assert specific.get("permissionDecision") != "deny"

    def test_claude_code_guide_without_delegation_passes(self, tmp_path: pathlib.Path) -> None:
        """公式資料照会専用エージェントも入力が条件を満たすか独立して判定した後に許可する。"""
        result = _run_after_skill_acquisition(
            {
                "session_id": "guide-without-delegation",
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude-code-guide", "prompt": "公式資料を確認する。"},
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"


class TestGenericAgentLaunchBlock:
    """公開hookから汎用定義の遮断・再発行と対象外の無通知を確認する。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    @pytest.mark.parametrize("agent_type", ["general-purpose", "Explore", "Plan", "web-fetch", "claude", None, ""])
    def test_generic_definition_is_blocked_then_reissue_passes(
        self, tmp_path: pathlib.Path, tool_name: str, agent_type: str | None
    ) -> None:
        tool_input = {"prompt": "https://example.com/ の本文を取得して要約する。"}
        if agent_type is not None:
            tool_input["subagent_type"] = agent_type
        payload = {"session_id": f"generic-{tool_name}-{agent_type}", "tool_name": tool_name, "tool_input": tool_input}
        env = _plan_file_state_env(tmp_path)
        reason = _denial_reason(_run_after_skill_acquisition(payload, env_overrides=env))
        assert f"組み込みの汎用定義`{agent_type or 'general-purpose'}`" in reason
        assert "同等の作業を行える`mode`" in reason
        mode = "`explore`" if agent_type in {"Explore", "web-fetch"} else "`delegate`または役割文書のある`task`"
        assert f"`mode`が{mode}の`agents_server`の`start`へ発行せよ" in reason
        assert "`MCP`ツールを呼べない場合" in reason
        assert "ユーザーの明示指示がある場合" in reason
        assert "対応する`mode`で起動したが同等の結果を得られなかった場合" in reason
        repeated = _run(payload, env_overrides=env)
        assert repeated.returncode == 0, repeated.stderr
        assert not repeated.stderr
        assert "deny" not in repeated.stdout
        assert "組み込みの汎用定義" not in _additional_context(repeated)
        _denial_reason(_run(payload, env_overrides=env))

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    @pytest.mark.parametrize(
        ("agent_type", "launch_kind"),
        [
            ("fork", ""),
            ("claude-code-guide", ""),
            ("project-reviewer", ""),
            ("Explore", "explore"),
            ("general-purpose", "shell"),
            ("web-fetch", "write"),
        ],
    )
    def test_fork_dedicated_and_lightweight_are_not_blocked(
        self, tmp_path: pathlib.Path, tool_name: str, agent_type: str, launch_kind: str
    ) -> None:
        result = _run_after_skill_acquisition(
            {
                "session_id": f"dedicated-{agent_type}",
                "tool_name": tool_name,
                "tool_input": {"subagent_type": agent_type, "prompt": "担当の作業を進める。"},
            },
            env_overrides={**_plan_file_state_env(tmp_path), "AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND": launch_kind},
        )
        assert result.returncode == 0
        assert not result.stderr
        assert "deny" not in result.stdout
        assert "組み込みの汎用定義" not in _additional_context(result)

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    @pytest.mark.parametrize("agent_type", [None, 0, ["general-purpose"]])
    def test_non_string_type_is_not_blocked(self, tmp_path: pathlib.Path, tool_name: str, agent_type: object) -> None:
        """明示した文字列以外の`subagent_type`は汎用定義と判定できないため、遮断も通知もしない。"""
        result = _run_after_skill_acquisition(
            {
                "session_id": f"non-string-{tool_name}-{agent_type!r}",
                "tool_name": tool_name,
                "tool_input": {"subagent_type": agent_type, "prompt": "担当の作業を進める。"},
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert not result.stderr
        assert "組み込みの汎用定義" not in result.stdout

    @pytest.mark.parametrize("failure", ["missing-session", "unwritable-state"])
    def test_unrecordable_reissue_passes_with_warning(self, monkeypatch, failure: str) -> None:
        """再発行の記録を保存できない起動は、何度発行しても遮断せず警告だけを返す。"""
        monkeypatch.setattr(agent_checks, "update_state", lambda *_args, **_kwargs: False)
        payload = {"session_id": "" if failure == "missing-session" else "generic-unwritable"}
        tool_input = {"subagent_type": "Explore", "prompt": "担当の作業を進める。"}
        for _ in range(2):
            # 状態の保存の失敗は公開hookから注入できないため、判定の関数を直接呼ぶ。
            block, notice = agent_checks._check_generic_agent_launch(  # pylint: disable=protected-access
                payload, "Agent", tool_input
            )
            assert block is None
            assert notice is not None and "遮断せずに通した" in notice and "`explore`" in notice


class TestWorkflowSkillInvocation:
    """`agent-toolkit:delegation`以外のスキルの起動が、`agent-toolkit:delegation`の状態記録または事前案内を発生させないことを確認する。"""

    @pytest.mark.parametrize(
        "skill_name",
        [
            "agent-toolkit:write-plan",
            "write-plan",
            "agent-toolkit:process-wi",
            "process-wi",
            "agent-toolkit:review-session",
            "review-session",
            "agent-basics:bugfix",
            "bugfix",
        ],
    )
    def test_no_delegation_notice_for_workflow_skill(self, tmp_path: pathlib.Path, skill_name: str) -> None:
        """`agent-toolkit:delegation`以外のスキルを起動しても委譲に関する追加出力を返さない。"""
        result = _run_after_skill_acquisition(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": skill_name},
                "session_id": "reminder-target",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert "/skills/delegation/SKILL.md" not in _additional_context(result)

    def test_no_notice_for_other_skill(self, tmp_path: pathlib.Path) -> None:
        """Skill取得時に条件候補・委譲に関する追加出力を返さない。"""
        result = _run_after_skill_acquisition(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-basics:writing-standards"},
                "session_id": "reminder-other",
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        context = _additional_context(result)
        assert not context
        assert "/skills/delegation/SKILL.md" not in _additional_context(result)

    def test_no_notice_in_sidechain(self, tmp_path: pathlib.Path) -> None:
        """サイドチェーンでは委譲の案内を返さない。"""
        result = _run_after_skill_acquisition(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:write-plan"},
                "session_id": "reminder-sidechain",
                "isSidechain": True,
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert "/skills/delegation/SKILL.md" not in _additional_context(result)

    def test_agent_launch_after_workflow_skill_is_allowed(self, tmp_path: pathlib.Path) -> None:
        """`agent-toolkit:delegation`以外のスキルの起動後のAgent起動も、`agent-toolkit:delegation`の起動状態を問わない。"""
        env = _plan_file_state_env(tmp_path)
        skill_result = _run_after_skill_acquisition(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:write-plan"},
                "session_id": "reminder-then-agent",
            },
            env_overrides=env,
        )
        agent_result = _run_after_generic_reissue(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": "調査する。"},
                "session_id": "reminder-then-agent",
            },
            env_overrides=env,
        )
        assert skill_result.returncode == 0, skill_result.stderr
        assert "/skills/delegation/SKILL.md" not in _additional_context(skill_result)
        assert agent_result.returncode == 0
        specific = json.loads(agent_result.stdout).get("hookSpecificOutput", {}) if agent_result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert "agent-toolkit:delegation" not in agent_result.stderr


@pytest.mark.parametrize(
    "delegation_env",
    [{}, {"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}, {"AGENT_TOOLKIT_OWNER_SESSION": "owner-session"}],
    ids=["main", "delegated-flag", "owner-only"],
)
@pytest.mark.parametrize("codex", [False, True], ids=["claude-host", "codex-host"])
def test_language_boundary_uses_host_and_preserves_delegated_coverage(
    tmp_path: pathlib.Path, delegation_env: dict[str, str], codex: bool
) -> None:
    """全ホストのPreToolUseはtranscript言語判定を持たず、Claudeの実行前判定はmodが担う。"""
    entry = {
        "type": "assistant",
        "message": {
            "id": "m1",
            "role": "assistant",
            "content": [{"type": "text", "text": "The implementation is still in progress; I'll wait for the completion."}],
            "stop_reason": "end_turn",
        },
    }
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    env = {
        **_plan_file_state_env(tmp_path),
        "AGENT_TOOLKIT_DELEGATED_SESSION": "",
        "AGENT_TOOLKIT_OWNER_SESSION": "",
        **delegation_env,
    }
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "transcript_path": str(transcript),
        "session_id": "owner-session-only",
        **({"turn_id": "codex-turn"} if codex else {}),
    }

    result = _run(payload, env_overrides=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not result.stderr
