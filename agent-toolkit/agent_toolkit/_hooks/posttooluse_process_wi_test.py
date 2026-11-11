# pylint: disable=protected-access
"""posttooluse_process_wiの契約テスト。"""

import json
import os
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.posttooluse_support import _run, _state_path


class TestAwiSkillFlags:
    """自動振り返りの起点となるスキル呼び出しの状態フラグ記録。"""

    @pytest.mark.parametrize(
        ("skill", "flag"),
        [
            ("agent-toolkit:process-wi", "process_wi_skill_invoked"),
            ("process-wi", "process_wi_skill_invoked"),
        ],
    )
    def test_skill_records_flag(self, tmp_path: pathlib.Path, skill: str, flag: str) -> None:
        sid = f"fb-{skill.replace(':', '-')}"
        _run({"session_id": sid, "tool_name": "Skill", "tool_input": {"skill": skill}}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get(flag) is True


class TestExitSessionResetsProcessAwisFlag:
    """終了CLIの機械可読な応答で自動振り返り起点フラグをリセットする。"""

    @staticmethod
    def _invoke(session_id: str, state_dir: pathlib.Path, command: str = "atk agents-exit-session") -> None:
        result = _run(
            {
                "session_id": session_id,
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_response": {"stdout": '{"exit_session_invoked":true,"status":"unsupported"}'},
            },
            state_dir=state_dir,
        )
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr, result.stderr

    def test_reset_when_exit_session_invoked(self, tmp_path: pathlib.Path) -> None:
        sid = "exit-cli"
        # 事前に自動振り返り起点フラグを立てる。
        _state_path(sid).write_text(
            json.dumps(
                {
                    "process_wi_skill_invoked": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._invoke(sid, tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("process_wi_skill_invoked") is False
        assert state.get("autonomous_exit_invoked") is True

    def test_reset_idempotent_when_already_false(self, tmp_path: pathlib.Path) -> None:
        """既に偽の状態でも終了CLIの記録だけを追加する。"""
        sid = "exit-idem"
        _state_path(sid).write_text(
            json.dumps({"process_wi_skill_invoked": False}, ensure_ascii=False),
            encoding="utf-8",
        )
        self._invoke(sid, tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("process_wi_skill_invoked") is False
        assert state.get("autonomous_exit_invoked") is True

    def test_no_rewrite_when_exit_and_reset_state_is_already_complete(self, tmp_path: pathlib.Path) -> None:
        """進捗起点と終了CLI記録とリセット済み状態がそろう場合は再書き込みしない。"""
        sid = "exit-no-rewrite"
        path = session_state.state_path(sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "autonomous_exit_invoked": True,
                    "process_wi_skill_invoked": False,
                    "marker": "keep",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # 終了処理と独立した進捗起点の初回記録を、測定の前に通常のhook経由で済ませる。
        observed = _run({"session_id": sid, "tool_name": "Read", "tool_input": {}}, state_dir=tmp_path)
        assert observed.returncode == 0, observed.stderr
        assert "Traceback" not in observed.stderr, observed.stderr
        # 更新精度が秒単位のファイルシステムでも、不要な書き込みを確実に検出する。
        os.utime(path, ns=(0, 0))
        mtime_before = path.stat().st_mtime_ns
        self._invoke(sid, tmp_path)
        assert _read_state(tmp_path, sid)["marker"] == "keep"
        assert path.stat().st_mtime_ns == mtime_before

    @pytest.mark.parametrize(
        ("command", "recorded"),
        [
            ("atk agents-exit-session 2>&1", True),
            ("atk agents-exit-session 2>/dev/null", True),
            ("atk agents-exit-session --x", False),
        ],
    )
    def test_redirection_does_not_count_as_argument(self, tmp_path: pathlib.Path, command: str, recorded: bool) -> None:
        """リダイレクトだけを伴う起動は単独の起動として記録し、引数を伴う起動は記録しない。"""
        sid = "exit-redirect"
        self._invoke(sid, tmp_path, command)
        assert (_read_state(tmp_path, sid).get("autonomous_exit_invoked") is True) is recorded


class TestProcessAwisInvokedNonIdempotent:
    """process-wiスキル再起動時のフラグ強制上書き。"""

    def test_reset_and_reinvoke_sets_flag_true(self, tmp_path: pathlib.Path) -> None:
        """終了CLI起動後の再起動でフラグが確実にTrueへ戻る。"""
        sid = "reinvoke"
        # 事前にフラグを立てる。
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:process-wi"},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_wi_skill_invoked") is True
        # 終了CLI起動でリセット。
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "atk agents-exit-session"},
                "tool_response": {"stdout": '{"exit_session_invoked":true,"status":"unsupported"}'},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_wi_skill_invoked") is False
        # 再起動でTrueへ確実に戻ることを確認する。
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:process-wi"},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_wi_skill_invoked") is True
