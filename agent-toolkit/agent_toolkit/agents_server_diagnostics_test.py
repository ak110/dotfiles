"""Codex App Server起動失敗時の診断情報を検証する。"""

import sys

import pytest

from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import codex_rpc


async def _ignore_message(_message: dict[str, object]) -> None:
    """テスト中に到達しない通知を受理する。"""


@pytest.mark.asyncio
async def test_start_failure_reports_command_returncode_and_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """初期化前に子が終了した場合も終了理由を委譲元へ返す。"""
    command = (
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('起動診断\\n'); raise SystemExit(23)",
    )
    monkeypatch.setattr(codex_rpc, "APP_SERVER_COMMAND", command)
    async with subject.JsonRpcProcess(_ignore_message, _ignore_message) as client:
        with pytest.raises(subject.AppServerError) as raised:
            await client.start()

    message = str(raised.value)
    assert f"command={' '.join(command)}" in message
    assert "returncode=23" in message
    assert "stderr=起動診断" in message


@pytest.mark.asyncio
async def test_start_failure_bounds_reported_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """大量の標準エラーは上限内の末尾だけを委譲元へ返す。"""
    command = (
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('a' * 20 + '末尾'); raise SystemExit(2)",
    )
    monkeypatch.setattr(codex_rpc, "APP_SERVER_COMMAND", command)
    monkeypatch.setattr(codex_rpc, "APP_SERVER_STDERR_LIMIT_CHARS", 8)
    async with subject.JsonRpcProcess(_ignore_message, _ignore_message) as client:
        with pytest.raises(subject.AppServerError) as raised:
            await client.start()

    message = str(raised.value)
    assert "stderr=aaaaaa末尾" in message
    assert "a" * 9 not in message


@pytest.mark.asyncio
async def test_app_server_stderr_redacts_before_taking_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """末尾の上限の開始位置が資格情報の途中にあっても、子プロセスの診断へ断片を残さない。"""
    command = (
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('https://' + 'A' * 45 + '@example.org\\n' + 'z' * 3950 + '\\n'); raise SystemExit(2)",
    )
    monkeypatch.setattr(codex_rpc, "APP_SERVER_COMMAND", command)
    async with subject.JsonRpcProcess(_ignore_message, _ignore_message) as client:
        with pytest.raises(subject.AppServerError) as raised:
            await client.start()

    message = str(raised.value)
    assert "A" * 10 not in message
    assert "z" * 3000 in message


def test_claude_initialization_stderr_redacts_before_taking_tail() -> None:
    """Claude SDKの初期化の標準エラーも、末尾の上限を取る前に伏せる。"""
    from agent_toolkit._agents_server import claude_diagnostics  # pylint: disable=import-outside-toplevel

    diagnostic = claude_diagnostics._InitializationDiagnostic()  # pylint: disable=protected-access
    diagnostic.capture_stderr("https://" + "A" * 45 + "@example.org\n")
    diagnostic.capture_stderr("z" * 3950 + "\n")
    assert "A" * 10 not in diagnostic.stderr
    assert "A" * 10 not in diagnostic.public(child_processes=[])["stderr"]
    assert len(diagnostic.stderr) <= 4000
