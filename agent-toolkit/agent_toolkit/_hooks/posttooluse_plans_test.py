# pylint: disable=protected-access
"""posttooluse_plansの契約テスト。"""

import json
import pathlib
import shlex

import pytest

from agent_toolkit._testing.helpers import _read_state, auto_message_opening_attributes
from agent_toolkit._testing.posttooluse_support import _run


class TestCurrentPlanFilePathTracking:
    """plan file編集時の`current_plan_file_path`記録。

    pretooluse.py側の遡及スキャンの記録とprocess7の完了を確かめる処理が
    計画ファイル本文を再読み込みする際に使う。
    """

    def test_write_records_current_plan_file_path(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plans_dir = home / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan_path = plans_dir / "sample.md"
        sid = "plan-path-write"
        _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            home_dir=home,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("current_plan_file_path") == str(plan_path)

    def test_create_plan_files_bash_records_current_plan_file_path(self, tmp_path: pathlib.Path) -> None:
        """`create_plan_files.py`のBash標準出力からも現在の計画パスを記録する。"""
        home = tmp_path / "home"
        plans_dir = home / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan_path = plans_dir / "15-0713_process-wi_レーン01.md"
        sid = "plan-path-bash"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "uv run --project /plugin /plugin/skills/write-plan/scripts/create_plan_files.py"},
                "tool_response": {"stdout": f"{plan_path}\n"},
            },
            state_dir=tmp_path,
            home_dir=home,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("current_plan_file_path") == str(plan_path)

    @pytest.mark.parametrize("scenario", ["other-command", "no-plan-path"])
    def test_bash_without_plan_output_keeps_current_plan_file_path(self, tmp_path: pathlib.Path, scenario: str) -> None:
        """`create_plan_files.py`を含まない実行と、計画ファイルを出力しない実行では記録を変えない。"""
        home = tmp_path / "home"
        (home / ".claude" / "plans").mkdir(parents=True)
        sid = f"plan-path-bash-{scenario}"
        command = (
            "echo done"
            if scenario == "other-command"
            else "uv run --project /plugin /plugin/skills/write-plan/scripts/create_plan_files.py"
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_response": {"stdout": "計画ファイルを作成できません\n"},
            },
            state_dir=tmp_path,
            home_dir=home,
        )
        state = _read_state(tmp_path, sid)
        assert "current_plan_file_path" not in state

    def test_private_notes_write_records_current_plan_file_path(self, tmp_path: pathlib.Path) -> None:
        """新しいprivate-notes計画rootのWriteも現在の計画パスとして記録する。"""
        private_notes = tmp_path / "private-notes"
        plans_dir = private_notes / "plans" / "2026" / "08"
        plans_dir.mkdir(parents=True)
        plan_path = plans_dir / "30-計画保存先移行-a1b2.md"
        sid = "private-notes-plan-path-write"
        _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            extra_env={"AGENT_TOOLKIT_PRIVATE_NOTES": str(private_notes)},
        )
        state = _read_state(tmp_path, sid)
        assert state.get("current_plan_file_path") == str(plan_path)

    def test_non_plan_file_write_does_not_record(self, tmp_path: pathlib.Path):
        sid = "plan-path-non-plan"
        _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "a.py"), "content": "x"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert "current_plan_file_path" not in state


class TestDetailCurrentPlanPathTracking:
    """詳細計画の編集が既存の現在計画パス契約を変えないことを検証する。"""

    def test_detail_file_write_does_not_record_current_plan_file_path(self, tmp_path: pathlib.Path):
        """計画ファイル（詳細）は計画ファイル（メイン）を判定する述語が偽を返すため、現在値を記録しない。"""
        home = tmp_path / "home"
        plans_dir = home / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        detail_path = plans_dir / "sample.detail.md"
        sid = "plan-path-detail-write"
        _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(detail_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            home_dir=home,
        )
        state = _read_state(tmp_path, sid)
        assert "current_plan_file_path" not in state


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
        result = _run({"session_id": "silent", "tool_input": {"command": "pytest"}}, state_dir=tmp_path)
        assert result.stdout == ""


class TestPlanFilePostWriteNotice:
    """計画ファイルのWrite成功時に書き込み後チェック案内をhookSpecificOutput経由で返す挙動。"""

    def _make_plan_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        home = tmp_path / "home"
        plans = home / ".claude" / "plans"
        plans.mkdir(parents=True)
        return plans / "sample.md"

    def test_notice_emitted_on_plan_file_write(self, tmp_path: pathlib.Path) -> None:
        plan_path = self._make_plan_path(tmp_path)
        sid = "post-write-notice"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            home_dir=plan_path.parents[2],
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        message = payload["hookSpecificOutput"]["additionalContext"]
        assert "書き込み後に計画の構造を確かめ" in message
        assert "check_plan_file.py" in message
        assert auto_message_opening_attributes(message)["source"] == "posttooluse"

    def test_plan_file_write_notice_is_executable_as_written(self, tmp_path: pathlib.Path) -> None:
        """案内文がそのまま実行できる形であること。

        案内文を受け取った側はこれをシェルで実行する。実行するシェルの環境に
        `${CLAUDE_PLUGIN_ROOT}`は存在しないため、スクリプトは絶対パスで示す。
        照会先を指定しない場合は実行時の作業ディレクトリを使い、対象リポジトリと一致する保証がないため、
        payloadの`cwd`を`--work-dir`へ明示する。
        """
        plan_path = self._make_plan_path(tmp_path)
        work_dir = tmp_path / "target repo"
        work_dir.mkdir()
        result = _run(
            {
                "session_id": "post-write-notice-executable",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# x\n"},
                "cwd": str(work_dir),
            },
            state_dir=tmp_path,
            home_dir=plan_path.parents[2],
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        message = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "CLAUDE_PLUGIN_ROOT" not in message
        expected_script = pathlib.Path(__file__).resolve().parents[2] / "skills/write-plan/scripts/check_plan_file.py"
        assert str(expected_script) in message
        # 空白を含むパスは引用しないと単語分割され、意図しない引数として渡る。
        assert f"--work-dir {shlex.quote(str(work_dir))}" in message

    def test_notice_on_removed_detail_file_write_is_skipped(self, tmp_path: pathlib.Path) -> None:
        """廃止した`.detail.md`への書込みを現行計画として判定しない。"""
        plan_path = self._make_plan_path(tmp_path)
        detail_path = plan_path.with_name("sample.detail.md")
        sid = "post-write-notice-detail"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(detail_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            home_dir=plan_path.parents[2],
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_notice_skipped_when_plan_mode_not_invoked(self, tmp_path: pathlib.Path) -> None:
        plan_path = self._make_plan_path(tmp_path)
        sid = "post-write-no-write-plan"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            home_dir=plan_path.parents[2],
        )
        assert result.returncode == 0
        assert not result.stdout

    def test_no_notice_on_non_plan_file_write(self, tmp_path: pathlib.Path) -> None:
        sid = "post-write-non-plan"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "a.py"), "content": "x"},
            },
            state_dir=tmp_path,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert not result.stdout

    def test_no_notice_on_plan_file_edit(self, tmp_path: pathlib.Path) -> None:
        plan_path = self._make_plan_path(tmp_path)
        plan_path.write_text("# t\n", encoding="utf-8")
        sid = "post-write-edit"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Edit",
                "tool_input": {"file_path": str(plan_path), "old_string": "t", "new_string": "u"},
            },
            state_dir=tmp_path,
            plan_mode_skill_invoked=True,
            home_dir=plan_path.parents[2],
        )
        assert result.returncode == 0
        assert not result.stdout

    def test_no_notice_on_sidecar_file_write(self, tmp_path: pathlib.Path) -> None:
        home = tmp_path / "home"
        plans = home / ".claude" / "plans"
        plans.mkdir(parents=True)
        sidecar = plans / "sample.review.md"
        sid = "post-write-sidecar"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(sidecar), "content": "# x\n"},
            },
            state_dir=tmp_path,
            plan_mode_skill_invoked=True,
            home_dir=home,
        )
        assert result.returncode == 0
        assert not result.stdout
