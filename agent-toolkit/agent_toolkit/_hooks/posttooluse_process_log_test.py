# pylint: disable=protected-access
"""posttooluse_process_logの契約テスト。"""

import pathlib

import pytest

from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.posttooluse_support import _run


class TestAgentsServerProcessLoopLog:
    """計画実行系`model_type`の`agents_server` sessionの起動時刻と終了時刻の記録。

    `model_type`は開始入力から解決するため、起動と終了を同じsessionへ通して記録の対応を確認する。
    """

    def _run_session(
        self,
        tmp_path: pathlib.Path,
        *,
        model_type: str,
        observe_tool: str = "kill",
        final_status: str = "completed",
        enable_env: bool = True,
        expected_session_id: str | None = None,
    ) -> str:
        xdg_state_home = tmp_path / "xdg-state"
        extra_env = {
            "XDG_STATE_HOME": str(xdg_state_home),
            "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1" if enable_env else "",
        }
        if expected_session_id is not None:
            extra_env["AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"] = expected_session_id
        sid = "process-loop"
        remote_session_id = "thread-process-loop"
        _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start_custom",
                "tool_input": {"prompt": "実装する", "model_type": model_type, "cwd": str(tmp_path)},
                "tool_response": {
                    "structuredContent": {
                        "session_id": remote_session_id,
                        "status": "running",
                    }
                },
            },
            state_dir=tmp_path,
            extra_env=extra_env,
        )
        _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{observe_tool}",
                "tool_input": {"session_id": remote_session_id},
                "tool_response": {"structuredContent": {"session_id": remote_session_id, "status": final_status}},
            },
            state_dir=tmp_path,
            extra_env=extra_env,
        )
        log_path = xdg_state_home / "agent-toolkit" / "process-wi.log"
        return log_path.read_text(encoding="utf-8") if log_path.exists() else ""

    @pytest.mark.parametrize("observe_tool", ("kill",))
    def test_terminal_status_logs_start_and_end(self, tmp_path: pathlib.Path, observe_tool: str) -> None:
        """観測対象の工程は起動時刻を記録し、終了した観測で終了時刻を記録する。"""
        text = self._run_session(tmp_path, model_type="execute", observe_tool=observe_tool)
        assert "event=subagent_start" in text
        assert "event=subagent_end" in text
        assert text.count("type=execute") == 2

    def test_untracked_model_type_is_not_logged(self, tmp_path: pathlib.Path) -> None:
        """探索起動など観測対象外の工程は記録しない。"""
        assert self._run_session(tmp_path, model_type="chores_fast") == ""

    def test_running_status_does_not_log_end(self, tmp_path: pathlib.Path) -> None:
        """終了していない観測では終了時刻を記録しない。"""
        text = self._run_session(tmp_path, model_type="execute", final_status="running")
        assert "event=subagent_start" in text
        assert "event=subagent_end" not in text

    def test_disabled_env_suppresses_logging(self, tmp_path: pathlib.Path) -> None:
        """process-loop起動セッション以外では記録しない。"""
        assert self._run_session(tmp_path, model_type="execute", enable_env=False) == ""

    def test_nested_session_id_suppresses_logging(self, tmp_path: pathlib.Path) -> None:
        """別会話が親の環境印を継承しても常駐ログへ書かない。"""
        assert self._run_session(tmp_path, model_type="execute", expected_session_id="parent") == ""

    def test_matching_session_id_logs(self, tmp_path: pathlib.Path) -> None:
        """親会話のIDに一致すると起動と終了を記録する。"""
        text = self._run_session(tmp_path, model_type="execute", expected_session_id="process-loop")
        assert "event=subagent_start" in text
        assert "event=subagent_end" in text


class TestRemovedRecordsAreAbsent:
    """撤去した判定処理へ渡していた状態と、常時規範の編集警告を記録・出力しないことを検証する。"""

    _REMOVED_KEYS = (
        "test_executed",
        "git_log_checked",
        "amend_pending_status_check",
        "session_edited_files",
        "atk_help_observed",
        "always_loaded_rule_before_sizes",
        "always_loaded_rule_added_texts",
    )

    def test_bash_and_edit_do_not_record_removed_state(self, tmp_path: pathlib.Path) -> None:
        sid = "removed-records"
        rule = pathlib.Path(__file__).resolve().parents[2] / "share" / "rules" / "common" / "01-agent.md"
        payloads: list[dict] = [
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "uv run pytest"}, "tool_response": {}},
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log -3"}, "tool_response": {}},
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git commit --amend --no-edit"},
                "tool_response": {},
            },
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "atk wi list --help"}, "tool_response": {}},
            {
                "session_id": sid,
                "tool_name": "Edit",
                "tool_input": {"file_path": str(rule), "old_string": "a", "new_string": "b"},
            },
        ]
        for payload in payloads:
            result = _run(payload, state_dir=tmp_path)

            assert result.returncode == 0
            if payload.get("tool_input", {}).get("command") == "git commit --amend --no-edit":
                assert "agent-toolkit:report-completion" in result.stdout
            else:
                assert result.stdout == ""
        state = _read_state(tmp_path, sid)
        assert not [key for key in self._REMOVED_KEYS if key in state]
