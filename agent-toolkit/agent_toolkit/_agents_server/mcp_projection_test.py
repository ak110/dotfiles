"""直接配送するMCPの起動引数を、保持した配布物のrootで確定する。"""

# 試験ではbackendの直接MCP投影境界を呼び出す。
# pylint: disable=protected-access

from __future__ import annotations

import json
import pathlib
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from agent_toolkit._agents_server import claude, codex
from agent_toolkit._common import process_tree


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.parametrize("backend", ["codex", "claude-start", "claude-resume"])
@pytest.mark.parametrize("placeholder", [True, False])
def test_direct_mcp_launch_uses_retained_source_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    host_environ: Callable[[], dict[str, str]],
    backend: str,
    placeholder: bool,
) -> None:
    """別cwdから新規・再開で起動し、空白入りrootと既存の具体化済み引数を保持する。"""
    root = tmp_path / "retained plugin"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    launcher = scripts / "launch.py"
    launcher.write_text(
        "import json, pathlib, sys\nprint(json.dumps([str(pathlib.Path(__file__).parent.parent), sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    entry = "${CLAUDE_PLUGIN_ROOT}/scripts/launch.py" if placeholder else str(launcher)
    definition = {"command": sys.executable, "args": ["-B", entry, "agent_toolkit/_common/pyfltr_entry.py", "${OTHER_TOKEN}"]}
    source = root / ".mcp.json"
    source.write_text(json.dumps({"mcpServers": {"pyfltr": definition}}), encoding="utf-8")
    hooks = root / "hooks"
    hooks.mkdir()
    (hooks / "hooks.codex.json").write_text(json.dumps({"hooks": {"PostToolUse": []}}), encoding="utf-8")
    original = source.read_bytes()
    monkeypatch.setattr(codex.plugin_roots, "SERVER_PLUGIN_ROOT", root)
    monkeypatch.setattr(claude.plugin_roots, "SERVER_PLUGIN_ROOT", root)
    if backend == "codex":
        config = codex.AppServerManager._explore_config(  # noqa: SLF001
            {"features": {"retained": True}}, SimpleNamespace(parent_environment={"PATH": "retained"})
        )
        assert config["features"] == {"retained": True, "plugins": False, "hooks": True}
        projected: Any = config["mcp_servers"]["pyfltr"]
    else:
        session_id = "saved-session" if backend == "claude-resume" else None
        options = claude._build_options(str(tmp_path), "model", "high", session_id, "explore")  # noqa: SLF001
        projected = options.mcp_servers["pyfltr"]
    result = process_tree.run_with_timeout(
        [projected["command"], *projected["args"]],
        cwd=tmp_path,
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(root), ["agent_toolkit/_common/pyfltr_entry.py", "${OTHER_TOKEN}"]]
    assert source.read_bytes() == original
