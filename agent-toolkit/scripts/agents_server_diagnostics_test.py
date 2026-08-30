"""Codex App Server起動失敗時の診断情報を検証する。"""

import sys

import _agents_server_codex as subject
import pytest


async def _ignore_message(_message: dict[str, object]) -> None:
    """テスト中に到達しない通知を受理する。"""


@pytest.mark.asyncio
async def test_start_failure_reports_command_returncode_and_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """初期化前に子が終了した場合も終了理由を呼出元へ返す。"""
    command = (
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('起動診断\\n'); raise SystemExit(23)",
    )
    monkeypatch.setattr(subject, "APP_SERVER_COMMAND", command)
    client = subject.JsonRpcProcess(_ignore_message, _ignore_message)

    with pytest.raises(subject.AppServerError) as raised:
        await client.start()

    message = str(raised.value)
    assert f"command={' '.join(command)}" in message
    assert "returncode=23" in message
    assert "stderr=起動診断" in message


@pytest.mark.asyncio
async def test_start_failure_bounds_reported_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """大量の標準エラーは上限内の末尾だけを呼出元へ返す。"""
    command = (
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('a' * 20 + '末尾'); raise SystemExit(2)",
    )
    monkeypatch.setattr(subject, "APP_SERVER_COMMAND", command)
    monkeypatch.setattr(subject, "APP_SERVER_STDERR_LIMIT_CHARS", 8)
    client = subject.JsonRpcProcess(_ignore_message, _ignore_message)

    with pytest.raises(subject.AppServerError) as raised:
        await client.start()

    message = str(raised.value)
    assert "stderr=aaaaaa末尾" in message
    assert "a" * 9 not in message
