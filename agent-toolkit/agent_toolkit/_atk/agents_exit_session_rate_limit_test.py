"""利用上限の再開要求がFunction hooksの既存保持期限に従うことを確認する。"""

import os
import pathlib
import time

import pytest

from agent_toolkit._atk import agents_exit_session


def test_rate_limit_requests_keep_clear_and_expire(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """通常終了で現行会話を保持し、clearと期限経過で対象だけを回収する。"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    directory = tmp_path / "agent-toolkit-function-hooks"
    directory.mkdir()
    current = directory / "rate-limit-current.txt"
    expired = directory / "rate-limit-expired.txt"
    fresh = directory / "rate-limit-fresh.txt"
    for path in (current, expired, fresh):
        path.write_text("requested", encoding="utf-8")
    stamp = time.time() - 15 * 24 * 60 * 60
    for path in (current, expired):
        os.utime(path, (stamp, stamp))
    agents_exit_session.sweep_function_hook_files(keep_session_id="current")
    assert current.exists()
    assert fresh.exists()
    assert not expired.exists()
    agents_exit_session.sweep_function_hook_files(keep_session_id="current", clear=True)
    assert not current.exists()
    assert fresh.exists()
