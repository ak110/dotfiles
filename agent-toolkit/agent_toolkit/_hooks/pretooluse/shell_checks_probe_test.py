# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""shell_checks_probeの契約テスト。"""

import json
import pathlib
import re

import pytest

from agent_toolkit._testing.pretooluse_support import _plan_file_state_env, _write_session_state
from agent_toolkit._testing.shell_checks_support import _existing_work_contract as _existing_work_contract
from agent_toolkit._testing.shell_checks_support import _run_in_ready_context


class TestIssSidechainProbe:
    """`_record_iss_sidechain_probe`によるisSidechain実値採取デバッグログ（FB7対応）。"""

    _state_env = staticmethod(_plan_file_state_env)
    _write_state = staticmethod(_write_session_state)

    @staticmethod
    def _log_path(tmp_path: pathlib.Path, session_id: str) -> pathlib.Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
        return tmp_path / f"claude-agent-toolkit-issidechain-{safe}.log"

    def test_os_error_is_swallowed_and_execution_continues(self, tmp_path: pathlib.Path):
        """ログ出力先をディレクトリにして`OSError`を起こしても処理を継続する。"""
        env = self._state_env(tmp_path)
        session_id = "probe-oserror"
        log_path = self._log_path(tmp_path, session_id)
        log_path.mkdir()
        result = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "os-error-probe", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": session_id,
                "isSidechain": True,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert log_path.is_dir()
        assert not list(log_path.iterdir())

    def test_rotates_when_log_exceeds_one_megabyte(self, tmp_path: pathlib.Path):
        """ログファイルが1MB超過時に`_file_lock.rotate_if_needed`経由で`.1`世代ファイルへローテートされる。"""
        env = self._state_env(tmp_path)
        log_path = self._log_path(tmp_path, "probe-rotate")
        log_path.write_text("x" * (1_000_001), encoding="utf-8")
        result = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe-rotate",
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        rotated = log_path.with_suffix(log_path.suffix + ".1")
        assert rotated.exists()
        assert len(rotated.read_text(encoding="utf-8")) == 1_000_001
        assert len(log_path.read_text(encoding="utf-8").splitlines()) == 1

    @pytest.mark.parametrize(
        "operation,sidechain",
        [
            ("start", {}),
            ("start", {"isSidechain": False}),
            ("start", {"isSidechain": True}),
            ("start", {"isSidechain": "yes"}),
            ("send_message", {"isSidechain": False}),
        ],
    )
    def test_public_operation_records_complete_probe(self, tmp_path: pathlib.Path, operation: str, sidechain: dict):
        """公開起動・継続で1行を保存し、欠落・真偽・非booleanの値と全記録項目を保持する。"""
        env = self._state_env(tmp_path)
        session = "probe-fields"
        self._write_state(
            tmp_path,
            session,
            {
                "current_plan_file_path": "/tmp/plan.md",
                "agents_server_cwd_by_session": {"abc": "/tmp/workdir"},
            },
        )
        tool = f"mcp__plugin_agent-toolkit_agents_server__{operation}"
        inputs = (
            {"session_id": "abc", "prompt": "next"}
            if operation == "send_message"
            else {
                "prompt": "hello",
                "sandbox": "danger-full-access",
                "cwd": "/tmp/workdir",
            }
        )
        result = _run_in_ready_context(
            {
                "tool_name": tool,
                "tool_input": inputs,
                "session_id": session,
                "transcript_path": "/tmp/transcript.jsonl",
                "cwd": "/tmp/workdir",
                **sidechain,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        lines = self._log_path(tmp_path, session).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["isSidechain"] == sidechain.get("isSidechain")
        assert type(entry["isSidechain"]) is type(sidechain.get("isSidechain"))
        assert entry["session_id"] == session
        assert entry["tool_name"] == tool
        assert entry["transcript_path"] == "/tmp/transcript.jsonl"
        assert entry["cwd"] == "/tmp/workdir"
        assert entry["current_plan_file_path"] == "/tmp/plan.md"
