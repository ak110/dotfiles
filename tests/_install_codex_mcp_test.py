"""pytools._install_codex_mcp のテスト。

subprocess.run / shutil.which をモックして、前提条件分岐・既登録判定・
mcp add の呼び出し経路を検証する。
"""

import subprocess

import pytest

from pytools import _install_codex_mcp


class _FakeResult:
    """subprocess.CompletedProcess の軽量な代替。"""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestPrerequisites:
    """claude CLI の存在チェック。"""

    def test_missing_claude_skips(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: None)
        assert _install_codex_mcp.run() is False


class TestAlreadyRegistered:
    """codex が既に登録されている場合は add を呼ばない。"""

    def test_already_registered_skips_add(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
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

        monkeypatch.setattr(_install_codex_mcp.subprocess, "run", fake_run)

        assert _install_codex_mcp.run() is False
        # mcp add は呼ばれないこと
        assert [c for c in calls if c[:3] == ["claude", "mcp", "add"]] == []


class TestAddsWhenMissing:
    """codex が未登録の場合は add を呼ぶ。"""

    def test_adds_codex_when_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")
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

        monkeypatch.setattr(_install_codex_mcp.subprocess, "run", fake_run)

        assert _install_codex_mcp.run() is True
        add_calls = [c for c in calls if c[:3] == ["claude", "mcp", "add"]]
        assert len(add_calls) == 1
        # --scope user が渡されていること
        assert "--scope" in add_calls[0] and "user" in add_calls[0]
        # codex codex mcp-server の順で渡されていること
        assert add_calls[0][-3:] == ["codex", "codex", "mcp-server"]


class TestFailureHandling:
    """失敗系で例外を出さず False を返すこと。"""

    def test_add_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[:3] == ["claude", "mcp", "list"]:
                return _FakeResult(returncode=0, stdout="")
            if cmd[:3] == ["claude", "mcp", "add"]:
                return _FakeResult(returncode=1, stderr="boom")
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_install_codex_mcp.subprocess, "run", fake_run)

        assert _install_codex_mcp.run() is False

    def test_timeout_is_swallowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_codex_mcp.shutil, "which", lambda _name: "/usr/bin/claude")

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            raise subprocess.TimeoutExpired(cmd, timeout=1)

        monkeypatch.setattr(_install_codex_mcp.subprocess, "run", fake_run)

        assert _install_codex_mcp.run() is False
