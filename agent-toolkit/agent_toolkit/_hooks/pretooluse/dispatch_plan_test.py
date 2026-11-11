# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""dispatch_planの契約テスト。"""

import pathlib

import pytest

from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._testing.dispatch_support import _existing_work_contract as _existing_work_contract
from agent_toolkit._testing.pretooluse_support import (
    _VALID_H2_PLAN_CONTENT,
    _make_plan_file,
    _plan_file_state_env,
    _user_facing_payload,
    _write_session_state,
)
from agent_toolkit._testing.pretooluse_support import run_after_read_delivery as _run


class TestAtkContractBeforeQuestion:
    """`atk`サブコマンドを主題とする確認を遮断しない。

    受理形式の事前確認は`agent-toolkit/share/rules/common/02-agent-operations.md`「ツール・コマンド運用」が定め、
    hookの遮断では強制しない。
    """

    _SUBCOMMAND = "atk wi process-loop abort"

    def _payload(self, field: str, session_id: str) -> dict:
        payload = _user_facing_payload(field, f"`{self._SUBCOMMAND}`の扱いを選んでください。")
        payload["session_id"] = session_id
        return payload

    @pytest.mark.parametrize("field", ["question", "header", "label", "description"])
    def test_unobserved_subcommand_is_not_blocked(self, tmp_path: pathlib.Path, field: str) -> None:
        """未観測のサブコマンド名を含む確認も遮断しない。"""
        result = _run(self._payload(field, f"contract-{field}"), env_overrides=_plan_file_state_env(tmp_path))

        assert result.returncode == 0
        assert result.stderr == ""

    def test_question_without_subcommand_is_not_blocked(self, tmp_path: pathlib.Path) -> None:
        """サブコマンド名を含まない確認は遮断しない。"""
        payload = _user_facing_payload("question", "process-loopの扱いを選んでください。")
        payload["session_id"] = "contract-absent"

        result = _run(payload, env_overrides=_plan_file_state_env(tmp_path))

        assert result.returncode == 0
        assert result.stderr == ""

    def test_exit_plan_mode_is_not_blocked(self, tmp_path: pathlib.Path) -> None:
        """計画本文も遮断しない。"""
        payload = {
            "tool_name": "ExitPlanMode",
            "tool_input": {"plan": f"`{self._SUBCOMMAND}`でprocess-loopを止める。"},
            "session_id": "contract-plan",
        }

        result = _run(payload, env_overrides=_plan_file_state_env(tmp_path))

        assert result.returncode == 0
        assert result.stderr == ""


class TestPlanModeSkillCallSites:
    """write-planスキルを呼び出すときに遮断も警告も返さないことの確認。

    write-plan起動時の計画単位リセット以外の委譲状態を参照せず、`returncode`は0を保つ。
    """

    _state_env = staticmethod(_plan_file_state_env)

    @pytest.fixture(autouse=True)
    def _isolate_read_guidance(self, monkeypatch):
        """計画modeの判定を、別契約の資料候補通知から分離する。"""
        monkeypatch.setattr(pretooluse, "read_delivery", lambda *_args, **_kwargs: Decision())

    @pytest.mark.parametrize("skill_name", ["agent-toolkit:write-plan", "write-plan"])
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
                "tool_input": {"skill": "agent-toolkit:write-plan"},
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
        """資料未読のWriteを警告しない。"""
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
        assert "textlint-violations.md" not in result.stderr

    def test_edit_without_read_does_not_warn(self, tmp_path: pathlib.Path):
        """資料未読のEditを警告しない。"""
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
        assert "textlint-violations.md" not in result.stderr

    def test_legacy_read_flag_does_not_change_result(self, tmp_path: pathlib.Path):
        """旧読了フラグが残るセッションでも計画編集を妨げない。"""
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

    def test_allows_non_plan_file_edit_without_read(self, tmp_path: pathlib.Path):
        """plan file以外の編集はフラグ未設定でも通過する。"""
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
        assert result.stdout == ""
