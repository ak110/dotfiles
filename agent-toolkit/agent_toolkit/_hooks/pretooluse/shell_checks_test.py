# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-position,undefined-variable
"""agent-toolkit/agent_toolkit/_hooks/pretooluse/shell_checks.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

import ast
import json
import os
import pathlib
import re
import shlex
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
from agent_toolkit._hooks.pretooluse import shell_checks
from agent_toolkit._hooks.pretooluse.test_support_test import *  # noqa: F403
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE, auto_message_opening_attributes


class TestAgentsServerInputChecks:
    """agents_serverの入力検査が委譲スキル状態から独立していることを確認する。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

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
    def test_allowed_without_skill_record(
        self,
        state_dir: dict[str, str],
        tmp_path: pathlib.Path,
        tool_suffix: str,
        tool_input: dict,
        remote_session_id: str | None,
    ) -> None:
        """専用スキルの起動記録が無くても独立した入力検査を通過すれば許可する。"""
        session_id = f"without-skill-{tool_suffix}"
        if remote_session_id is not None:
            _write_session_state(
                tmp_path,
                session_id,
                {"agents_server_cwd_by_session": {remote_session_id: "/tmp/workdir"}},
            )
        result = _run(
            {
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{tool_suffix}",
                "tool_input": tool_input,
                "session_id": session_id,
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_allowed_when_sidechain_without_skill_record(self, state_dir: dict[str, str]) -> None:
        """サイドチェーンでは明示Skill起動記録を要求しない。"""
        result = _run(
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
        """sandbox未指定の場合は`danger-full-access`へ自動補正する。"""
        result = _run(
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
        """`danger-full-access`以外のsandbox指定は自動補正する。"""
        result = _run(
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
        """サブエージェント内部からの呼び出しでもsandboxを自動補正する。"""
        result = _run(
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
        """sandbox・approval-policyが共に既定値の場合、updatedInputは返すがsystemMessageを含めない。"""
        result = _run(
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
        result = _run(
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
    """開始時`cwd`は実行基盤の入力検査へ委ねる。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    @pytest.mark.parametrize("cwd", [None, "", "   ", "relative/path", "/tmp/worktree"])
    def test_start_input_is_allowed(self, state_dir: dict[str, str], cwd: str | None) -> None:
        """開始入力は`cwd`の値にかかわらずフックを通過する。"""
        tool_input = {"prompt": "hello", "sandbox": "danger-full-access"}
        if cwd is not None:
            tool_input["cwd"] = cwd
        result = _run(
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
        """Codex継続用MCP toolが入力検査後に強制承認される。"""
        _write_session_state(
            tmp_path,
            "reply1",
            {"agents_server_cwd_by_session": {"abc": "/tmp"}},
        )
        result = _run(
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


class TestCodexMcpLanguageWarningMerge:
    """codex MCP強制承認時に保留言語警告が単一JSONへ統合されることを検証する。

    `flush_pending_notices()`を廃止し`emit_json()`単独で承認とadditionalContextを
    出力する回帰を防ぐ。stdoutが2件のJSONへ分裂しないこと・`additionalContext`に
    警告本文が統合されることを確認する。
    """

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

    def test_codex_merges_pending_language_warning(self, tmp_path: pathlib.Path):
        """mcp__plugin_agent-toolkit_agents_server__start分岐で保留警告が承認JSONへ統合される。"""
        env = self._state_env(tmp_path)
        transcript = self._write_transcript(tmp_path, "A" * 100)
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "transcript_path": str(transcript),
                "session_id": "codex-lang",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        # stdoutは単一JSONオブジェクトとしてパースできる（2件分裂していない）
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "英語主体" in out["hookSpecificOutput"]["additionalContext"]

    def test_codex_reply_merges_pending_language_warning(self, tmp_path: pathlib.Path):
        """mcp__plugin_agent-toolkit_agents_server__send_message分岐で保留警告が承認JSONへ統合される。"""
        env = self._state_env(tmp_path)
        _write_session_state(
            tmp_path,
            "reply-lang",
            {"agents_server_cwd_by_session": {"abc": "/tmp"}},
        )
        transcript = self._write_transcript(tmp_path, "A" * 100)
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
                "tool_input": {"session_id": "abc", "prompt": "next"},
                "transcript_path": str(transcript),
                "session_id": "reply-lang",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "英語主体" in out["hookSpecificOutput"]["additionalContext"]


class TestIssSidechainProbe:
    """`_record_iss_sidechain_probe`によるisSidechain実値採取デバッグログ（FB7対応）。"""

    _state_env = staticmethod(_plan_file_state_env)
    _write_state = staticmethod(_write_session_state)

    @staticmethod
    def _log_path(tmp_path: pathlib.Path, session_id: str) -> pathlib.Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
        return tmp_path / f"claude-agent-toolkit-issidechain-{safe}.log"

    def test_writes_one_jsonl_line_under_tempdir(self, tmp_path: pathlib.Path):
        """`tempfile.gettempdir()`起点、session_id含むパスへJSONL 1行を追記する。"""
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe1",
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        log_path = self._log_path(tmp_path, "probe1")
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

    def test_recorded_fields_include_expected_keys(self, tmp_path: pathlib.Path):
        """記録項目に`isSidechain`・`session_id`・`tool_name`・`transcript_path`・`cwd`・`current_plan_file_path`が含まれる。"""
        env = self._state_env(tmp_path)
        self._write_state(
            tmp_path,
            "probe2",
            {
                "current_plan_file_path": "/tmp/plan.md",
            },
        )
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe2",
                "isSidechain": False,
                "transcript_path": "/tmp/transcript.jsonl",
                "cwd": "/tmp/workdir",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        entry = json.loads(self._log_path(tmp_path, "probe2").read_text(encoding="utf-8").splitlines()[0])
        assert entry["isSidechain"] is False
        assert entry["session_id"] == "probe2"
        assert entry["tool_name"] == "mcp__plugin_agent-toolkit_agents_server__start"
        assert entry["transcript_path"] == "/tmp/transcript.jsonl"
        assert entry["cwd"] == "/tmp/workdir"
        assert entry["current_plan_file_path"] == "/tmp/plan.md"

    def test_iss_sidechain_absent_is_recorded_as_null(self, tmp_path: pathlib.Path):
        """`isSidechain`欠落時に`null`が記録される。"""
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe3",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        entry = json.loads(self._log_path(tmp_path, "probe3").read_text(encoding="utf-8").splitlines()[0])
        assert entry["isSidechain"] is None

    def test_iss_sidechain_non_boolean_is_recorded_as_is(self, tmp_path: pathlib.Path):
        """`isSidechain`が非boolean型（整数・文字列など）でもそのまま記録される。"""
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe4",
                "isSidechain": "yes",
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        entry = json.loads(self._log_path(tmp_path, "probe4").read_text(encoding="utf-8").splitlines()[0])
        assert entry["isSidechain"] == "yes"

    def test_os_error_is_swallowed_and_execution_continues(self, tmp_path: pathlib.Path):
        """ログ出力先をディレクトリにして`OSError`を起こしても処理を継続する。"""
        env = self._state_env(tmp_path)
        session_id = "probe-oserror"
        log_path = self._log_path(tmp_path, session_id)
        log_path.mkdir()
        result = _run(
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
        result = _run(
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

    def test_called_for_codex_reply_tool(self, tmp_path: pathlib.Path):
        """`mcp__plugin_agent-toolkit_agents_server__send_message`の呼び出し時にも本ヘルパーが呼ばれる。"""
        env = self._state_env(tmp_path)
        _write_session_state(
            tmp_path,
            "probe-reply",
            {"agents_server_cwd_by_session": {"abc": "/tmp"}},
        )
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
                "tool_input": {"session_id": "abc", "prompt": "next"},
                "session_id": "probe-reply",
                "isSidechain": False,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        log_path = self._log_path(tmp_path, "probe-reply")
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        assert entry["tool_name"] == "mcp__plugin_agent-toolkit_agents_server__send_message"

    def test_called_even_when_iss_sidechain_true(self, tmp_path: pathlib.Path):
        """`isSidechain=True`ケースでも本ヘルパーが呼ばれる（既存ゲートより前で実行される確認）。"""
        env = self._state_env(tmp_path)
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe-sidechain-true",
                "isSidechain": True,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        log_path = self._log_path(tmp_path, "probe-sidechain-true")
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        assert entry["isSidechain"] is True


class TestBashProcessKillByPattern:
    """`Bash`経由のパターン一致プロセス終了（`pkill`・`killall`）検出（block）。"""

    @pytest.mark.parametrize(
        "command",
        [
            'pkill -f "codex exec"',
            "killall python",
            "pkill node",
        ],
    )
    def test_blocks(self, command: str):
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert auto_message_opening_attributes(result.stderr)["source"] == "agent-toolkit/pretooluse"

    def test_kill_by_pid_allowed(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "kill 12345"}})
        assert result.returncode == 0

    def test_unrelated_command_allowed(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "echo killall-report"}})
        assert result.returncode == 0

    @pytest.mark.parametrize(
        "command",
        [
            'grep -rn "killall" /tmp/x',
            'grep -n "pkill\\|kill" /tmp/x',
            "rg -n pkill agent-toolkit/",
            "rg -g '*.{md,py}' pkill .",
            "git grep -n -F 'pkill' -- agent-toolkit",
            "git log -S 'killall' --oneline",
            "git -C /tmp grep -n -F 'pkill' -- agent-toolkit",
            "git -C /tmp log -S 'killall' --oneline",
            "git grep -e 'pkill' -e 'killall' -- agent-toolkit",
            "git log --grep='pkill' --oneline",
            "echo killall-report",
            "kill 12345",
            "cat <<'EOF'\npkill -f worker\nEOF",
        ],
    )
    def test_allows_literal_argument_matches(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0

    @pytest.mark.parametrize(
        "command",
        [
            'pkill -f "codex exec"',
            "killall python",
            'sh -c "pkill -f worker"',
            "sh -c 'sh -c \"pkill x\"'",
            '/bin/sh -c "pkill -f worker"',
            "xargs pkill",
            "timeout 5 pkill x",
            "env FOO=1 killall python",
            "sudo -n pkill x",
            "/usr/bin/pkill -f x",
            "$(echo pkill) -f worker",
            "echo $(pkill -f worker)",
            "echo $( pkill -f worker )",
            'rg "$(pkill -f worker)" .',
            'git grep "$(echo pkill)"',
            "git grep -O'pkill -f worker' needle",
            "git grep --open-files-in-pager='pkill -f worker' needle",
            "git -c core.pager='pkill -f worker' grep -O needle",
            "git -c diff.external='pkill -f worker' log --ext-diff -p",
            "git -c diff.external='pkill -f worker' log --ext-diff -p -S pkill",
            "echo ok\npkill -f worker",
            "/usr/bin/env pkill -f worker",
            "echo <( pkill -f worker )",
            "(pkill -f worker)",
            "if pkill -f worker; then echo done; fi",
            'git add -A; pkill -f "worker"',
        ],
    )
    def test_blocks_indirect_or_executable_matches(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "パターン一致によるプロセス終了" in result.stderr


class TestBashHeredocLiteralExclusion:
    """ヒアドキュメント本文のリテラルを実行コマンドとして誤検出しない。

    区間分割と実行位置解析はヒアドキュメント本文も実行コマンド列として扱うため、
    本文へ書き込む字面だけでは検査が成立しないことを検証する。
    """

    def test_pattern_kill_in_heredoc_body_is_not_blocked(self, tmp_path: pathlib.Path) -> None:
        """ヒアドキュメント本文へ書き込むパターン終了コマンドの字面は遮断しない。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "cat <<'EOF'\nkillall は所有権を確認できない\nEOF"},
                "session_id": "heredoc-pattern-kill",
            },
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0

    def test_pattern_kill_before_heredoc_is_still_blocked(self, tmp_path: pathlib.Path) -> None:
        """ヒアドキュメントより前にある実際のパターン終了コマンドは遮断する。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "killall worker; cat <<'EOF' > /tmp/doc.md\ntext\nEOF"},
                "session_id": "heredoc-pattern-kill-before",
            },
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 2


class TestBashGitRevParseShortMultiple:
    """`git rev-parse --short`へ複数のrevisionを渡すコマンドの警告（warn）。"""

    @pytest.mark.parametrize(
        "command",
        [
            "git rev-parse --short=7 af6889b HEAD",
            "git rev-parse --short origin/develop origin/master",
            "git -C /tmp/repo rev-parse --short=7 HEAD~1 HEAD",
            "cd /tmp/repo && git rev-parse --verify --short=7 main develop",
        ],
    )
    def test_warns_multiple_revisions(self, command: str):
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "`git rev-parse --short`へ2つのrevision" in context
        assert "`git rev-parse --short=7 <revision>`" in context

    @pytest.mark.parametrize(
        "command",
        [
            "git rev-parse --short=7 HEAD",
            "git rev-parse HEAD~1 HEAD",
            "git rev-parse --short=7 HEAD -- path/file",
            "git log --oneline HEAD~1 HEAD",
        ],
    )
    def test_single_revision_or_other_command_not_warned(self, command: str):
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0
        assert "git rev-parse --short" not in result.stdout
