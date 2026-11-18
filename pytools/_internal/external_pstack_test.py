"""pstack-claudeの導入が無効状態で行われ、導入済みの設定を保つことを検証する。"""

import json
import subprocess

import pytest

from pytools._internal import external_pstack


def _fake_claude(monkeypatch: pytest.MonkeyPatch, *, plugins: list[dict], marketplaces: list[dict]) -> list[list[str]]:
    monkeypatch.setattr(external_pstack.dotfiles_mode, "is_minimal", lambda: False)
    calls: list[list[str]] = []

    def run_claude(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ["plugin", "list", "--json"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(plugins), "")
        if args == ["plugin", "marketplace", "list", "--json"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(marketplaces), "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(external_pstack.claude_common, "run_claude", run_claude)
    return calls


def test_fresh_install_registers_installs_and_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_claude(monkeypatch, plugins=[], marketplaces=[])
    assert external_pstack.run()
    assert calls[2:] == [
        ["plugin", "marketplace", "add", "michael-denyer/pstack-claude"],
        ["plugin", "install", "pstack@pstack-claude", "--scope=user"],
        ["plugin", "disable", "pstack@pstack-claude", "--scope=user"],
    ]


def test_installed_is_updated_without_changing_enabled_state(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_claude(
        monkeypatch,
        plugins=[{"id": "pstack@pstack-claude"}],
        marketplaces=[{"name": "pstack-claude"}],
    )
    assert external_pstack.run()
    assert calls[2:] == [
        ["plugin", "marketplace", "update", "pstack-claude"],
        ["plugin", "update", "pstack@pstack-claude", "--scope=user"],
    ]


def test_install_failure_does_not_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_claude(monkeypatch, plugins=[], marketplaces=[{"name": "pstack-claude"}])
    original = external_pstack.claude_common.run_claude

    def run_claude(args: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str] | None:
        if args[:2] == ["plugin", "install"]:
            calls.append(args)
            return subprocess.CompletedProcess(args, 1, "", "error")
        return original(args, timeout=timeout)

    monkeypatch.setattr(external_pstack.claude_common, "run_claude", run_claude)
    assert not external_pstack.run()
    assert ["plugin", "disable", "pstack@pstack-claude", "--scope=user"] not in calls


def test_minimal_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(external_pstack.dotfiles_mode, "is_minimal", lambda: True)
    monkeypatch.setattr(external_pstack.claude_common, "run_claude", pytest.fail)
    assert not external_pstack.run()
