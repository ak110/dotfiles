# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""shell_checks_mcpの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._testing.pretooluse_support import _plan_file_state_env, _write_session_state
from agent_toolkit._testing.shell_checks_support import _existing_work_contract as _existing_work_contract
from agent_toolkit._testing.shell_checks_support import _run_in_ready_context


class TestAgentsServerInputChecks:
    """基準の取得後に、agents_serverの入力の妥当性を判定する。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    @pytest.mark.parametrize("host", ["claude-code", "codex"])
    @pytest.mark.parametrize(
        ("tool_suffix", "tool_input", "remote_session_id"),
        [
            pytest.param(
                "start",
                {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                None,
                id="start",
            ),
            pytest.param("send_message", {"prompt": "hello", "session_id": "remote"}, "remote", id="send_message"),
            pytest.param("kill", {"session_id": "remote"}, "remote", id="kill"),
        ],
    )
    def test_allowed_with_skill_ready(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        tool_suffix: str,
        tool_input: dict,
        remote_session_id: str | None,
        host: str,
    ) -> None:
        """基準の取得後は、入力の妥当性の判定を通過すれば許可する。"""
        session_id = f"without-skill-{tool_suffix}"
        if remote_session_id is not None:
            _write_session_state(
                tmp_path,
                session_id,
                {"agents_server_cwd_by_session": {remote_session_id: "/tmp/workdir"}},
            )
        result = _run_in_ready_context(
            {
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{tool_suffix}",
                "tool_input": tool_input,
                "session_id": session_id,
                **({"turn_id": "codex-turn"} if host == "codex" else {}),
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        if host == "codex":
            assert not result.stdout
        else:
            out = json.loads(result.stdout)
            assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_allowed_when_sidechain_with_skill_ready(self, state_dir: dict[str, str]) -> None:
        """サイドチェーンでは明示Skill起動記録を要求しない。"""
        result = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "sidechain-invoked",
                "isSidechain": True,
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestCodexMcpExecution:
    """codex MCP sandbox明示指定の強制・approval-policy自動修正（CLI統合テスト）。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    def test_sandbox_unspecified_blocked(self, state_dir: dict[str, str]):
        """sandbox未指定の開始ツール呼び出しは入力を補正せずに通過させる。"""
        result = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "cwd": "/tmp/workdir"},
                "session_id": "fix1",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert "updatedInput" not in out["hookSpecificOutput"]
        assert "systemMessage" not in out

    @pytest.mark.parametrize("sandbox", ["network-only", "read-only", "workspace-write"])
    def test_sandbox_other_values_blocked(self, sandbox: str, state_dir: dict[str, str]):
        """`danger-full-access`以外のsandbox指定も入力を補正せずに通過させる。"""
        result = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": sandbox, "cwd": "/tmp/workdir"},
                "session_id": "fix2",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert "updatedInput" not in out["hookSpecificOutput"]
        assert "systemMessage" not in out

    def test_sandbox_blocked_in_sidechain(self, state_dir: dict[str, str]):
        """サブエージェント内部からの呼び出しでもsandbox指定を補正せずに通過させる。"""
        result = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "read-only", "cwd": "/tmp/workdir"},
                "session_id": "fix_side",
                "isSidechain": True,
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert "updatedInput" not in out["hookSpecificOutput"]
        assert "systemMessage" not in out

    def test_sandbox_correct_no_message(self, state_dir: dict[str, str]):
        """sandbox・approval-policyが共に省略時の値と一致する場合、updatedInputは返すがsystemMessageを含めない。"""
        result = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {
                    "prompt": "hello",
                    "sandbox": "danger-full-access",
                    "approval-policy": "never",
                    "cwd": "/tmp/workdir",
                },
                "session_id": "fix3",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "updatedInput" not in out["hookSpecificOutput"]
        assert "systemMessage" not in out

    def test_approval_policy_wrong_value_auto_fix_with_correct_sandbox(self, state_dir: dict[str, str]):
        """sandboxが正しい値でもapproval-policyのみ誤りなら単独でneverへ強制修正する。"""
        result = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {
                    "prompt": "hello",
                    "sandbox": "danger-full-access",
                    "approval-policy": "on-request",
                    "cwd": "/tmp/workdir",
                },
                "session_id": "fix_ap",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert "updatedInput" not in out["hookSpecificOutput"]
        assert "systemMessage" not in out


class TestCheckCodexMcpCwd:
    """開始時`cwd`は入力の妥当性を判定する実行基盤へ委ねる。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    @pytest.mark.parametrize("cwd", [None, "", "   ", "relative/path", "/tmp/worktree"])
    def test_start_input_is_allowed(self, state_dir: dict[str, str], cwd: str | None) -> None:
        """開始入力は`cwd`の値にかかわらずフックを通過する。"""
        tool_input = {"prompt": "hello", "sandbox": "danger-full-access"}
        if cwd is not None:
            tool_input["cwd"] = cwd
        result = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": tool_input,
                "session_id": f"cwd-{cwd}",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestCodexMcpReply:
    """Codex継続用MCP toolの強制承認。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    @pytest.mark.parametrize(
        ("tool_name", "prompt"),
        [
            ("mcp__plugin_agent-toolkit_agents_server__send_message", "next"),
            ("mcp__plugin_agent-toolkit_agents_server__send_message", "追加指示"),
            ("mcp__plugin_agent-toolkit_agents_server__kill", ""),
        ],
    )
    def test_continuation_auto_approved(self, state_dir: dict[str, str], tmp_path: pathlib.Path, tool_name: str, prompt: str):
        """Codex継続用MCP toolが入力の妥当性を確かめた後に強制承認される。"""
        _write_session_state(
            tmp_path,
            "reply1",
            {"agents_server_cwd_by_session": {"abc": "/tmp"}},
        )
        result = _run_in_ready_context(
            {
                "tool_name": tool_name,
                "tool_input": {"session_id": "abc", **({"prompt": prompt} if prompt else {})},
                "session_id": "reply1",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestMcpLanguageBoundary:
    """MCPの承認は保存済みtranscriptの言語を問わず、現在の応答はmodが判定する。"""

    _state_env = staticmethod(_plan_file_state_env)
    _write_state = staticmethod(_write_session_state)

    @staticmethod
    def _write_transcript(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
        entry = {
            "type": "assistant",
            "message": {
                "id": "m-lang",
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
            },
        }
        path = tmp_path / "transcript.jsonl"
        path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    @pytest.mark.parametrize("codex", [False, True])
    def test_start_permission_follows_host_language_boundary(self, tmp_path: pathlib.Path, codex: bool):
        """同じMCP開始でも言語判定は呼出元ホストの契約に従う。"""
        env = self._state_env(tmp_path)
        transcript = self._write_transcript(tmp_path, "A" * 100)
        result = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                **({"turn_id": "codex-turn"} if codex else {}),
                "transcript_path": str(transcript),
                "session_id": "codex-lang",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert not result.stderr
        if codex:
            assert not result.stdout
        else:
            assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
        self._write_transcript(tmp_path, "根拠の確認が完了しました。")
        approved = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "調査する", "cwd": str(tmp_path)},
                "transcript_path": str(transcript),
                "session_id": "codex-lang",
                **({"turn_id": "codex-turn"} if codex else {}),
            },
            env_overrides=env,
        )
        assert approved.returncode == 0
        if codex:
            assert approved.stdout == ""
        else:
            assert json.loads(approved.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"

    @pytest.mark.parametrize("codex", [False, True])
    def test_continuation_permission_follows_host_language_boundary(self, tmp_path: pathlib.Path, codex: bool):
        """所有記録のある継続操作でも保存済みの言語を判定せず、ホスト別の承認を保つ。"""
        env = self._state_env(tmp_path)
        _write_session_state(
            tmp_path,
            "reply-lang",
            {"agents_server_cwd_by_session": {"abc": "/tmp"}},
        )
        transcript = self._write_transcript(tmp_path, "A" * 100)
        result = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
                "tool_input": {"session_id": "abc", "prompt": "next"},
                **({"turn_id": "codex-turn"} if codex else {}),
                "transcript_path": str(transcript),
                "session_id": "reply-lang",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        assert not result.stderr
        if codex:
            assert not result.stdout
        else:
            assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
        self._write_transcript(tmp_path, "根拠の確認が完了しました。")
        approved = _run_in_ready_context(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
                "tool_input": {"session_id": "abc", "prompt": "続ける"},
                "transcript_path": str(transcript),
                "session_id": "reply-lang",
                **({"turn_id": "codex-turn"} if codex else {}),
            },
            env_overrides=env,
        )
        assert approved.returncode == 0
        if codex:
            assert approved.stdout == ""
        else:
            assert json.loads(approved.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
