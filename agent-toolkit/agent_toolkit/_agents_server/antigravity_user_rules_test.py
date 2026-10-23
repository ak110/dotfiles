"""ユーザー規範の読取失敗が診断と復旧手順を持ち、turnの結果を保つことを確かめる。"""

import asyncio
import logging
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import antigravity
from agent_toolkit._agents_server.antigravity_test import _install_fake_agy


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["encoding", "permission"])
async def test_user_rules_read_failure_keeps_result_and_recovers(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    """スタブCLIへの公開start/追送を通し、読めない規範だけを復旧して次turnで再取得する。"""
    _install_fake_agy(tmp_path, monkeypatch)
    config = tmp_path / "config"
    config.mkdir()
    target = config / "CLAUDE.md"
    target.write_bytes(b"\xff" if failure == "encoding" else "規範本文".encode())
    monkeypatch.setattr(antigravity.host_homes, "claude_config_dir", lambda **_kwargs: config)
    original = pathlib.Path.read_text
    blocked = True

    def read_text(path: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        if path == target and failure == "permission" and blocked:
            raise PermissionError(13, "読取拒否", str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    caplog.set_level(logging.WARNING)
    async with antigravity.AntigravityManager() as manager:
        session = await manager.start("結果を保持する", str(tmp_path))
        async with asyncio.timeout(10):
            while not session.terminal:
                await asyncio.sleep(0.01)
        assert session.status == "completed"
        assert session.agent_message.endswith("結果を保持する")
        messages = [record.getMessage() for record in caplog.records if "ユーザー規範を読めません" in record.getMessage()]
        assert len(messages) == 1
        assert str(target) in messages[0]
        assert "次の操作:" in messages[0] and "読取権限とUTF-8" in messages[0]
        assert "追送時に規範を再取得" in messages[0]

        blocked = False
        target.write_text("復旧した規範", encoding="utf-8")
        caplog.clear()
        identity = session.session_id
        await manager.send_message(session, "同じ会話で継続する")
        async with asyncio.timeout(10):
            while not session.terminal:
                await asyncio.sleep(0.01)
        assert session.session_id == identity
        assert session.status == "completed"
        assert session.agent_message.endswith("同じ会話で継続する")
        assert not [record for record in caplog.records if "ユーザー規範を読めません" in record.getMessage()]
