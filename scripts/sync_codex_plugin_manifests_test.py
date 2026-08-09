"""sync_codex_plugin_manifestsのテスト。"""

import json
from pathlib import Path
from typing import Any

import pytest
import sync_codex_plugin_manifests as subject

from pytools._internal import claude_common


def _plugin_data() -> dict[str, Any]:
    return {
        "name": "agent-toolkit",
        "version": "1.2.3",
        "description": "desc",
        "author": {"name": "aki"},
        "homepage": "h",
        "repository": "r",
        "license": "MIT",
        "keywords": ["k"],
    }


@pytest.fixture(name="manifest_root")
def manifest_root_fixture(tmp_path: Path) -> Path:
    """最小正本fixtureを作成する。"""
    plugin = _plugin_data()
    marketplace = {"name": "ak110-dotfiles", "plugins": [{**plugin, "source": "./agent-toolkit"}]}
    fixtures: tuple[tuple[Path, dict[str, Any]], ...] = (
        (subject.PLUGIN_SOURCE, plugin),
        (subject.MARKETPLACE_SOURCE, marketplace),
        (
            subject.MCP_SOURCE,
            {
                "mcpServers": {
                    "pyfltr": {
                        "command": "uvx",
                        "args": ["--from", "pyfltr>=3.16", "pyfltr", "mcp"],
                        "env": {"MODE": "portable"},
                        "cwd": "./workspace",
                    }
                }
            },
        ),
        (
            subject.HOOKS_SOURCE,
            {
                "hooks": {
                    "PermissionRequest": [
                        {
                            "matcher": "Write|Edit|MultiEdit|Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "uv run --no-project --script "
                                    "${CLAUDE_PLUGIN_ROOT}/scripts/claude_hook.py permissionrequest",
                                }
                            ],
                        },
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": subject.CODEX_PERMISSION_REQUEST_COMMAND}],
                        },
                    ],
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {"type": "command", "command": subject.CODEX_USER_PROMPT_SUBMIT_COMMAND},
                            ],
                        }
                    ],
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": subject.CODEX_STOP_COMMAND},
                            ],
                        }
                    ],
                }
            },
        ),
    )
    for path, value in fixtures:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")
    return tmp_path


def test_sync_is_deterministic(manifest_root: Path) -> None:
    assert subject.sync(manifest_root) is True
    assert subject.sync(manifest_root) is False
    generated = json.loads((manifest_root / subject.PLUGIN_TARGET).read_text())
    for key, value in _plugin_data().items():
        assert generated[key] == value
    assert generated["hooks"] == "./hooks/hooks.codex.json"
    agent_plugin_text = (manifest_root / subject.AGENT_PLUGIN_TARGET).read_text()
    agent_plugin = json.loads(agent_plugin_text)
    assert agent_plugin == {"$schema": subject.AGENT_PLUGIN_SCHEMA, **_plugin_data()}
    assert agent_plugin_text.endswith("\n")
    agent_mcp_text = (manifest_root / subject.AGENT_MCP_TARGET).read_text()
    assert json.loads(agent_mcp_text) == {
        "$schema": subject.AGENT_MCP_SCHEMA,
        "mcpServers": {
            "pyfltr": {
                "type": "stdio",
                "command": "uvx",
                "args": ["--from", "pyfltr>=3.16", "pyfltr", "mcp"],
                "env": {"MODE": "portable"},
                "cwd": "./workspace",
            }
        },
    }
    assert agent_mcp_text.endswith("\n")
    generated_hooks = json.loads((manifest_root / subject.HOOKS_TARGET).read_text())
    assert generated_hooks == {
        "hooks": {
            "PermissionRequest": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": subject.CODEX_PERMISSION_REQUEST_COMMAND}],
                }
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [{"type": "command", "command": subject.CODEX_USER_PROMPT_SUBMIT_COMMAND}],
                }
            ],
            "Stop": [
                {
                    "hooks": [{"type": "command", "command": subject.CODEX_STOP_COMMAND}],
                }
            ],
        }
    }
    assert (manifest_root / subject.PLUGIN_TARGET).read_text().endswith("\n")


def test_mcp_servers_propagated_when_source_exists(manifest_root: Path) -> None:
    subject.sync(manifest_root)

    generated = json.loads((manifest_root / subject.PLUGIN_TARGET).read_text())
    assert generated["mcpServers"] == "./.mcp.json"


def test_mcp_servers_absent_when_source_missing(manifest_root: Path) -> None:
    (manifest_root / subject.MCP_SOURCE).unlink()
    subject.sync(manifest_root)

    generated = json.loads((manifest_root / subject.PLUGIN_TARGET).read_text())
    assert "mcpServers" not in generated


@pytest.mark.parametrize(
    ("server", "message"),
    [
        ({"command": "uvx", "url": "https://example.com"}, "stdioへ変換できないMCP field"),
        ({"command": 1}, "MCP commandは文字列"),
        ({"command": "uvx", "args": "mcp"}, "MCP argsは文字列の配列"),
        ({"command": "uvx", "env": {"MODE": 1}}, "MCP envは文字列を値に持つJSON object"),
        ({"command": "uvx", "cwd": 1}, "MCP cwdは文字列"),
    ],
)
def test_rejects_unportable_mcp_server(manifest_root: Path, server: dict[str, Any], message: str) -> None:
    source = manifest_root / subject.MCP_SOURCE
    source.write_text(json.dumps({"mcpServers": {"pyfltr": server}}), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        subject.sync(manifest_root)


@pytest.mark.parametrize("source", [{"mcpServers": []}, {"mcpServers": {}, "unknown": True}])
def test_rejects_unportable_mcp_root(manifest_root: Path, source: dict[str, Any]) -> None:
    (manifest_root / subject.MCP_SOURCE).write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="MCP正本はmcpServersだけ"):
        subject.sync(manifest_root)


def test_sync_replaces_stale_outputs(manifest_root: Path) -> None:
    subject.sync(manifest_root)
    (manifest_root / subject.PLUGIN_TARGET).write_text("{}")
    (manifest_root / subject.AGENT_PLUGIN_TARGET).write_text("{}")
    (manifest_root / subject.AGENT_MCP_TARGET).write_text("{}")
    stale_hooks = manifest_root / subject.HOOKS_TARGET
    stale_hooks.write_text("{}")
    assert subject.sync(manifest_root) is True
    assert json.loads((manifest_root / subject.PLUGIN_TARGET).read_text())["version"] == "1.2.3"
    assert json.loads((manifest_root / subject.AGENT_PLUGIN_TARGET).read_text())["$schema"] == subject.AGENT_PLUGIN_SCHEMA
    assert json.loads((manifest_root / subject.AGENT_MCP_TARGET).read_text())["$schema"] == subject.AGENT_MCP_SCHEMA
    assert json.loads(stale_hooks.read_text())["hooks"]["PermissionRequest"][0]["matcher"] == "Bash"


def test_rejects_missing_allowlisted_handler(manifest_root: Path) -> None:
    hooks = json.loads((manifest_root / subject.HOOKS_SOURCE).read_text())
    hooks["hooks"]["PermissionRequest"] = hooks["hooks"]["PermissionRequest"][:1]
    (manifest_root / subject.HOOKS_SOURCE).write_text(json.dumps(hooks), encoding="utf-8")
    with pytest.raises(ValueError, match="許可済みhandler"):
        subject.sync(manifest_root)


@pytest.mark.parametrize("event", ["UserPromptSubmit", "Stop"])
def test_rejects_missing_shared_allowlisted_handler(manifest_root: Path, event: str) -> None:
    hooks = json.loads((manifest_root / subject.HOOKS_SOURCE).read_text())
    del hooks["hooks"][event]
    (manifest_root / subject.HOOKS_SOURCE).write_text(json.dumps(hooks), encoding="utf-8")
    with pytest.raises(ValueError, match="未知のCodex hookイベント"):
        subject.sync(manifest_root)


def test_rejects_unknown_allowlisted_event(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "CODEX_HOOK_ALLOWLIST", {"UnknownEvent": ("command",)})
    with pytest.raises(ValueError, match="未知のCodex hookイベント"):
        subject.sync(manifest_root)


def test_rejects_mismatched_sources(manifest_root: Path) -> None:
    data = json.loads((manifest_root / subject.MARKETPLACE_SOURCE).read_text())
    data["plugins"][0]["version"] = "9.9.9"
    (manifest_root / subject.MARKETPLACE_SOURCE).write_text(json.dumps(data))
    with pytest.raises(ValueError, match="version"):
        subject.sync(manifest_root)


def test_atomic_write_failure_is_reported(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """共通atomic writeが失敗した場合は同期成功として扱わない。"""
    monkeypatch.setattr(claude_common, "atomic_write_text", lambda *args, **kwargs: False)
    with pytest.raises(OSError, match="書き込みに失敗"):
        subject.sync(manifest_root)
