"""Claude Code CLIの操作別タイムアウトを検証する。"""

# pylint: disable=protected-access

import subprocess
from pathlib import Path

from pytools._internal import claude_common, install_claude_plugins


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

    assert observed == [30, 300]


def test_plugin_install_and_update_use_extended_timeout(monkeypatch) -> None:
    """pluginのinstallとupdateだけを300秒へ延長する。"""
    observed: list[tuple[list[str], dict[str, object]]] = []

    def fake_run_claude(args, **kwargs):
        observed.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(claude_common, "run_claude", fake_run_claude)

    assert install_claude_plugins._install_plugin("example")
    assert install_claude_plugins._update_plugin("example")
    assert install_claude_plugins._disable_plugin("example@marketplace")

    assert [kwargs for _, kwargs in observed] == [{"timeout": 300}, {"timeout": 300}, {}]
