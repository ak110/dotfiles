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
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE


class TestBashUvRunPythonBlock:
    """`uv run python <path>`形式の起動ブロック。

    `[tool.uv]`のみで`[project]`セクションが無いcwdで`uv run python <path>`
    を実行すると、uvがcwdをプロジェクト解決対象として扱い`.venv`と`uv.lock`
    を生成する副作用がある。エージェントがPEP 723スクリプトを誤起動する事故を
    予防的にブロックするためのテスト。
    """

    @staticmethod
    def _make_python_project(tmp_path: pathlib.Path) -> str:
        """`[project]`セクション付きpyproject.tomlを作成しcwd文字列を返す。"""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.0.0"\n',
            encoding="utf-8",
        )
        return str(tmp_path)

    @staticmethod
    def _make_non_python_project(tmp_path: pathlib.Path) -> str:
        """`[tool.uv]`のみ持つpyproject.tomlを作成しcwd文字列を返す。"""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv]\nexclude-newer = "2025-01-01"\n',
            encoding="utf-8",
        )
        return str(tmp_path)

    @staticmethod
    def _make_child_directory(tmp_path: pathlib.Path) -> pathlib.Path:
        """子ディレクトリを作成して返す。"""
        child = tmp_path / "child"
        child.mkdir()
        return child

    @staticmethod
    def _invoke(command: str, cwd: str) -> subprocess.CompletedProcess[str]:
        return _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd})

    def test_script_option_allowed(self, tmp_path: pathlib.Path):
        """`--script`経由はcwdの依存解決を行わないため許容する。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run --script /tmp/foo.py", cwd)
        assert result.returncode == 0

    def test_no_project_option_allowed(self, tmp_path: pathlib.Path):
        """`--no-project`経由はcwdの依存解決を行わないため許容する。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run --no-project python -c 'print(1)'", cwd)
        assert result.returncode == 0

    def test_python_project_allowed(self, tmp_path: pathlib.Path):
        """`[project]`セクション付きcwdでは`uv run python -c '...'`を許容する。"""
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("uv run python -c 'print(1)'", cwd)
        assert result.returncode == 0

    def test_child_of_python_project_allowed(self, tmp_path: pathlib.Path) -> None:
        """祖先の`[project]`を解決する子ディレクトリでは正規実行を許容する。"""
        self._make_python_project(tmp_path)
        cwd = self._make_child_directory(tmp_path)
        result = self._invoke("uv run python -c 'print(1)'", str(cwd))
        assert result.returncode == 0

    def test_nearest_non_python_project_warns_despite_ancestor_project(self, tmp_path: pathlib.Path) -> None:
        """直近が`[tool.uv]`のみなら祖先に`[project]`があっても検出する。"""
        self._make_python_project(tmp_path)
        cwd = self._make_child_directory(tmp_path)
        self._make_non_python_project(cwd)
        result = self._invoke("uv run python -c 'print(1)'", str(cwd))
        assert result.returncode == 0
        assert "uv run python" in _agent_messages(result)

    def test_non_python_project_script_is_auto_fixed(self, tmp_path: pathlib.Path) -> None:
        """単純なスクリプトパス形を`uv run --script`へ補正する。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run python /tmp/foo.py --flag 'two words'", cwd)
        assert result.returncode == 0
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["updatedInput"]["command"] == "uv run --script /tmp/foo.py --flag 'two words'"

    def test_non_python_project_inline_code_is_warned(self, tmp_path: pathlib.Path) -> None:
        """安全にスクリプト形へ直せないインラインコード形は警告する。

        通した場合の結果はプロジェクト解決の失敗による終了に限り、復元できる。
        """
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run python -c 'print(1)'", cwd)
        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "[auto-generated: agent-toolkit/pretooluse]" in messages
        assert "uv run python" in messages

    def test_no_pyproject_script_is_auto_fixed(self, tmp_path: pathlib.Path):
        """pyproject.tomlが無いcwdでも単純なスクリプトパス形を補正する。"""
        result = self._invoke("uv run python /tmp/foo.py", str(tmp_path))
        assert result.returncode == 0
        assert json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"] == "uv run --script /tmp/foo.py"

    def test_script_after_python_warned(self, tmp_path: pathlib.Path):
        """`uv run python --script s.py`は`--script`がpythonの引数となるため例外扱いしない。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run python --script s.py", cwd)
        assert result.returncode == 0
        assert "uv run python" in _agent_messages(result)

    def test_no_project_after_python_warned(self, tmp_path: pathlib.Path):
        """`uv run python --no-project s.py`は同上の理由で例外扱いしない。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run python --no-project s.py", cwd)
        assert result.returncode == 0
        assert "uv run python" in _agent_messages(result)

    def test_cd_to_python_project_allowed(self, tmp_path: pathlib.Path) -> None:
        """静的に解決できる`cd`先がPythonプロジェクトなら許容する。"""
        payload_cwd = tmp_path / "payload"
        payload_cwd.mkdir()
        target = tmp_path / "python-target"
        target.mkdir()
        self._make_python_project(target)
        result = self._invoke(f"cd {target} && uv run python /tmp/foo.py", str(payload_cwd))
        assert result.returncode == 0

    def test_quoted_cd_to_python_project_script_is_auto_fixed(self, tmp_path: pathlib.Path) -> None:
        """引用符で保護した`cd`先でも単純なスクリプトパス形を補正する。"""
        payload_cwd = tmp_path / "payload"
        payload_cwd.mkdir()
        target = tmp_path / "python-target[1]"
        target.mkdir()
        self._make_python_project(target)
        result = self._invoke(f"cd '{target}' && uv run python /tmp/foo.py", str(payload_cwd))
        assert result.returncode == 0
        assert json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"] == (
            f"cd '{target}' && uv run --script /tmp/foo.py"
        )

    def test_cd_to_non_python_project_script_is_auto_fixed(self, tmp_path: pathlib.Path) -> None:
        """静的に解決できる`cd`先が非Pythonプロジェクトでも単純なスクリプトパス形を補正する。"""
        payload_cwd = tmp_path / "payload"
        payload_cwd.mkdir()
        target = tmp_path / "non-python-target"
        target.mkdir()
        self._make_non_python_project(target)
        result = self._invoke(f"cd {target} && uv run python /tmp/foo.py", str(payload_cwd))
        assert result.returncode == 0
        assert json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"] == (
            f"cd {target} && uv run --script /tmp/foo.py"
        )

    def test_unresolved_cd_warns(self, tmp_path: pathlib.Path) -> None:
        """shell展開を含む`cd`は、payload cwdがPythonプロジェクトでも検出する。"""
        payload_cwd = self._make_python_project(tmp_path)
        result = self._invoke('cd "$TARGET" && uv run python /tmp/foo.py', payload_cwd)
        assert result.returncode == 0
        assert "uv run python" in _agent_messages(result)

    def test_pushd_then_uv_run_warned(self, tmp_path: pathlib.Path):
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("pushd /tmp && uv run python /tmp/foo.py", cwd)
        assert result.returncode == 0
        assert "uv run python" in _agent_messages(result)

    def test_uv_directory_option_warned(self, tmp_path: pathlib.Path):
        """`uv --directory`はプロジェクト解決対象をpayload cwdから外すため検出対象。"""
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("uv --directory /tmp run python /tmp/foo.py", cwd)
        assert result.returncode == 0
        assert "uv run python" in _agent_messages(result)

    def test_uv_project_global_option_warned(self, tmp_path: pathlib.Path):
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("uv --project /tmp run python /tmp/foo.py", cwd)
        assert result.returncode == 0
        assert "uv run python" in _agent_messages(result)

    def test_uv_run_project_option_warned(self, tmp_path: pathlib.Path):
        """runサブコマンドオプション位置の`--project=`も検出対象。"""
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("uv run --project=/tmp python /tmp/foo.py", cwd)
        assert result.returncode == 0
        assert "uv run python" in _agent_messages(result)

    def test_cd_with_no_project_allowed(self, tmp_path: pathlib.Path):
        """cwd変更があっても`--no-project`例外が優先するため許容する。"""
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("cd /tmp && uv run --no-project python -c 'print(1)'", cwd)
        assert result.returncode == 0

    def test_unrelated_command_unaffected(self, tmp_path: pathlib.Path):
        """`uv run pytest`などは対象外（`python`トークンを含まない）。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run pytest tests/", cwd)
        assert result.returncode == 0

    def test_uvx_unaffected(self, tmp_path: pathlib.Path):
        """`uvx`は別コマンドのため対象外。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uvx ruff check .", cwd)
        assert result.returncode == 0


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
    """`mcp__plugin_agent-toolkit_agents_server__start`呼び出しの`cwd`絶対パス強制（CLI統合テスト、公開インターフェース経由）。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    def test_blocks_missing_cwd(self, state_dir: dict[str, str]) -> None:
        """`cwd`未指定の場合はブロックする。"""
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access"},
                "session_id": "cwd-missing",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "unspecified" in result.stderr

    def test_blocks_empty_string_cwd(self, state_dir: dict[str, str]) -> None:
        """`cwd`が空文字列の場合はブロックする。"""
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": ""},
                "session_id": "cwd-empty",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "unspecified" in result.stderr

    def test_blocks_whitespace_only_cwd(self, state_dir: dict[str, str]) -> None:
        """`cwd`が空白のみの場合はブロックする。"""
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "   "},
                "session_id": "cwd-whitespace",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "`   `" in result.stderr

    def test_blocks_relative_path_cwd(self, state_dir: dict[str, str]) -> None:
        """`cwd`が相対パスの場合はブロックする。"""
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "relative/path"},
                "session_id": "cwd-relative",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "relative/path" in result.stderr

    def test_start_explore_blocks_relative_path_cwd(self, state_dir: dict[str, str]) -> None:
        """探索起動も相対`cwd`を開始前に拒否する。"""
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start_explore",
                "tool_input": {"prompt": "調査", "cwd": "relative/path"},
                "session_id": "explore-cwd-relative",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "relative/path" in result.stderr

    def test_start_shell_blocks_relative_path_cwd(self, state_dir: dict[str, str]) -> None:
        """シェル実行委譲も相対`cwd`を開始前に拒否する。"""
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start_shell",
                "tool_input": {"command": "make test", "cwd": "relative/path", "summary_policy": "終了状態だけ"},
                "session_id": "shell-cwd-relative",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 2
        assert "relative/path" in result.stderr

    def test_allows_absolute_path_cwd(self, state_dir: dict[str, str]) -> None:
        """`cwd`が絶対パスの場合は許可する。"""
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/home/aki/dotfiles"},
                "session_id": "cwd-absolute",
            },
            env_overrides=state_dir,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


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


class TestAgentsServerListRepeat:
    """状態指紋に基づく`agents_server`の`list`再取得遮断を検証する。"""

    @pytest.fixture(name="state_dir")
    def _state_dir(self, tmp_path: pathlib.Path) -> dict[str, str]:
        return _plan_file_state_env(tmp_path)

    @staticmethod
    def _payload(session_id: str) -> dict:
        return {
            "tool_name": "mcp__agents_server__list",
            "tool_input": {},
            "session_id": session_id,
        }

    def test_agents_server_list_repeat_is_blocked_once(self, state_dir: dict[str, str], tmp_path: pathlib.Path) -> None:
        """同一状態の2回目だけを遮断し、権限決定を出力しない。"""
        session_id = "list-repeat"
        _write_session_state(tmp_path, session_id, {"agents_server_sessions": {"remote": {"status": "running"}}})

        first = _run(self._payload(session_id), env_overrides=state_dir)
        blocked = _run(self._payload(session_id), env_overrides=state_dir)

        assert first.returncode == 0
        assert not first.stdout
        assert blocked.returncode == 2
        assert "前回の`list`から`agents_server`の状態が変化していない" in blocked.stderr
        assert "`stop(session_id)`" in blocked.stderr
        assert "`atk agents wait`" in blocked.stderr
        assert not blocked.stdout

    def test_agents_server_list_passes_on_retry_and_state_change(
        self, state_dir: dict[str, str], tmp_path: pathlib.Path
    ) -> None:
        """遮断直後の再実行と状態変化後の再取得は通過する。"""
        session_id = "list-retry"
        _write_session_state(tmp_path, session_id, {"agents_server_sessions": {"remote": {"status": "running"}}})

        assert _run(self._payload(session_id), env_overrides=state_dir).returncode == 0
        assert _run(self._payload(session_id), env_overrides=state_dir).returncode == 2
        retry = _run(self._payload(session_id), env_overrides=state_dir)

        assert retry.returncode == 0
        state = _read_session_state(tmp_path, session_id)
        state["agents_server_sessions"] = {"remote": {"status": "completed"}}
        _write_session_state(tmp_path, session_id, state)
        changed = _run(self._payload(session_id), env_overrides=state_dir)
        assert changed.returncode == 0


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
        """ログ出力先の書き込みで`OSError`が発生しても例外を送出せず処理を継続する。"""
        blocked_tmpdir = tmp_path / "not-a-directory"
        blocked_tmpdir.write_text("x", encoding="utf-8")
        env = {"TMPDIR": str(blocked_tmpdir), "TEMP": str(blocked_tmpdir), "TMP": str(blocked_tmpdir)}
        result = _run(
            {
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "hello", "sandbox": "danger-full-access", "cwd": "/tmp/workdir"},
                "session_id": "probe-oserror",
                "isSidechain": True,
            },
            env_overrides=env,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

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


class TestBashAgentToolkitVersionBump:
    """agent-toolkit/配下コミット時のversion bump漏れ警告。

    pretooluse.pyがsubprocess経由で起動されるため、subprocess.runの差し替えではなく
    実gitリポジトリを構築して判定動作を検証する（既存testパターンと整合する）。
    """

    @staticmethod
    def _init_repo(repo: pathlib.Path) -> None:
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), capture_output=True, check=True)

    @classmethod
    def _make_repo(cls, tmp_path: pathlib.Path, staged: dict[str, str] | None = None) -> pathlib.Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        cls._init_repo(repo)
        (repo / "seed.txt").write_text("seed")
        subprocess.run(["git", "add", "seed.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        if staged:
            for name, content in staged.items():
                target = repo / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        return repo

    @classmethod
    def _make_repo_with_upstream(
        cls,
        tmp_path: pathlib.Path,
        unpushed_files: dict[str, str],
        staged: dict[str, str],
    ) -> pathlib.Path:
        """upstreamを持ち、unpushed_filesを含む未プッシュコミットがある状態を構築する。"""
        upstream = tmp_path / "upstream.git"
        subprocess.run(["git", "init", "--bare", str(upstream)], capture_output=True, check=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        cls._init_repo(repo)
        (repo / "seed.txt").write_text("seed")
        subprocess.run(["git", "add", "seed.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(upstream)], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "push", "-u", "origin", "HEAD:refs/heads/main"], cwd=str(repo), capture_output=True, check=True)
        for name, content in unpushed_files.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "unpushed"], cwd=str(repo), capture_output=True, check=True)
        for name, content in staged.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        return repo

    @classmethod
    def _make_repo_with_gone_upstream(
        cls,
        tmp_path: pathlib.Path,
        default_branch_files: dict[str, str],
        staged: dict[str, str],
    ) -> pathlib.Path:
        """`@{u}`解決対象の追跡先refが存在しない（`gone`相当）状態を構築する。

        既定ブランチ（`origin/master`）の`refs/remotes/origin/HEAD`は正常に解決できる状態を保ちつつ、
        現在の作業ブランチの`branch.master.merge`だけ存在しないリモートブランチへ向けることで、
        `@{u}`のみが解決失敗する状態を実gitリポジトリ上に再現する。
        """
        upstream = tmp_path / "upstream.git"
        subprocess.run(["git", "init", "--bare", str(upstream)], capture_output=True, check=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        cls._init_repo(repo)
        (repo / "seed.txt").write_text("seed")
        subprocess.run(["git", "add", "seed.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "branch", "-M", "master"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(upstream)], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "push", "origin", "HEAD:refs/heads/master"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "fetch", "origin"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "remote", "set-head", "origin", "master"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "branch.master.remote", "origin"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "branch.master.merge", "refs/heads/deleted-branch"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        for name, content in default_branch_files.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "unpushed-on-default-branch"], cwd=str(repo), capture_output=True, check=True)
        for name, content in staged.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            subprocess.run(["git", "add", name], cwd=str(repo), capture_output=True, check=True)
        return repo

    @staticmethod
    def _invoke(command: str, cwd: str) -> subprocess.CompletedProcess[str]:
        return _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd, "session_id": "vb-test"})

    @staticmethod
    def _has_version_bump_warning(result: subprocess.CompletedProcess[str]) -> bool:
        if not result.stdout.strip():
            return False
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
        return "plugin.json" in ctx and "version" in ctx

    def test_non_commit_command_unaffected(self, tmp_path: pathlib.Path):
        repo = self._make_repo(tmp_path, {"agent-toolkit/skills/x/SKILL.md": "# x\n"})
        result = self._invoke("git status", str(repo))
        assert result.returncode == 0
        assert not self._has_version_bump_warning(result)

    def test_no_staged_no_warn(self, tmp_path: pathlib.Path):
        repo = self._make_repo(tmp_path)
        result = self._invoke("git commit -m 'x'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_outside_agent_toolkit_no_warn(self, tmp_path: pathlib.Path):
        repo = self._make_repo(tmp_path, {"README.md": "# r\n"})
        result = self._invoke("git commit -m 'docs'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_only_test_files_no_warn(self, tmp_path: pathlib.Path):
        toolkit_prefix = "agent-" + "toolkit"
        repo = self._make_repo(tmp_path, {f"{toolkit_prefix}/scripts/foo_test.py": "x = 1\n"})
        result = self._invoke("git commit -m 'test'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_skill_change_warns(self, tmp_path: pathlib.Path):
        repo = self._make_repo(tmp_path, {"agent-toolkit/skills/x/SKILL.md": "# x\n"})
        result = self._invoke("git commit -m 'skill'", str(repo))
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert "permissionDecision" not in output["hookSpecificOutput"]
        assert self._has_version_bump_warning(result)

    def test_commit_uses_effective_cd_cwd(self, tmp_path: pathlib.Path):
        """`cd`後のcommitはpayload cwdではなく移動先の差分でversion警告を判定する。"""
        target_base = tmp_path / "target"
        target_base.mkdir()
        payload_base = tmp_path / "payload"
        payload_base.mkdir()
        target = self._make_repo(target_base, {"agent-toolkit/skills/x/SKILL.md": "# x\n"})
        payload_repo = self._make_repo(payload_base)
        result = self._invoke(f"cd {target} && git commit -m 'skill'", str(payload_repo))
        assert result.returncode == 0
        assert self._has_version_bump_warning(result)

    def test_commit_with_unresolved_cwd_suppresses_version_warning(self, tmp_path: pathlib.Path):
        """shell展開を含むcommitではpayload cwdへフォールバックしてversion警告を抑止する。"""
        payload_repo = self._make_repo(tmp_path, {"agent-toolkit/skills/x/SKILL.md": "# x\n"})
        result = self._invoke('cd "$TARGET" && git commit -m "skill"', str(payload_repo))
        assert result.returncode == 0
        assert not self._has_version_bump_warning(result)

    def test_plugin_manifest_in_staged_no_warn(self, tmp_path: pathlib.Path):
        repo = self._make_repo(
            tmp_path,
            {
                "agent-toolkit/skills/x/SKILL.md": "# x\n",
                "agent-toolkit/.claude-plugin/plugin.json": '{"version": "1.0.1"}\n',
            },
        )
        result = self._invoke("git commit -m 'skill+bump'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_unpushed_plugin_json_change_no_warn(self, tmp_path: pathlib.Path):
        repo = self._make_repo_with_upstream(
            tmp_path,
            unpushed_files={"agent-toolkit/.claude-plugin/plugin.json": '{"version": "1.0.1"}\n'},
            staged={"agent-toolkit/skills/x/SKILL.md": "# x\n"},
        )
        result = self._invoke("git commit -m 'followup'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_gone_upstream_with_default_branch_bump_no_warn(self, tmp_path: pathlib.Path):
        repo = self._make_repo_with_gone_upstream(
            tmp_path,
            default_branch_files={"agent-toolkit/.claude-plugin/plugin.json": '{"version": "1.0.1"}\n'},
            staged={"agent-toolkit/skills/x/SKILL.md": "# x\n"},
        )
        result = self._invoke("git commit -m 'followup'", str(repo))
        assert not self._has_version_bump_warning(result)

    def test_gone_upstream_without_default_branch_bump_warns(self, tmp_path: pathlib.Path):
        repo = self._make_repo_with_gone_upstream(
            tmp_path,
            default_branch_files={"README.md": "# r\n"},
            staged={"agent-toolkit/skills/x/SKILL.md": "# x\n"},
        )
        result = self._invoke("git commit -m 'skill'", str(repo))
        assert result.returncode == 0
        assert self._has_version_bump_warning(result)

    def test_linked_worktree_no_warn(self, tmp_path: pathlib.Path):
        """linked worktreeからのcommitでは警告しない。当該作業ツリーはbumpを実行しない。"""
        repo = self._make_repo(tmp_path)
        worktree = tmp_path / "lane"
        subprocess.run(
            ["git", "worktree", "add", "-b", "lane-branch", str(worktree)],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        target = worktree / "agent-toolkit" / "skills" / "x" / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# x\n")
        subprocess.run(
            ["git", "add", "agent-toolkit/skills/x/SKILL.md"],
            cwd=str(worktree),
            capture_output=True,
            check=True,
        )

        result = self._invoke("git commit -m 'skill'", str(worktree))

        assert result.returncode == 0
        assert not self._has_version_bump_warning(result)

    def test_commit_string_in_argument_position_no_warn(self, tmp_path: pathlib.Path):
        """`git commit`を検索語として含むだけの読み取り操作は警告しない。"""
        repo = self._make_repo(tmp_path, {"agent-toolkit/skills/x/SKILL.md": "# x\n"})
        result = self._invoke("grep -rn 'git commit' docs", str(repo))
        assert result.returncode == 0
        assert not self._has_version_bump_warning(result)


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
        assert "[auto-generated: agent-toolkit/pretooluse]" in result.stderr

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


class TestBashBlockBeforeAccumulatedWarnings:
    """Bashハンドラーは警告条件との同居時も遮断検査を優先する。"""

    @pytest.mark.parametrize(
        ("blocking_command", "expected_message"),
        [
            ('pkill -f "worker"', "パターン一致によるプロセス終了"),
        ],
        ids=["process-kill"],
    )
    def test_bulk_stage_warning_cannot_bypass_block(
        self,
        blocking_command: str,
        expected_message: str,
        tmp_path: pathlib.Path,
    ) -> None:
        """一括stage警告より後続の遮断条件を常に優先する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"tracked.txt": "初期値\n"})
        (repo / "untracked.txt").write_text("未追跡\n", encoding="utf-8")
        session_id = f"block-before-warning-{blocking_command.split()[0]}"
        _write_session_state(tmp_path, session_id, {"session_edited_files": []})

        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": f"git add -A; {blocking_command}"},
                "session_id": session_id,
                "cwd": str(repo),
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )

        assert result.returncode == 2
        assert "blocked" in result.stderr
        assert expected_message in result.stderr

    def test_multiple_warnings_are_accumulated_in_one_output(self, tmp_path: pathlib.Path) -> None:
        """複数の警告条件を単一PreToolUse応答へ欠落なく蓄積する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"tracked.txt": "初期値\n"})
        (repo / "untracked.txt").write_text("未追跡\n", encoding="utf-8")
        session_id = "accumulate-warnings"
        _write_session_state(tmp_path, session_id, {"session_edited_files": []})

        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git add -A; codex exec --help"},
                "session_id": session_id,
                "cwd": str(repo),
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        assert "一括`stage`" in context
        assert "`codex exec`を実行" in context


class TestBashHeredocLiteralExclusion:
    """ヒアドキュメント本文のリテラルを実行コマンドとして誤検出しない。

    区間分割と実行位置解析はヒアドキュメント本文も実行コマンド列として扱うため、
    本文へ書き込む字面だけでは検査が成立しないことを検証する。
    """

    @pytest.mark.parametrize(
        ("command", "detected_text"),
        [
            (
                "cat <<'EOF'\n待機例: echo start; sleep 300; echo done\nEOF",
                "foreground sleep",
            ),
            (
                "cat <<'EOF'\npytest -q | tail -5\nEOF",
                "truncating it",
            ),
            (
                "cat <<'EOF'\nrg keyword ~/.local\nEOF",
                "high-capacity user directory",
            ),
            (
                "cat <<'EOF'\ncodex exec 'draft the plan'\nEOF",
                "running codex exec",
            ),
        ],
        ids=["sleep-poll", "output-truncation", "recursive-home-search", "codex-exec"],
    )
    def test_warning_checks_skip_heredoc_body(self, tmp_path: pathlib.Path, command: str, detected_text: str) -> None:
        """ヒアドキュメント本文中の字面では警告を返さない。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "session_id": "heredoc-exclusion",
            },
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert detected_text not in _additional_context(result)

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

    def test_output_status_after_truncation_in_heredoc_is_silent(self, tmp_path: pathlib.Path) -> None:
        """ヒアドキュメント本文の切り詰めと終了状態参照の字面は診断しない。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "cat <<'EOF'\npytest -q | tail -5; echo \"$?\"\nEOF"},
                "session_id": "output-status-after-truncation-heredoc",
            },
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)
        assert "終了状態を示す" not in _agent_messages(result)

    def test_recursive_grep_after_heredoc_is_blocked(self, tmp_path: pathlib.Path) -> None:
        """heredoc終端後の再帰grepを通常の入力と同じく遮断する。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "cat <<'EOF'\n本文\nEOF\ngrep -R needle ."},
                "session_id": "heredoc-recursive-grep-after",
                "cwd": str(tmp_path),
            },
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 2
        assert "除外設定を反映しない再帰`grep`" in result.stderr
        assert "Git管理対象の内容は`git grep`" in result.stderr
        assert "`rg`には`--hidden`" in result.stderr
        assert "構造の探索は`find`" in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "git grep -F needle -- .claude",
            "rg --hidden needle .",
            "find . -name AGENTS.md",
        ],
    )
    def test_recursive_grep_fix_commands_are_not_blocked(self, command: str, tmp_path: pathlib.Path) -> None:
        """通知が対象性質ごとに示す再実行は同じ検査で遮断しない。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "session_id": f"recursive-grep-fix-{len(command)}",
                "cwd": str(tmp_path),
            },
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 0


class TestStaticSafetyBlocks:
    """静的に一意判定できる多段シェルと秘密情報読取を遮断する。"""

    @pytest.mark.parametrize("command", ["sh -c 'echo ok'", "docker exec app sh -c 'echo ok'", "su -c 'echo ok'"])
    def test_nested_code_string_is_blocked(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "コード文字列" in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "cat <<'EOF' > out.txt\ntext\nEOF",
            "cat > out.txt <<'EOF'\ntext\nEOF",
            "python3 - <<'PY' | wc -l\nprint(1)\nPY",
        ],
    )
    def test_heredoc_with_redirection_or_pipe_is_allowed(self, command: str) -> None:
        """heredocとリダイレクト・パイプの併用を遮断しない。

        実行環境がBash中心の作業手段を指示する構成でこの形が必要になるため、
        規範から併用禁止を外した変更に合わせて遮断も外した。
        """
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 0

    @pytest.mark.parametrize("command", ["cat .env", "head -n 1 config/.env.local", "xxd .env.production"])
    def test_env_content_output_is_blocked(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert ".env" in result.stderr

    def test_env_example_and_key_extraction_are_allowed(self) -> None:
        for command in ("cat .env.example", "grep '^TOKEN=' .env"):
            result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
            assert result.returncode == 0

    def test_read_env_is_blocked(self) -> None:
        result = _run({"tool_name": "Read", "tool_input": {"file_path": "/tmp/.env"}})
        assert result.returncode == 2
        assert ".env" in result.stderr

    def test_missing_explicit_path_is_warned(self, tmp_path: pathlib.Path) -> None:
        """不在のパスは実行してもコマンドが失敗するだけで復元できるため警告で返す。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "rg needle absent.txt"}, "cwd": str(tmp_path)})
        assert result.returncode == 0
        assert "absent.txt" in _agent_messages(result)

    def test_existing_explicit_path_is_allowed(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "present.txt"
        target.write_text("needle", encoding="utf-8")
        result = _run({"tool_name": "Bash", "tool_input": {"command": "rg needle present.txt"}, "cwd": str(tmp_path)})
        assert result.returncode == 0

    def test_git_grep_trailing_option_is_moved(self) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git grep needle --ignore-case"}})
        assert result.returncode == 0
        assert json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"] == ("git grep --ignore-case needle")

    def test_atk_unknown_option_is_warned(self) -> None:
        """未受理オプションは実行しても`atk`が終了するだけで復元できるため警告で返す。

        通知本文は対象トークンと接頭辞が一致する受理オプションだけを示す。
        受理集合の全体は判定を変えないまま実行主体のコンテキストを占めるため載せない。
        """
        result = _run({"tool_name": "Bash", "tool_input": {"command": "atk wi list --not-supported"}})
        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "--not-supported" in messages
        assert "接頭辞が一致する受理オプション: --no-json" in messages
        assert "当該サブコマンドが受理するオプション: " not in messages

    @pytest.mark.parametrize("command", ["sh -c 'echo ok'", "su -c 'echo ok'", "ssh host 'echo ok'"])
    def test_nested_code_string_notice_names_the_save_destination(self, command: str) -> None:
        """多段引用の遮断は保存先を管理対象一時領域として名指しする。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert "管理対象一時領域" in result.stderr

    def test_python_eval_argument_with_multiple_statements_is_blocked(self) -> None:
        """`python -c`へ複数の文を渡す入力を遮断する。"""
        code = "import json\nprint(json.dumps({}))"
        result = _run({"tool_name": "Bash", "tool_input": {"command": f"python3 -c {shlex.quote(code)}"}})
        assert result.returncode == 2
        assert "複数の文を含む" in result.stderr
        assert "管理対象一時領域" in result.stderr

    def test_python_eval_block_notice_names_specialized_commands_first(self) -> None:
        """`python -c`の遮断案内が、保存と実行より先に判定する専用コマンドを名指しする。"""
        code = "import json\nprint(json.dumps({}))"
        result = _run({"tool_name": "Bash", "tool_input": {"command": f"python3 -c {shlex.quote(code)}"}})
        assert result.returncode == 2
        assert "構造化データからの項目の取り出しだけを行う場合は`jq`" in result.stderr
        assert "行の抽出と置換だけを行う場合は`rg`" in result.stderr
        assert "先にその成否を判定する" in result.stderr

    def test_python_eval_argument_with_syntax_error_is_blocked(self) -> None:
        """`python -c`へ構文として成立しないコードを渡す入力を遮断する。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "python3 -c 'for x in'"}})
        assert result.returncode == 2
        assert "構文として成立しない" in result.stderr

    def test_python_eval_argument_with_a_single_statement_is_allowed(self) -> None:
        """単一の文だけを渡す`python -c`は引用境界が重なっても意味が変わらないため通す。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": "python3 -c 'print(1)'"}})
        assert result.returncode == 0


class TestBashOutputTruncationRepetition:
    """Bash出力の切り詰め補正を、同一セッションの初回だけ通し2回目から遮断する。"""

    @staticmethod
    def _invoke(command: str, session_id: str, tmp_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
        return _run(
            {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id},
            _plan_file_state_env(tmp_path),
        )

    def test_same_kind_is_blocked_from_the_second_call(self, tmp_path: pathlib.Path) -> None:
        """検索語と対象パスを変えた同種の指定も2回目として遮断する。"""
        session_id = "truncation-same-kind"
        assert self._invoke("ls -1 /tmp | head -5", session_id, tmp_path).returncode == 0

        result = self._invoke("ls -1 /var | head -5", session_id, tmp_path)

        assert result.returncode == 2
        assert "同じセッションで再び検出した" in result.stderr

    def test_other_truncation_command_is_also_blocked_as_a_repeat(self, tmp_path: pathlib.Path) -> None:
        """切り詰めコマンドの表記が変わっても同じ判定種別として2回目に数える。"""
        session_id = "truncation-other-command"
        assert self._invoke("ls -1 /tmp | head -5", session_id, tmp_path).returncode == 0

        assert self._invoke("ls -1 /tmp | tail -5", session_id, tmp_path).returncode == 2

    def test_first_notice_announces_the_next_block(self, tmp_path: pathlib.Path) -> None:
        """初回の補正の通知が、次回から遮断する旨を示す。"""
        result = self._invoke("ls -1 /tmp | head -5", "truncation-announce", tmp_path)

        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "補正せず実行前に遮断する" in context

    def test_atk_output_and_help_are_not_corrected(self, tmp_path: pathlib.Path) -> None:
        """規範が対象外と定めるヘルプ取得と`atk`の出力では補正が発火しない。"""
        for command in ("atk wi list --help | head -50", "atk wi list | head -50", "git log --help | head -20"):
            result = self._invoke(command, f"truncation-exempt-{hash(command)}", tmp_path)
            assert result.returncode == 0
            assert "切り詰め処理を除去し" not in result.stdout

    def test_exempt_calls_do_not_consume_the_first_detection(self, tmp_path: pathlib.Path) -> None:
        """対象外の取得は検出回数へ算入せず、後続の初回の補正を遮断へ変えない。"""
        session_id = "truncation-exempt-count"
        assert self._invoke("atk wi list --help | head -50", session_id, tmp_path).returncode == 0
        assert self._invoke("git log --help | head -20", session_id, tmp_path).returncode == 0

        result = self._invoke("ls -1 /tmp | head -5", session_id, tmp_path)

        assert result.returncode == 0
        assert "切り詰め処理を除去し" in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_repository_file_search_is_still_corrected(self, tmp_path: pathlib.Path) -> None:
        """作業ツリー内のファイルを件数指定で初回取得する呼び出しでは補正が発火する。"""
        target = tmp_path / "present.txt"
        target.write_text("needle\n", encoding="utf-8")

        result = self._invoke(f"rg needle {target} | head -5", "truncation-repo-search", tmp_path)

        assert result.returncode == 0
        assert "切り詰め処理を除去し" in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_autofix_notice_is_tagged_as_informational(self, tmp_path: pathlib.Path) -> None:
        """補正が成立した通知は`notice`タグで発行し、是正を要する`warn`と区別する。

        補正は補正前の呼び出しが要求した結果をそのまま返すため、実行主体の是正を要さない。
        `warn`のまま発行すると、振り返りの抽出器が当該通知を問題候補として保持する。
        """
        result = self._invoke("ls -1 /tmp | head -5", "truncation-notice-tag", tmp_path)

        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "[notice]" in context
        assert "[warn]" not in context

    def test_autofix_notice_shows_the_returned_range_and_the_avoidance_body(self, tmp_path: pathlib.Path) -> None:
        """補正の通知が、当該呼び出しへ返る範囲と、切り詰めを含まない書き方を示す。"""
        session_id = "truncation-autofix-body"

        result = self._invoke("ls -1 /tmp | head -5", session_id, tmp_path)

        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "補正前のコマンドが要求した範囲を当該呼び出しの結果へ返す" in context
        avoidance = shell_checks._OUTPUT_TRUNCATION_AVOIDANCE  # pylint: disable=protected-access  # noqa: SLF001
        assert avoidance in context

    def test_quoted_pipe_in_an_argument_is_corrected(self, tmp_path: pathlib.Path) -> None:
        """引用の内側にあるパイプ文字を演算子として数えず、切り詰めを補正する。"""
        session_id = "truncation-quoted-pipe"

        result = self._invoke("rg -l 'alpha|beta' --glob '!*.lock' | head -20", session_id, tmp_path)

        assert result.returncode == 0
        corrected = json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        producer, separator, consumer = corrected.partition("; ")
        assert separator == "; "
        assert producer.startswith("rg -l 'alpha|beta' --glob '!*.lock' > ")
        assert consumer.startswith("head -20 ")

    def test_stderr_duplication_is_kept_after_the_save_target(self, tmp_path: pathlib.Path) -> None:
        """`2>&1`を末尾に持つ呼び出しでは、保存先へのリダイレクトを当該冗長化の前へ置く。

        後方へ連結すると、標準エラーは元の標準出力の宛先へ複製され、保存先へ入らない。
        """
        session_id = "truncation-stderr-merge"

        result = self._invoke("ls -1 /tmp 2>&1 | head -5", session_id, tmp_path)

        assert result.returncode == 0
        payload = json.loads(result.stdout)["hookSpecificOutput"]
        corrected = payload["updatedInput"]["command"]
        match = re.search(r"> (\S+) 2>&1; head -5 (\S+)$", corrected)
        assert match is not None
        assert match.group(1) == match.group(2)
        assert "標準出力と標準エラー" in payload["additionalContext"]

    def test_loop_body_read_back_returns_each_iteration(self, tmp_path: pathlib.Path) -> None:
        """ループ本体の補正でも、反復ごとにconsumerが当該反復の出力を読む形へ補正する。

        保存先の読み戻しを持たない補正では、反復が同じ保存先を上書きし、
        最後の1件の内容だけが残って当該呼び出しの観測目的へ達しない。
        """
        session_id = "truncation-loop-body"

        result = self._invoke("for f in a b; do atk wi show $f | grep -m1 '^# '; done", session_id, tmp_path)

        assert result.returncode == 0
        corrected = json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        assert "| grep" not in corrected
        match = re.search(r"do atk wi show \$f > (\S+); grep -m1 '\^# ' (\S+); done$", corrected)
        assert match is not None
        assert match.group(1) == match.group(2)

    def test_loop_body_append_keeps_every_iteration(self, tmp_path: pathlib.Path) -> None:
        """consumerが操作対象を持つ区間がループ本体にある場合は、反復ごとの出力を保存先へ追記する。

        上書きにすると、反復が同じ保存先を上書きして最後の1件の内容だけが残る。
        """
        session_id = "truncation-loop-append"

        result = self._invoke("for f in a b; do ls -1 $f | grep -m1 needle -; done", session_id, tmp_path)

        assert result.returncode == 0
        corrected = json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        assert re.search(r"do ls -1 \$f >> \S+; done$", corrected) is not None

    def test_autofix_notice_identifies_the_detected_segment(self, tmp_path: pathlib.Path) -> None:
        """補正の通知が、検出した直列区間と切り詰めと判定したコマンドの表記を示す。"""
        session_id = "truncation-detected-segment"

        result = self._invoke("ls -1 /tmp; ls -1 /var | head -5", session_id, tmp_path)

        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "第2直列区間" in context
        assert "切り詰めと判定したコマンドは`head`" in context


class TestBashOutputTruncationBlockNotice:
    """全量観測が必要な出力の切り詰めを遮断する通知本文が、是正の対象を一意に示す。"""

    def test_block_notice_identifies_the_detected_segment(self, tmp_path: pathlib.Path) -> None:
        """遮断の通知が、検出した直列区間と切り詰めと判定したコマンドの表記を示す。

        是正の対象を示さない通知は、実行主体が入力全体を推測で書き直し、同じ形の再提出を反復させる。
        """
        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": "ls -1 /tmp; pytest | head -5"}, "cwd": str(tmp_path)},
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 2
        assert "第2直列区間" in result.stderr
        assert "全量観測が必要なコマンドは`pytest`" in result.stderr
        assert "切り詰めと判定したコマンドは`head`" in result.stderr


class TestBashRecursiveGrepTargetJudgement:
    """再帰`grep`の遮断本文が対象ごとのGit作業ツリー判定を示す。"""

    def test_notice_reports_the_worktree_root_for_a_tracked_target(self, tmp_path: pathlib.Path) -> None:
        """Git作業ツリーに属する対象では、rootを併記して`git grep`を組み立てられる状態にする。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": "grep -r needle ."}, "cwd": str(repo)},
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 2
        assert "Git作業ツリー" in result.stderr
        assert repo.name in result.stderr

    def test_notice_reports_a_target_outside_git(self, tmp_path: pathlib.Path) -> None:
        """Git管理外の対象では管理外である旨を示す。"""
        plain = tmp_path / "plain"
        plain.mkdir()

        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": "grep -r needle ."}, "cwd": str(plain)},
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 2
        assert "Git管理外" in result.stderr

    def test_notice_shows_the_replacement_command_for_a_tracked_target(self, tmp_path: pathlib.Path) -> None:
        """Git作業ツリーへ属する対象では、patternとパスを埋めた`git grep`の形を示す。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": "grep -r needle ."}, "cwd": str(repo)},
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 2
        assert "置換後のコマンド" in result.stderr
        assert "git -C " in result.stderr
        assert "grep -e needle -- ." in result.stderr

    def test_notice_shows_the_replacement_command_outside_git(self, tmp_path: pathlib.Path) -> None:
        """Git管理外の対象では、patternとパスを埋めた`rg`の形を示す。"""
        plain = tmp_path / "plain"
        plain.mkdir()

        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": "grep -r needle ."}, "cwd": str(plain)},
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 2
        assert "置換後のコマンド" in result.stderr
        assert "rg -e needle -- ." in result.stderr

    def test_notice_reports_why_the_replacement_is_undetermined(self, tmp_path: pathlib.Path) -> None:
        """pattern本文を一意に取り出せない入力では、確定できなかった理由を示す。"""
        plain = tmp_path / "plain"
        plain.mkdir()

        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": "grep -r -f patterns.txt ."}, "cwd": str(plain)},
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 2
        assert "置換後の形を確定できない理由" in result.stderr


class TestBashUnboundedRootTraversal:
    """走査範囲を限定しないファイルシステムの根からの`find`を遮断する。"""

    def test_root_traversal_is_blocked(self) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": "find / -name review_table"}})

        assert result.returncode == 2
        assert "ファイルシステムの根" in result.stderr
        assert "-maxdepth" in result.stderr
        assert "-prune" in result.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "find / -maxdepth 2 -name review_table",
            "find / -xdev -name review_table",
            "find /usr/share -name review_table",
            "find . -name review_table",
        ],
        ids=["maxdepth", "xdev", "scoped-directory", "relative-directory"],
    )
    def test_bounded_or_scoped_traversal_is_allowed(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})

        assert result.returncode == 0

    def test_home_traversal_stays_a_warning(self, tmp_path: pathlib.Path) -> None:
        """ホームディレクトリ起点の走査は既存の警告のまま維持する。"""
        home = tmp_path / "home"
        home.mkdir()

        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": "find ~ -name review_table"}},
            _plan_file_state_env(tmp_path, home),
        )

        assert result.returncode == 0
        assert "大容量のユーザーディレクトリ" in _additional_context(result)


class TestNormViolatingArgumentForms:
    """規範が明文で禁じる引数の形の実行前検出。

    いずれも通した場合の結果は当該コマンドの失敗に限り復元できるため、応答水準は警告とする。
    """

    @staticmethod
    def _invoke(command: str, cwd: pathlib.Path, session_id: str = "arg-forms") -> subprocess.CompletedProcess[str]:
        return _run(
            {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd), "session_id": session_id},
            _plan_file_state_env(cwd),
        )

    def test_missing_paths_are_listed_together(self, tmp_path: pathlib.Path) -> None:
        """不在のパスは実行位置ごとに全件を列挙する。"""
        result = self._invoke("rg needle absent-a.txt absent-b.txt", tmp_path)
        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "absent-a.txt" in messages
        assert "absent-b.txt" in messages

    @pytest.mark.parametrize(
        "command",
        [
            "sed -n '1,5p' absent.txt",
            "ls -l absent.txt",
            "cp absent.txt copied.txt",
            "find absent.txt -name x",
            "wc -l absent.txt",
            "grep -n needle absent.txt",
            "cat absent.txt | wc -l",
        ],
    )
    def test_missing_path_in_extended_commands(self, command: str, tmp_path: pathlib.Path) -> None:
        """対象コマンドとパイプを含む呼び出しでも不在のパスを検出する。"""
        result = self._invoke(command, tmp_path)
        assert result.returncode == 0
        assert "absent.txt" in _agent_messages(result)

    def test_existing_relative_and_absolute_paths_are_silent(self, tmp_path: pathlib.Path) -> None:
        """実在するパスは相対と絶対のいずれでも検出しない。"""
        target = tmp_path / "present.txt"
        target.write_text("needle\n", encoding="utf-8")
        for command in (f"wc -l {target}", "wc -l present.txt"):
            result = self._invoke(command, tmp_path)
            assert result.returncode == 0
            assert "明示された検索・読取パスが存在しない" not in _agent_messages(result)

    def test_missing_path_is_removed_when_other_targets_remain(self, tmp_path: pathlib.Path) -> None:
        """不在パスを除いても対象が残る呼び出しは、当該パスを除いた形へ補正する。

        警告は当該呼び出しの入力を変えないため、補正しなければ当該コマンドが失敗して再発行を要する。
        """
        (tmp_path / "present.txt").write_text("needle\n", encoding="utf-8")

        result = self._invoke("rg needle present.txt absent.txt", tmp_path)

        assert result.returncode == 0
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["updatedInput"]["command"] == "rg needle present.txt"
        context = output["additionalContext"]
        assert "実在しない検索・読取パスを当該呼び出しの対象から除いた" in context
        assert "absent.txt" in context
        assert "明示された検索・読取パスが存在しない" not in context
        # 補正で呼び出しの対象集合が狭まるため、是正を要する通知として`warn`で発行する。
        assert "[warn]" in context

    def test_only_missing_path_stays_a_warning(self, tmp_path: pathlib.Path) -> None:
        """不在パスを除くと対象が残らない呼び出しは補正せず警告のまま通す。"""
        result = self._invoke("rg needle absent.txt", tmp_path)

        assert result.returncode == 0
        assert "明示された検索・読取パスが存在しない" in _agent_messages(result)
        assert "updatedInput" not in result.stdout

    def test_output_of_a_preceding_script_is_not_missing(self, tmp_path: pathlib.Path) -> None:
        """先行区間がスクリプトを実行する場合は、以降の区間を不在判定の対象から外す。

        当該プログラムが出力するファイルはコマンド文字列へ現れず、実在を静的に判定できない。
        """
        result = self._invoke("python3 build.py && wc -l generated.txt", tmp_path)

        assert result.returncode == 0
        assert "明示された検索・読取パスが存在しない" not in _agent_messages(result)

    def test_redirect_after_cd_resolves_from_the_destination(self, tmp_path: pathlib.Path) -> None:
        """`cd <実在の絶対パス> &&`に続くリダイレクト先は、当該ディレクトリから解決する。

        起動時の作業ディレクトリから解決すると、遷移先にだけ存在する親ディレクトリを不在と判定する。
        """
        destination = tmp_path / "work"
        (destination / "logs").mkdir(parents=True)

        result = self._invoke(f"cd {destination} && wc -l /etc/hostname > logs/out.txt", tmp_path)

        assert result.returncode == 0
        assert "親ディレクトリが存在しない" not in _agent_messages(result)

    def test_redirect_after_cd_to_a_missing_directory_warns(self, tmp_path: pathlib.Path) -> None:
        """`cd`先にも存在しないディレクトリ配下への出力は引き続き検出する。"""
        destination = tmp_path / "work"
        destination.mkdir()

        result = self._invoke(f"cd {destination} && wc -l /etc/hostname > absent-dir/out.txt", tmp_path)

        assert result.returncode == 0
        assert "親ディレクトリが存在しない" in _agent_messages(result)

    def test_attached_short_value_option_is_not_terminator_data(self, tmp_path: pathlib.Path) -> None:
        """値を密着させた短縮オプションを、オプション終端を要するデータとして扱わない。"""
        result = self._invoke("rg -g'*.py' needle .", tmp_path)

        assert result.returncode == 0
        assert "オプション終端" not in _agent_messages(result)

    def test_git_grep_attached_pattern_value_is_not_a_type_option(self, tmp_path: pathlib.Path) -> None:
        """値を密着させた`-e`の値に種別を表す文字が含まれても、種別の指定として扱わない。"""
        result = self._invoke("git grep -eP.*needle", tmp_path)

        assert result.returncode == 0
        assert "いずれの種別も指定していない" in _agent_messages(result)

    def test_git_grep_attached_context_value_is_silent(self, tmp_path: pathlib.Path) -> None:
        """値を密着させた`-C3`は、オプション終端の欠落にも種別の誤判定にも当たらない。"""
        result = self._invoke("git grep -C3 needle", tmp_path)

        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "オプション終端" not in messages
        assert "いずれの種別も指定していない" not in messages

    def test_git_grep_attached_pattern_without_metacharacter_is_silent(self, tmp_path: pathlib.Path) -> None:
        """値を密着させた`-e`からもpattern本文を取り出し、メタ文字が無ければ警告しない。"""
        result = self._invoke("git grep -eneedle", tmp_path)

        assert result.returncode == 0
        assert "いずれの種別も指定していない" not in _agent_messages(result)

    def test_git_grep_without_pattern_type_warns(self, tmp_path: pathlib.Path) -> None:
        """メタ文字を含むpatternでは、種別の指定により一致結果が変わるため警告する。"""
        result = self._invoke("git grep 'need.*le'", tmp_path)
        assert result.returncode == 0
        assert "いずれの種別も指定していない" in _agent_messages(result)

    @pytest.mark.parametrize("command", ["git grep needle", "git grep -n needle", "git grep -e needle"])
    def test_git_grep_without_metacharacter_is_silent(self, command: str, tmp_path: pathlib.Path) -> None:
        """メタ文字を含まないpatternは`-F`の指定で一致結果が変わらないため警告しない。"""
        result = self._invoke(command, tmp_path)
        assert result.returncode == 0
        assert "いずれの種別も指定していない" not in _agent_messages(result)

    def test_git_grep_with_unresolvable_pattern_warns(self, tmp_path: pathlib.Path) -> None:
        """pattern本文を一意に取り出せない指定は、差異の有無を確定できないため警告する。"""
        result = self._invoke("git grep -f patterns.txt", tmp_path)
        assert result.returncode == 0
        assert "いずれの種別も指定していない" in _agent_messages(result)

    def test_redirect_target_written_by_an_earlier_segment_is_not_missing(self, tmp_path: pathlib.Path) -> None:
        """先行区間がリダイレクト先として生成するパスを、後続区間の不在判定から除く。"""
        result = self._invoke("ls -1 > listing.txt 2> listing.err; wc -l listing.txt listing.err", tmp_path)
        assert result.returncode == 0
        assert "明示された検索・読取パスが存在しない" not in _agent_messages(result)

    def test_path_not_written_by_an_earlier_segment_is_still_missing(self, tmp_path: pathlib.Path) -> None:
        """先行区間が生成しない不在パスは引き続き検出する。"""
        result = self._invoke("ls -1 > listing.txt; wc -l absent.txt", tmp_path)
        assert result.returncode == 0
        assert "absent.txt" in _agent_messages(result)

    def test_atk_redirection_is_not_counted_as_a_positional(self, tmp_path: pathlib.Path) -> None:
        """リダイレクトのトークンと宛先を位置引数として数えない。"""
        result = self._invoke("atk agents wait > wait.json 2> wait.err", tmp_path)
        assert result.returncode == 0
        assert "位置引数を受理しない" not in _agent_messages(result)

    def test_atk_actual_positional_is_still_warned(self, tmp_path: pathlib.Path) -> None:
        """位置引数を受理しないサブコマンドへ実際の位置引数を渡した場合は警告する。

        引数を受理しないサブコマンドでは、対処として引数なしでの再発行を示す。
        値をオプションで渡す対処は当該サブコマンドで実行できないため示さない。
        対象には受理オプションが`-h`だけの`atk wi pull`を使う。値付きオプションを持つサブコマンドは
        値をオプションで渡す対処が成立するため、本検体の対象から外れる。
        """
        result = self._invoke("atk wi pull extra", tmp_path)
        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "位置引数を受理しない" in messages
        assert "引数を付けずに再発行する" in messages
        assert "当該の値をオプションで渡す" not in messages

    @pytest.mark.parametrize("command", ["git grep -F needle", "git grep -nE needle", "git grep -P needle"])
    def test_git_grep_with_pattern_type_is_silent(self, command: str, tmp_path: pathlib.Path) -> None:
        result = self._invoke(command, tmp_path)
        assert result.returncode == 0
        assert "いずれの種別も指定していない" not in _agent_messages(result)

    def test_word_embedded_parenthesis_warns(self, tmp_path: pathlib.Path) -> None:
        """語の内側の丸括弧は引用の崩れとして検出する。"""
        result = self._invoke("wc -l report(1).txt", tmp_path)
        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "引用されていないシェルメタ文字" in messages
        assert "ANSI-Cクォート" in messages

    @pytest.mark.parametrize("command", ["(cd /tmp && ls)", "echo $(date)", "wc -l 'report(1).txt'"])
    def test_legitimate_parenthesis_forms_are_silent(self, command: str, tmp_path: pathlib.Path) -> None:
        result = self._invoke(command, tmp_path)
        assert result.returncode == 0
        assert "引用されていないシェルメタ文字" not in _agent_messages(result)

    def test_unresolved_git_object_warns(self, tmp_path: pathlib.Path) -> None:
        """対象リポジトリで解決できないOIDを検出する。"""
        repository = tmp_path / "repo"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True, capture_output=True)
        result = self._invoke("git log deadbeefdeadbeef..cafebabecafebabe", repository)
        assert result.returncode == 0
        assert "deadbeefdeadbeef" in _agent_messages(result)

    def test_option_terminator_missing_warns(self, tmp_path: pathlib.Path) -> None:
        result = self._invoke("rg needle -weird.txt", tmp_path)
        assert result.returncode == 0
        assert "オプション終端" in _agent_messages(result)

    def test_option_terminator_missing_warns_for_revision_subcommand(self, tmp_path: pathlib.Path) -> None:
        """revisionを位置引数として受け取る`git`サブコマンドは対象とする。"""
        result = self._invoke("git log -weird.txt", tmp_path)
        assert result.returncode == 0
        assert "オプション終端" in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        ["git commit -m -weird.txt", "git config -weird.txt", "git push -weird.txt"],
    )
    def test_option_terminator_is_silent_for_non_revision_subcommand(self, command: str, tmp_path: pathlib.Path) -> None:
        """revisionを受け取らない`git`サブコマンドは対象外とする。"""
        result = self._invoke(command, tmp_path)
        assert result.returncode == 0
        assert "オプション終端" not in _agent_messages(result)

    def test_rg_newline_pattern_without_multiline_warns(self, tmp_path: pathlib.Path) -> None:
        result = self._invoke(r"rg 'a\nb' .", tmp_path)
        assert result.returncode == 0
        assert "複数行モード" in _agent_messages(result)

    def test_rg_newline_pattern_with_multiline_is_silent(self, tmp_path: pathlib.Path) -> None:
        result = self._invoke(r"rg -U 'a\nb' .", tmp_path)
        assert result.returncode == 0
        assert "複数行モード" not in _agent_messages(result)

    def test_unknown_atk_subcommand_warns(self, tmp_path: pathlib.Path) -> None:
        """実在しないサブコマンドでは、親コマンドが受理する一覧と要約を示す。"""
        result = self._invoke("atk not-a-subcommand", tmp_path)
        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "実在しないサブコマンド" in messages
        assert "- wi: " in messages

    def test_atk_subcommand_without_positionals_warns(self, tmp_path: pathlib.Path) -> None:
        """位置引数を受理しないサブコマンドへ引数を付けた実行を検出する。

        オプションを受理するサブコマンドでは、オプションで渡す対処と受理形式の確定手段を示す。
        """
        result = self._invoke("atk wi list 20260101-000000-001.md", tmp_path)
        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "位置引数を受理しない" in messages
        assert "当該の値をオプションで渡す" in messages
        assert "受理するオプションは`--help`を単独で実行して確認する" in messages

    def test_atk_subcommand_with_positionals_is_silent(self, tmp_path: pathlib.Path) -> None:
        """位置引数を受理するサブコマンドの正常な実行は検出しない。"""
        result = self._invoke("atk wi show 20260101-000000-001.md", tmp_path)
        assert result.returncode == 0
        assert "位置引数を受理しない" not in _agent_messages(result)

    def test_unknown_child_subcommand_lists_the_level_catalog(self, tmp_path: pathlib.Path) -> None:
        """下位の段の誤りでは、当該階層の受理一覧を示し、位置引数の警告を返さない。

        受理形式の判定は下位サブコマンドを位置引数として表現するため、当該判定へ委ねると実態と異なる本文が返る。
        """
        result = self._invoke("atk config list", tmp_path)
        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "実在しないサブコマンド" in messages
        assert "- show: " in messages
        assert "位置引数を受理しない" not in messages

    def test_find_pattern_predicate_value_is_not_a_path(self, tmp_path: pathlib.Path) -> None:
        """`find`の値がパスを指さない述語の値は、パス候補として扱わない。

        規範がパスの解決手段として指定する`find`の呼び出しそのものへ、不在の警告を発火させない。
        """
        result = self._invoke("find . -maxdepth 1 -name 'absent.jsonl'", tmp_path)
        assert result.returncode == 0
        assert "明示された検索・読取パスが存在しない" not in _agent_messages(result)

    def test_find_newer_predicate_value_is_still_checked(self, tmp_path: pathlib.Path) -> None:
        """値が実在のパスを指す述語では、不在の検出を保つ。"""
        result = self._invoke("find . -newer absent.txt", tmp_path)
        assert result.returncode == 0
        assert "absent.txt" in _agent_messages(result)

    def test_relative_path_after_cd_is_resolved_from_the_destination(self, tmp_path: pathlib.Path) -> None:
        """`cd <ディレクトリ> &&`に続く相対パスは、当該ディレクトリから解決する。"""
        destination = tmp_path / "work"
        destination.mkdir()
        (destination / "present.txt").write_text("needle\n", encoding="utf-8")

        result = self._invoke(f"cd {destination} && wc -l present.txt", tmp_path)

        assert result.returncode == 0
        assert "明示された検索・読取パスが存在しない" not in _agent_messages(result)

    def test_relative_path_absent_at_the_destination_is_still_missing(self, tmp_path: pathlib.Path) -> None:
        """`cd`先にも存在しない相対パスは引き続き検出する。"""
        destination = tmp_path / "work"
        destination.mkdir()

        result = self._invoke(f"cd {destination} && wc -l absent.txt", tmp_path)

        assert result.returncode == 0
        assert "absent.txt" in _agent_messages(result)

    def test_redirect_to_missing_parent_warns(self, tmp_path: pathlib.Path) -> None:
        result = self._invoke("wc -l /etc/hostname > absent-dir/out.txt", tmp_path)
        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "親ディレクトリが存在しない" in messages
        assert "absent-dir" in messages

    @pytest.mark.parametrize(
        "command",
        [
            "wc -l /etc/hostname > out.txt",
            'wc -l /etc/hostname > "$LOG_DIR"/out.txt',
            "mkdir -p new-dir && wc -l /etc/hostname > new-dir/out.txt",
        ],
    )
    def test_redirect_targets_without_violation_are_silent(self, command: str, tmp_path: pathlib.Path) -> None:
        """存在する親、動的な出力先、先行作成を含む入力は検出しない。"""
        result = self._invoke(command, tmp_path)
        assert result.returncode == 0
        assert "親ディレクトリが存在しない" not in _agent_messages(result)

    def test_rg_unknown_option_warns(self, tmp_path: pathlib.Path) -> None:
        """`rg`の受理しないオプションは、受理形式の確定手段とともに実行前に差し戻す。

        接頭辞が一致する受理オプションが無い呼び出しでは、受理集合の全体を列挙せず確定手段だけを示す。
        """
        session_id = "rg-option-contract"
        # 受理集合は`rg --help`から取得して保持する。
        # 実行環境への`rg`の導入有無で結果が変わらないよう、保持済みの状態として与える。
        _write_session_state(
            tmp_path,
            session_id,
            {"external_command_option_contracts": {"rg": {"flags": ["--files-with-matches"], "valued": ["--regexp"]}}},
        )
        result = self._invoke("rg --not-supported needle .", tmp_path, session_id=session_id)
        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "--not-supported" in messages
        assert "`--help`を単独で実行して受理形式を確定する" in messages
        assert "--files-with-matches" not in messages

    def test_rg_option_terminator_and_value_arguments_are_not_options(self, tmp_path: pathlib.Path) -> None:
        """オプション終端の後ろと値引数の位置にあるハイフン始まりのデータを、オプションとして扱わない。"""
        session_id = "rg-option-terminator"
        _write_session_state(
            tmp_path,
            session_id,
            {"external_command_option_contracts": {"rg": {"flags": ["-n"], "valued": ["-e"]}}},
        )
        for command in ("rg -n -- '--all' .", "rg -n -e '--all' ."):
            result = self._invoke(command, tmp_path, session_id=session_id)
            assert result.returncode == 0
            assert "受理しないオプションである" not in _agent_messages(result)

    def test_rg_short_option_with_attached_value_is_accepted(self, tmp_path: pathlib.Path) -> None:
        """値を密着させた短縮オプションは、対象コマンドが受理する1つのトークンとして扱う。"""
        session_id = "rg-attached-value"
        _write_session_state(
            tmp_path,
            session_id,
            {"external_command_option_contracts": {"rg": {"flags": ["-n"], "valued": ["-A", "-B", "-m"]}}},
        )
        for command in ("rg -A14 needle .", "rg -A5 needle .", "rg -B30 needle .", "rg -m10 needle ."):
            result = self._invoke(command, tmp_path, session_id=session_id)
            assert result.returncode == 0
            assert "受理しないオプションである" not in _agent_messages(result)

    def test_help_line_with_an_uppercase_value_placeholder_marks_a_valued_option(self, tmp_path: pathlib.Path) -> None:
        """`-A NUM, --after-context=NUM`の形のヘルプ1行で、`-A`を値付きへ分類する。

        当該分類を欠くと、値を密着させた`-A14`を短縮オプションの連結として1文字ずつ照合し、
        受理集合に無い数字を根拠に警告を返す。
        """
        session_id = "rg-help-placeholder"
        stub_bin = tmp_path / "bin"
        stub_bin.mkdir()
        stub = stub_bin / "rg"
        stub.write_text(
            "#!/bin/sh\nprintf '%s\\n' '    -A NUM, --after-context=NUM' '    -n, --line-number'\n", encoding="utf-8"
        )
        stub.chmod(0o755)
        env = _plan_file_state_env(tmp_path)
        env["PATH"] = f"{stub_bin}{os.pathsep}{os.environ['PATH']}"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "rg -A14 needle ."},
                "cwd": str(tmp_path),
                "session_id": session_id,
            },
            env,
        )
        assert result.returncode == 0
        assert "受理しないオプションである" not in _agent_messages(result)
        contract = _read_session_state(tmp_path, session_id)["external_command_option_contracts"]["rg"]
        assert "-A" in contract["valued"]
        assert "-n" in contract["flags"]


class TestTruncationFixKeepsConditionalStructure:
    """切り詰め補正が`||`と`&&`の条件構造を保つこと。

    読み戻しを被演算子の外側の`;`区間へ移すと、補正前と異なる成否を返す。
    """

    @staticmethod
    def test_read_back_stays_inside_the_conditional(tmp_path: pathlib.Path) -> None:
        """`A || B | head -N`では読み戻しが`||`の右辺の内側に留まる。"""
        existing = tmp_path / "docs"
        existing.mkdir()
        command = f"test -f {tmp_path / 'absent.txt'} || ls -la {existing} | head -40"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "session_id": "conditional-truncation",
                "cwd": str(tmp_path),
            },
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 0
        corrected = json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        left, separator, right = corrected.partition("|| ")
        assert separator == "|| "
        assert "head -40 " not in left
        assert right.startswith("{ ")
        assert right.rstrip().endswith("; }")

    @staticmethod
    def test_missing_path_removal_losing_all_operands_is_blocked(tmp_path: pathlib.Path) -> None:
        """実在しないパスを除くと引数が無くなるコマンドを含む呼び出しを遮断する。"""
        existing = tmp_path / "docs"
        existing.mkdir()
        command = f"wc -l {tmp_path / 'absent.txt'} 2>/dev/null || ls -la {existing}"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "session_id": "operand-loss",
                "cwd": str(tmp_path),
            },
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 2
        messages = _agent_messages(result)
        assert "操作対象の引数が無くなる" in messages
        assert "`wc`" in messages
        assert "absent.txt" in messages


class TestRecursiveGrepReplacementKeepsOriginalOptions:
    """再帰`grep`の遮断が示す置換後コマンドの内容。

    元の呼び出しが指定した行番号出力、複数のpattern及び種別を保つ。
    """

    @staticmethod
    def test_line_number_and_multiple_patterns_are_preserved(tmp_path: pathlib.Path) -> None:
        """行番号出力と2件のpatternが提示へ現れる。"""
        target = tmp_path / "docs"
        target.mkdir()
        command = "grep -rn -e " + shlex.quote("ユーザー入力素材") + " -e " + shlex.quote("逐語") + " " + str(target)
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})

        assert result.returncode == 2
        messages = _agent_messages(result)
        assert "置換後のコマンド: `" in messages
        assert "-n" in messages
        assert "-e 'ユーザー入力素材'" in messages
        assert "-e '逐語'" in messages

    @staticmethod
    def test_fixed_string_option_is_not_added_without_the_original(tmp_path: pathlib.Path) -> None:
        """元の呼び出しが固定文字列指定を持たない場合は提示へ加えない。"""
        target = tmp_path / "docs"
        target.mkdir()
        command = "grep -r " + shlex.quote("needle") + " " + str(target)
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})

        assert result.returncode == 2
        replacement = _agent_messages(result).split("置換後のコマンド: `", 1)[1].split("`", 1)[0]
        assert " -F " not in f" {replacement} "

    @staticmethod
    def test_fixed_string_option_is_preserved(tmp_path: pathlib.Path) -> None:
        """元の呼び出しが固定文字列指定を持つ場合は提示へ保つ。"""
        target = tmp_path / "docs"
        target.mkdir()
        command = "grep -rF " + shlex.quote("needle") + " " + str(target)
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})

        assert result.returncode == 2
        replacement = _agent_messages(result).split("置換後のコマンド: `", 1)[1].split("`", 1)[0]
        assert " -F " in f" {replacement} "


class TestBashWriteTargetIsNotMissingPath:
    """コマンド自身の出力オプションが指す書込先の不在判定。

    書込先は実行の前に不在であることが正常であり、同じコマンド文字列の後続区間が
    その書込先を読む形も不在判定の対象から外す。
    """

    @staticmethod
    def test_curl_output_is_created_for_later_segments(tmp_path: pathlib.Path) -> None:
        """`curl -o <保存先>`の保存先を後続区間が読んでも警告しない。"""
        command = "curl -fsSL https://example.invalid/a -o saved.json; wc -l saved.json"
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert "明示された検索・読取パスが存在しない" not in _agent_messages(result)

    @staticmethod
    def test_output_option_without_value_still_warns(tmp_path: pathlib.Path) -> None:
        """`-o`の直後が別のオプションである呼び出しでは保存先が作成されないため警告する。"""
        command = "curl -fsSL https://example.invalid/a -o -X POST; wc -l saved.json"
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert "明示された検索・読取パスが存在しない" in _agent_messages(result)

    @staticmethod
    @pytest.mark.parametrize(
        "command",
        [
            "tee saved.log; wc -l saved.log",
            "sort -o sorted.txt /etc/hostname; wc -l sorted.txt",
            "mv /etc/hostname moved.txt",
        ],
    )
    def test_other_write_targets_are_not_missing(command: str, tmp_path: pathlib.Path) -> None:
        """`tee`、`sort -o`及び`mv`の宛先を不在として扱わない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert "明示された検索・読取パスが存在しない" not in _agent_messages(result)


class TestBashUnquotedShellMetacharacter:
    """語の内側の引用されていないシェルメタ文字の検出。

    コマンド置換とプロセス置換の括弧は引用できないため、対応の取れた範囲を取り除いた
    残りだけを検出対象とする。語頭に限らず語の内側に現れる形も取り除く。
    """

    @staticmethod
    @pytest.mark.parametrize(
        "command",
        [
            "line=$(grep '^X=' ~/.env)",
            "curl --data=$(cat f) https://example.invalid/",
            "diff <(sort a) <(sort b)",
            "payload=$(cat /tmp/a)",
        ],
    )
    def test_balanced_substitution_does_not_warn(command: str, tmp_path: pathlib.Path) -> None:
        """接頭が付いたコマンド置換とプロセス置換を警告しない。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert "語の内側に引用されていない" not in _agent_messages(result)

    @staticmethod
    @pytest.mark.parametrize("command", ["echo abc(def", "foo=abc(def", "echo a`b", "echo a(b)c("])
    def test_unbalanced_metacharacter_warns(command: str, tmp_path: pathlib.Path) -> None:
        """対応の取れない括弧とバッククォートは従来どおり警告する。"""
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert "語の内側に引用されていない" in _agent_messages(result)


class TestBashGitGrepBasicAlternation:
    """種別未指定の`git grep`のうち基本正規表現の代替表現を含むpatternの遮断。

    基本正規表現は当該表記を選択として解釈しないため、当該呼び出しは常に意図した一致を返さない。
    他のメタ文字だけを含むpatternは従来どおり警告で実行が継続する。
    """

    @staticmethod
    def test_alternation_without_pattern_type_is_blocked(tmp_path: pathlib.Path) -> None:
        """代替表現を含み種別を指定しない呼び出しを遮断する。"""
        command = "git grep -n " + shlex.quote("def a\\|def b") + " -- app"
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert result.returncode == 2
        messages = _agent_messages(result)
        assert "基本正規表現は当該表記を選択として解釈しない" in messages
        assert "`-E`を明示" in messages
        assert "`-F`を明示" in messages

    @staticmethod
    def test_other_metacharacter_still_warns(tmp_path: pathlib.Path) -> None:
        """代替表現を含まないpatternは警告のまま実行を継続する。"""
        command = "git grep -n " + shlex.quote("def .*a") + " -- app"
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert result.returncode == 0
        assert "いずれの種別も指定していない" in _agent_messages(result)

    @staticmethod
    def test_explicit_pattern_type_is_accepted(tmp_path: pathlib.Path) -> None:
        """種別を明示した呼び出しは遮断も警告もしない。"""
        command = "git grep -nE " + shlex.quote("def (a|b)") + " -- app"
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert result.returncode == 0
        messages = _agent_messages(result)
        assert "いずれの種別も指定していない" not in messages
        assert "基本正規表現は当該表記を選択として解釈しない" not in messages


class TestBashBoundaryAndPathRegressions:
    """Bashの外側境界、URI、書込先及びオプション契約の回帰検体。"""

    @staticmethod
    def test_nested_operators_do_not_trigger_truncation_autofix(tmp_path: pathlib.Path) -> None:
        command = f"stat -c '%y %n' $(ls -t {tmp_path}/*.jsonl 2>/dev/null | head -3)"
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert result.returncode == 0
        output = json.loads(result.stdout or "{}")
        assert "updatedInput" not in output.get("hookSpecificOutput", {})

    @staticmethod
    def test_invalid_rewrite_is_not_returned(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """補正処理が不正な構文を生成しても`updatedInput`候補として返さない。"""
        monkeypatch.setattr(
            shell_checks,
            "_autofix_missing_paths",
            lambda _command, _cwd: ("echo $(", ("absent.txt",)),
        )
        assert (
            shell_checks._autofix_bash_command(  # pylint: disable=protected-access
                "wc -l absent.txt present.txt",
                str(tmp_path),
                "syntax-gate",
            )
            is None
        )

    @staticmethod
    @pytest.mark.parametrize(
        "command",
        [
            "value=$(printf x; printf y); echo $value",
            "diff <(printf x | cat) <(printf y | cat); echo done",
            "(printf x && printf y); echo done",
            "value=`printf x | cat`; echo $value",
        ],
    )
    def test_nested_operators_are_not_serial_boundaries(command: str) -> None:
        assert len(shell_checks._split_serial_shell_commands(command)) == 2  # pylint: disable=protected-access

    @staticmethod
    def test_uri_is_not_a_local_path_but_local_operand_is(tmp_path: pathlib.Path) -> None:
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "wc -l https://example.invalid/a absent.txt"},
                "cwd": str(tmp_path),
            }
        )
        messages = _agent_messages(result)
        assert "absent.txt" in messages
        assert "https://example.invalid/a" not in messages

    @staticmethod
    @pytest.mark.parametrize(
        "command",
        [
            "atk wi list --output-file report.txt; wc -l report.txt",
            "atk wi list --output-file=report.txt; wc -l report.txt",
        ],
    )
    def test_atk_output_file_is_created_for_later_segment(command: str, tmp_path: pathlib.Path) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert "明示された検索・読取パスが存在しない" not in _agent_messages(result)

    @staticmethod
    def test_missing_path_guidance_explains_resolution_and_absence_check(tmp_path: pathlib.Path) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": "wc -l absent.txt"}, "cwd": str(tmp_path)})
        messages = _agent_messages(result)
        assert "`rg --files`" in messages
        assert "`find`" in messages
        assert "`test -e`" in messages

    @staticmethod
    def test_rg_ambiguous_valued_short_option_warns(tmp_path: pathlib.Path) -> None:
        session_id = "rg-ambiguous-valued-short"
        _write_session_state(
            tmp_path,
            session_id,
            {"external_command_option_contracts": {"rg": {"flags": ["-n"], "valued": ["-r", "-m"]}}},
        )
        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": "rg -rn needle ."}, "cwd": str(tmp_path), "session_id": session_id},
            _plan_file_state_env(tmp_path),
        )
        assert "曖昧な形" in _agent_messages(result)

    @staticmethod
    def test_arithmetic_expansion_does_not_warn(tmp_path: pathlib.Path) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": "echo $((1 + 2))"}, "cwd": str(tmp_path)})
        assert "語の内側に引用されていない" not in _agent_messages(result)

    @staticmethod
    @pytest.mark.parametrize(
        "command",
        [
            "printf data > artifact.txt; bash -c 'echo nested'",
            "printf data > artifact.txt; python -c 'x = 1; print(x)'",
        ],
    )
    def test_blocked_code_chain_explains_prior_artifact_is_absent(command: str, tmp_path: pathlib.Path) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)})
        assert result.returncode == 2
        assert "呼び出し全体を実行しない" in result.stderr
        assert "成果物も未作成" in result.stderr
