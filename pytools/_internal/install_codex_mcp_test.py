"""pytools._internal.install_codex_mcp のテスト。

subprocess.run / shutil.which をモックして、前提条件分岐・既登録判定・
mcp add の呼び出し経路を検証する。
ファイル直接読み取り関数の単体テストも含む。
"""

import json
import pathlib
import subprocess

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import install_codex_mcp as _install_codex_mcp

from ._test_helpers import _FakeResult


class TestPrerequisites:
    """claude CLI の存在チェック。"""

    def test_missing_claude_skips(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: None)
        assert _install_codex_mcp.run() is False


class TestAlreadyRegistered:
    """codex が既に登録されている場合は add を呼ばない (CLIフォールバックパス)。"""

    def test_already_registered_skips_add(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
        # ファイルが存在しない状態で CLI フォールバックパスに進む
        monkeypatch.setattr(_install_codex_mcp, "_CLAUDE_CONFIG_PATH", tmp_path / "missing.json")
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "mcp", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=(
                        "Checking MCP server health…\n\n"
                        "other: http://example - ✓ Connected\n"
                        "codex: codex mcp-server - ✓ Connected\n"
                    ),
                )
            return _FakeResult(returncode=1, stderr="should not be called")

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_codex_mcp.run() is False
        # mcp add は呼ばれないこと
        assert [c for c in calls if c[:3] == ["claude", "mcp", "add"]] == []


class TestAddsWhenMissing:
    """codex が未登録の場合は add を呼ぶ (CLIフォールバックパス)。"""

    def test_adds_codex_when_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
        # ファイルが存在しない状態で CLI フォールバックパスに進む
        monkeypatch.setattr(_install_codex_mcp, "_CLAUDE_CONFIG_PATH", tmp_path / "missing.json")
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "mcp", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout="Checking MCP server health…\n\nother: http://example - ✓ Connected\n",
                )
            if cmd[:3] == ["claude", "mcp", "add"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_codex_mcp.run() is True
        add_calls = [c for c in calls if c[:3] == ["claude", "mcp", "add"]]
        assert len(add_calls) == 1
        # --scope=user が渡されていること
        assert "--scope=user" in add_calls[0]
        # codex codex mcp-server の順で渡されていること
        assert add_calls[0][-3:] == ["codex", "codex", "mcp-server"]


class TestAlreadyExistsHandling:
    """mcp add が "already exists" を返す場合の処理 (CLIフォールバックパス)。"""

    def test_already_exists_treated_as_registered(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """タイムアウトで list が失敗した後、add が already exists を返す場合は登録済み扱い。"""
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
        # ファイルが存在しない状態で CLI フォールバックパスに進む
        monkeypatch.setattr(_install_codex_mcp, "_CLAUDE_CONFIG_PATH", tmp_path / "missing.json")

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[:3] == ["claude", "mcp", "list"]:
                # タイムアウト相当: returncode != 0
                return _FakeResult(returncode=1, stderr="timeout")
            if cmd[:3] == ["claude", "mcp", "add"]:
                return _FakeResult(returncode=1, stderr="MCP server codex already exists")
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        # 登録済みとして False を返す (例外なし)
        assert _install_codex_mcp.run() is False


class TestFailureHandling:
    """失敗系で例外を発生させず False を返すこと (CLIフォールバックパス)。"""

    def test_add_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
        # ファイルが存在しない状態で CLI フォールバックパスに進む
        monkeypatch.setattr(_install_codex_mcp, "_CLAUDE_CONFIG_PATH", tmp_path / "missing.json")

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[:3] == ["claude", "mcp", "list"]:
                return _FakeResult(returncode=0, stdout="")
            if cmd[:3] == ["claude", "mcp", "add"]:
                return _FakeResult(returncode=1, stderr="boom")
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_codex_mcp.run() is False

    def test_timeout_is_swallowed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
        # ファイルが存在しない状態で CLI フォールバックパスに進む
        monkeypatch.setattr(_install_codex_mcp, "_CLAUDE_CONFIG_PATH", tmp_path / "missing.json")

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            raise subprocess.TimeoutExpired(cmd, timeout=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_codex_mcp.run() is False


class TestReadCodexFromFile:
    """設定ファイル直接読み取り経路の統合テスト。

    `run()` 経由で `_CLAUDE_CONFIG_PATH` を差し替え、ファイル状態ごとの
    動作（登録済みスキップ・CLIフォールバック・CLI add 呼び出し）を検証する。
    """

    def test_codex_present_skips_add(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """mcpServersにcodexキーが存在する場合、CLI呼び出しなしで False を返す。"""
        path = tmp_path / ".claude.json"
        path.write_text(
            json.dumps(
                {"mcpServers": {"codex": {"type": "stdio", "command": "codex", "args": ["mcp-server"]}}}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
        monkeypatch.setattr(_install_codex_mcp, "_CLAUDE_CONFIG_PATH", path)

        def fail_if_called(cmd, **_kwargs):  # noqa: ANN001
            raise AssertionError(f"subprocess.runが呼ばれた: {cmd}")

        monkeypatch.setattr(_claude_common.subprocess, "run", fail_if_called)
        assert _install_codex_mcp.run() is False

    def test_codex_absent_triggers_add(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """mcpServersは存在するがcodexキーが無い場合、mcp add を呼び出す。"""
        path = tmp_path / ".claude.json"
        path.write_text(json.dumps({"mcpServers": {"other": {}}}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
        monkeypatch.setattr(_install_codex_mcp, "_CLAUDE_CONFIG_PATH", path)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "mcp", "add"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)
        assert _install_codex_mcp.run() is True
        assert any(c[:3] == ["claude", "mcp", "add"] for c in calls)

    def test_file_not_found_falls_back_to_cli(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """ファイルが存在しない場合、CLI フォールバックして mcp list を呼び出す。"""
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
        monkeypatch.setattr(_install_codex_mcp, "_CLAUDE_CONFIG_PATH", tmp_path / "missing.json")
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "mcp", "list"]:
                return _FakeResult(returncode=0, stdout="codex: codex mcp-server - ✓ Connected\n")
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)
        # CLI 経由で登録済みを確認 → add は呼ばれず False を返す
        assert _install_codex_mcp.run() is False
        assert any(c[:3] == ["claude", "mcp", "list"] for c in calls)

    def test_invalid_json_falls_back_to_cli(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """不正なJSONの場合、CLI フォールバックして mcp list を呼び出す。"""
        path = tmp_path / ".claude.json"
        path.write_text("{bad", encoding="utf-8")
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
        monkeypatch.setattr(_install_codex_mcp, "_CLAUDE_CONFIG_PATH", path)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "mcp", "list"]:
                return _FakeResult(returncode=0, stdout="")
            if cmd[:3] == ["claude", "mcp", "add"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)
        # JSON 不正 → CLI フォールバック → 未登録 → add を呼び出す
        assert _install_codex_mcp.run() is True
        assert any(c[:3] == ["claude", "mcp", "add"] for c in calls)

    def test_no_mcp_servers_key_falls_back_to_cli(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """mcpServersキー自体が無い場合、CLI フォールバックして mcp list を呼び出す。"""
        path = tmp_path / ".claude.json"
        path.write_text(json.dumps({"otherKey": "value"}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
        monkeypatch.setattr(_install_codex_mcp, "_CLAUDE_CONFIG_PATH", path)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "mcp", "list"]:
                return _FakeResult(returncode=0, stdout="codex: codex mcp-server - ✓ Connected\n")
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)
        # mcpServers キーなし → CLI フォールバック → 登録済み確認 → False
        assert _install_codex_mcp.run() is False
        assert any(c[:3] == ["claude", "mcp", "list"] for c in calls)


class TestHappyPathNoCli:
    """happy path（codex登録済み）でCLI呼び出しがゼロであることを検証する統合テスト。"""

    def test_registered_no_cli_calls(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """codexが登録済みならsubprocessを一切呼ばない。"""
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")

        path = tmp_path / ".claude.json"
        path.write_text(
            json.dumps(
                {"mcpServers": {"codex": {"type": "stdio", "command": "codex", "args": ["mcp-server"]}}}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_install_codex_mcp, "_CLAUDE_CONFIG_PATH", path)

        def fail_if_called(cmd, **_kwargs):  # noqa: ANN001
            raise AssertionError(f"subprocess.runが呼ばれた: {cmd}")

        monkeypatch.setattr(_claude_common.subprocess, "run", fail_if_called)

        assert _install_codex_mcp.run() is False
