"""Windowsの延期診断が変わっても管理サービスを再確認しない。"""

# pylint: disable=protected-access  # 既存の隔離用ハーネスと起動結果の型を再利用する。

import pathlib

import pytest

from pytools._internal import post_apply_outcome
from pytools._internal import warmup_agents_server as warmup
from pytools._internal import warmup_agents_server_test as harness


def test_startup_cache_ignores_install_detail(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    harness._setup(monkeypatch, tmp_path)
    monkeypatch.setattr(warmup.post_apply_cache, "record_path", lambda name: tmp_path / f"{name}.json")
    monkeypatch.setattr(warmup.plugin_warmup, "cleanup_prepared", lambda _prepared, **_kwargs: None)
    monkeypatch.setattr(warmup.windows_security, "is_elevated_above_user_processes", lambda: False)
    started: list[list[str]] = []

    async def startup(command: list[str]):
        started.append(command)
        return harness._STARTUP_OUTCOME(True, "起動確認済み")

    monkeypatch.setattr(warmup, "_check_startup", startup)
    monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", "deferred")
    monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", "agents-server.exe (PID=1001)")
    assert warmup.run() == post_apply_outcome.PostApplyOutcome()
    assert len(started) == 1

    monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", "agents-server.exe (PID=1002)")
    assert warmup.run() == post_apply_outcome.PostApplyOutcome()
    assert len(started) == 1

    monkeypatch.setenv("AGENT_TOOLKIT_RUNTIME_DATA", str(tmp_path / "new-runtime"))
    assert warmup.run() == post_apply_outcome.PostApplyOutcome()
    assert len(started) == 2
