# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""dispatch_planの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._testing.dispatch_support import _existing_work_contract as _existing_work_contract
from agent_toolkit._testing.pretooluse_support import (
    _VALID_H2_PLAN_CONTENT,
    _make_plan_file,
    _plan_file_state_env,
    _run,
    _user_facing_payload,
    _write_session_state,
)


class TestAtkContractBeforeQuestion:
    """`atk`サブコマンドを主題とする確認を遮断しない。

    受理形式の事前確認は入口の運用の規定（`atk-mode`の`references/operations.md`）が定め、
    hookの遮断では強制しない。
    """

    _SUBCOMMAND = "atk wi process-loop abort"

    def _payload(self, field: str, session_id: str) -> dict:
        payload = _user_facing_payload(field, f"`{self._SUBCOMMAND}`の扱いを選んでください。")
        payload["session_id"] = session_id
        return payload

    @pytest.mark.parametrize("field", ["question", "header", "label", "description"])
    def test_unobserved_subcommand_is_not_blocked(self, tmp_path: pathlib.Path, field: str) -> None:
        result = _run(self._payload(field, f"contract-{field}"), env_overrides=_plan_file_state_env(tmp_path))

        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert result.stderr == ""

    def test_question_without_subcommand_is_not_blocked(self, tmp_path: pathlib.Path) -> None:
        payload = _user_facing_payload("question", "process-loopの扱いを選んでください。")
        payload["session_id"] = "contract-absent"

        result = _run(payload, env_overrides=_plan_file_state_env(tmp_path))

        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert result.stderr == ""

    def test_exit_plan_mode_is_not_blocked(self, tmp_path: pathlib.Path) -> None:
        payload = {
            "tool_name": "ExitPlanMode",
            "tool_input": {"plan": f"`{self._SUBCOMMAND}`でprocess-loopを止める。"},
            "session_id": "contract-plan",
        }

        result = _run(payload, env_overrides=_plan_file_state_env(tmp_path))

        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert result.stderr == ""


class TestPlanModeSkillCallSites:
    _state_env = staticmethod(_plan_file_state_env)

    @pytest.mark.parametrize("skill_name", ["agent-toolkit:atk-mode", "atk-mode"])
    def test_allowed_outside_plan_mode(self, tmp_path: pathlib.Path, skill_name: str):
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": skill_name},
                "session_id": "outside-plan",
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_allowed_in_plan_mode(self, tmp_path: pathlib.Path):
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:atk-mode", "args": "write-plan"},
                "session_id": "inside-plan",
                "permission_mode": "plan",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_other_skills_unaffected_outside_plan_mode(self, tmp_path: pathlib.Path):
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-basics:writing-standards"},
                "session_id": "other-skill",
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert result.stdout == ""


class TestPlanFileDoesNotRequireTextlintRead:
    """計画を編集する前に文章lintの資料を読み終えることを求めた条件が撤去済みであることを検証する。

    `permission_mode`の値に依らず、新旧計画root配下の`*.md`に対する
    Write/Edit/MultiEditのみが警告対象となる。plan file以外の操作は
    一切ブロック・警告しない。完成条件を満たさない状態での次工程移行の抑止は
    `ExitPlanMode`のブロックへ集約する。
    """

    _state_env = staticmethod(_plan_file_state_env)
    _make_plan = staticmethod(_make_plan_file)

    @staticmethod
    def _prior_flags(tmp_path: pathlib.Path, session_id: str, **flags: object) -> None:
        _write_session_state(
            tmp_path,
            session_id,
            dict(flags),
        )

    def test_write_without_read_does_not_warn(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "req-both-unread"
        self._prior_flags(tmp_path, sid, plan_mode_skill_invoked=True)
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": "# t\n"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert "textlint-violations.md" not in (result.stdout + result.stderr)

    def test_edit_without_read_does_not_warn(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home, "edit.md")
        env = self._state_env(tmp_path, home)
        sid = "req-textlint-unread"
        self._prior_flags(tmp_path, sid, plan_mode_skill_invoked=True)
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(plan), "old_string": "a", "new_string": "b"},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert "textlint-violations.md" not in (result.stdout + result.stderr)

    def test_legacy_read_flag_does_not_change_result(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plan = self._make_plan(home)
        env = self._state_env(tmp_path, home)
        sid = "req-read"
        self._prior_flags(
            tmp_path,
            sid,
            plan_mode_skill_invoked=True,
            textlint_violations_read=True,
        )
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": _VALID_H2_PLAN_CONTENT},
                "session_id": sid,
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"

    def test_allows_non_plan_file_edit_without_read(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        home.mkdir()
        env = self._state_env(tmp_path, home)
        self._prior_flags(tmp_path, "req-other-file")
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "x.md"), "content": "# t\n"},
                "session_id": "req-other-file",
                "permission_mode": "default",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert result.stdout == ""
