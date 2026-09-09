# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-position,undefined-variable
"""agent-toolkit/scripts/_hooks/pretooluse.py のテスト。

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
from agent_toolkit._hooks.pretooluse.test_support_test import *  # noqa: F403
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE


class TestBashHelpOnlyStateChangeCommands:
    """ヘルプ専用呼び出しを状態変更コマンドの直列実行遮断から除外する。"""

    @pytest.mark.parametrize(
        "command",
        [
            "atk review-table init --help; atk review-table add --help",
            "git commit --help; git push --help",
            "gh pr create --help; gh pr merge --help",
        ],
    )
    def test_help_only_chaining_is_allowed(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})

        assert result.returncode == 0
        assert "状態を変更するコマンドを他のコマンド" not in _agent_messages(result)

    @pytest.mark.parametrize(
        "command",
        [
            "atk review-table init; echo done",
            "atk review-table init --help --title x; echo done",
            "atk review-table init -- --help; echo done",
            "printf -- '--help'; echo done",
        ],
    )
    def test_non_help_only_classifications_are_unchanged(self, command: str) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})

        if command.startswith("printf"):
            assert result.returncode == 0
            assert "状態を変更するコマンドを他のコマンド" not in _agent_messages(result)
        else:
            assert result.returncode == 2
            assert "状態を変更するコマンドを他のコマンド" in result.stderr


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

    def test_nearest_non_python_project_blocks_ancestor_project(self, tmp_path: pathlib.Path) -> None:
        """直近が`[tool.uv]`のみなら祖先に`[project]`があっても遮断する。"""
        self._make_python_project(tmp_path)
        cwd = self._make_child_directory(tmp_path)
        self._make_non_python_project(cwd)
        result = self._invoke("uv run python -c 'print(1)'", str(cwd))
        assert result.returncode == 2

    @pytest.mark.parametrize(
        "command",
        ["uv run python /tmp/foo.py", "uv run python -c 'print(1)'"],
        ids=["path", "inline_code"],
    )
    def test_non_python_project_forms_are_blocked(self, tmp_path: pathlib.Path, command: str) -> None:
        """パス引数形とインラインコード形の双方を公開インターフェースでブロックする。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke(command, cwd)
        assert result.returncode == 2
        assert "[auto-generated: agent-toolkit/pretooluse]" in result.stderr
        assert "uv run python" in result.stderr

    def test_no_pyproject_blocked(self, tmp_path: pathlib.Path):
        """pyproject.tomlが無いcwdでもblockする（Pythonプロジェクトと認識できないため）。"""
        result = self._invoke("uv run python /tmp/foo.py", str(tmp_path))
        assert result.returncode == 2

    def test_script_after_python_blocked(self, tmp_path: pathlib.Path):
        """`uv run python --script s.py`は`--script`がpythonの引数となるため例外扱いしない。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run python --script s.py", cwd)
        assert result.returncode == 2

    def test_no_project_after_python_blocked(self, tmp_path: pathlib.Path):
        """`uv run python --no-project s.py`は同上の理由で例外扱いしない。"""
        cwd = self._make_non_python_project(tmp_path)
        result = self._invoke("uv run python --no-project s.py", cwd)
        assert result.returncode == 2

    def test_cd_to_python_project_allowed(self, tmp_path: pathlib.Path) -> None:
        """静的に解決できる`cd`先がPythonプロジェクトなら許容する。"""
        payload_cwd = tmp_path / "payload"
        payload_cwd.mkdir()
        target = tmp_path / "python-target"
        target.mkdir()
        self._make_python_project(target)
        result = self._invoke(f"cd {target} && uv run python /tmp/foo.py", str(payload_cwd))
        assert result.returncode == 0

    def test_quoted_cd_to_python_project_blocks_for_safety(self, tmp_path: pathlib.Path) -> None:
        """引用符で保護した`cd`先でもglob文字を含むcwdは安全側で遮断する。"""
        payload_cwd = tmp_path / "payload"
        payload_cwd.mkdir()
        target = tmp_path / "python-target[1]"
        target.mkdir()
        self._make_python_project(target)
        result = self._invoke(f"cd '{target}' && uv run python /tmp/foo.py", str(payload_cwd))
        assert result.returncode == 2

    def test_cd_to_non_python_project_blocks(self, tmp_path: pathlib.Path) -> None:
        """静的に解決できる`cd`先がPythonプロジェクトでなければ遮断する。"""
        payload_cwd = tmp_path / "payload"
        payload_cwd.mkdir()
        target = tmp_path / "non-python-target"
        target.mkdir()
        self._make_non_python_project(target)
        result = self._invoke(f"cd {target} && uv run python /tmp/foo.py", str(payload_cwd))
        assert result.returncode == 2

    def test_unresolved_cd_blocks(self, tmp_path: pathlib.Path) -> None:
        """shell展開を含む`cd`は、payload cwdがPythonプロジェクトでも遮断する。"""
        payload_cwd = self._make_python_project(tmp_path)
        result = self._invoke('cd "$TARGET" && uv run python /tmp/foo.py', payload_cwd)
        assert result.returncode == 2

    def test_pushd_then_uv_run_blocked(self, tmp_path: pathlib.Path):
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("pushd /tmp && uv run python /tmp/foo.py", cwd)
        assert result.returncode == 2

    def test_uv_directory_option_blocked(self, tmp_path: pathlib.Path):
        """`uv --directory`はプロジェクト解決対象をpayload cwdから外すためblock。"""
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("uv --directory /tmp run python /tmp/foo.py", cwd)
        assert result.returncode == 2

    def test_uv_project_global_option_blocked(self, tmp_path: pathlib.Path):
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("uv --project /tmp run python /tmp/foo.py", cwd)
        assert result.returncode == 2

    def test_uv_run_project_option_blocked(self, tmp_path: pathlib.Path):
        """runサブコマンドオプション位置の`--project=`もblock対象。"""
        cwd = self._make_python_project(tmp_path)
        result = self._invoke("uv run --project=/tmp python /tmp/foo.py", cwd)
        assert result.returncode == 2

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
        repo = self._make_repo(tmp_path, {"agent-toolkit/scripts/foo_test.py": "x = 1\n"})
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
            ("uv run python script.py", "`uv run python`呼び出し"),
        ],
        ids=["process-kill", "uv-run-python"],
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
                "cat <<'EOF' > /tmp/doc.md\n待機例: echo start; sleep 300; echo done\nEOF",
                "foreground sleep",
            ),
            (
                "cat <<'EOF' > /tmp/doc.md\npytest -q | tail -5\nEOF",
                "truncating it",
            ),
            (
                "cat <<'EOF' > /tmp/doc.md\nrg keyword ~/.local\nEOF",
                "high-capacity user directory",
            ),
            (
                "cat <<'EOF' > /tmp/doc.md\ncodex exec 'draft the plan'\nEOF",
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
                "tool_input": {"command": "cat <<'EOF' > /tmp/doc.md\nkillall は所有権を確認できない\nEOF"},
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
                "tool_input": {"command": "cat <<'EOF' > /tmp/doc.md\npytest -q | tail -5; echo \"$?\"\nEOF"},
                "session_id": "output-status-after-truncation-heredoc",
            },
            _plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert "実行出力を`tail`・`head`で切り詰めている" not in _agent_messages(result)
        assert "終了状態を示す" not in _agent_messages(result)

    def test_state_change_chaining_before_heredoc_is_blocked(self, tmp_path: pathlib.Path) -> None:
        """heredocより前の状態変更コマンドを通常の直列連結と同じく遮断する。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "atk review-table init; cat <<'EOF'\n本文\nEOF"},
                "session_id": "heredoc-state-change-before",
            },
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 2
        assert "状態を変更するコマンドを他のコマンド" in result.stderr

    def test_recursive_grep_after_heredoc_is_warned(self, tmp_path: pathlib.Path) -> None:
        """heredoc終端後の再帰grepを通常の入力と同じく警告する。"""
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "cat <<'EOF'\n本文\nEOF\ngrep -R needle ."},
                "session_id": "heredoc-recursive-grep-after",
                "cwd": str(tmp_path),
            },
            _plan_file_state_env(tmp_path),
        )

        assert result.returncode == 0
        assert "除外設定を反映しない再帰`grep`" in _additional_context(result)
