"""Claude Code CLIの操作別タイムアウトを検証する。"""

# pylint: disable=protected-access

import subprocess
from pathlib import Path

from pytools._internal import claude_common, claude_marketplace, install_claude_plugins


def test_run_claude_uses_short_timeout_by_default(monkeypatch) -> None:
    """一般のClaude CLI呼び出しは30秒を既定とする。"""
    observed: list[float | None] = []
    monkeypatch.setattr(claude_common, "resolve_executable", lambda *args, **kwargs: Path("/claude"))

    def fake_run_subprocess(*args, **kwargs):
        observed.append(kwargs["timeout"])
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(claude_common, "run_subprocess", fake_run_subprocess)

    claude_common.run_claude(["auth", "status"])
    claude_common.run_claude(["plugin", "install", "example"], timeout=300)
    claude_common.run_claude(["debug", "wait"], timeout=None)

    assert observed == [30, 300, None]


def test_plugin_install_and_update_use_extended_timeout(monkeypatch) -> None:
    """pluginのinstallとupdateを300秒へ延長し、disableは既定値を保つ。"""
    observed: list[tuple[list[str], dict[str, object]]] = []

    def fake_run_claude(args, **kwargs):
        observed.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(claude_common, "run_claude", fake_run_claude)

    assert install_claude_plugins._install_plugin("example")
    assert install_claude_plugins._update_plugin("example")
    assert install_claude_plugins._disable_plugin("example@marketplace")

    assert [kwargs for _, kwargs in observed] == [{"timeout": 300}, {"timeout": 300}, {}]


def test_marketplace_write_operations_use_extended_timeout(monkeypatch, tmp_path) -> None:
    """marketplaceのadd/updateだけを300秒へ延長し、list/removeは既定値を保つ。"""
    observed: list[tuple[list[str], dict[str, object]]] = []

    def fake_run_claude(args, **kwargs):
        observed.append((args, kwargs))
        stdout = "[]" if args[2] == "list" else ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    checks = iter((None, False, True))
    monkeypatch.setattr(claude_common, "run_claude", fake_run_claude)
    monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(claude_marketplace, "_check_marketplace_from_file", lambda: next(checks))

    assert claude_marketplace.ensure_marketplace()
    assert claude_marketplace.repair_marketplace()
    assert claude_marketplace.refresh_marketplace()

    plugin_marketplace_calls = [(args[2], kwargs) for args, kwargs in observed if args[:2] == ["plugin", "marketplace"]]
    assert plugin_marketplace_calls == [
        ("list", {}),
        ("add", {"timeout": 300}),
        ("remove", {}),
        ("add", {"timeout": 300}),
        ("update", {"timeout": 300}),
        ("update", {"timeout": 300}),
    ]
