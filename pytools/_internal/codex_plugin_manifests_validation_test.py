# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""codex_plugin_manifestsのvalidationの試験。"""

# pylint: disable=protected-access

import json
from pathlib import Path
from typing import Any

import pytest

from pytools._internal import codex_plugin_manifests as subject
from pytools._internal import common
from pytools._internal.codex_plugin_manifests_test_helpers import _proxy_endpoint as _proxy_endpoint
from pytools._internal.codex_plugin_manifests_test_helpers import _quality_root as _quality_root
from pytools._internal.codex_plugin_manifests_test_helpers import manifest_root_fixture as manifest_root_fixture


def test_mcp_servers_propagated_when_source_exists(manifest_root: Path) -> None:
    subject.sync(manifest_root)

    generated = json.loads((manifest_root / subject.PLUGIN_TARGET).read_text(encoding="utf-8"))
    assert generated["mcpServers"] == "./.mcp.codex.json"


def test_mcp_servers_absent_when_source_missing(manifest_root: Path) -> None:
    (manifest_root / subject.MCP_SOURCE).unlink()
    subject.sync(manifest_root)

    generated = json.loads((manifest_root / subject.PLUGIN_TARGET).read_text(encoding="utf-8"))
    assert "mcpServers" not in generated


@pytest.mark.parametrize(
    ("server", "message"),
    [
        ({"command": "uvx", "url": "https://example.com"}, "stdioへ変換できないMCP field"),
        ({"command": 1}, "MCP commandは文字列"),
        ({"command": ""}, "MCP commandは空文字列"),
        ({"command": "uvx", "args": "mcp"}, "MCP argsは文字列の配列"),
        ({"command": "uvx", "env": {"MODE": 1}}, "MCP envは文字列を値に持つJSON object"),
        ({"command": "uvx", "env": {"PLUGIN_ROOT": "./root"}}, "MCP envにAgent Pluginsの予約名"),
        ({"command": "uvx", "env": {"PLUGIN_DATA": "./data"}}, "MCP envにAgent Pluginsの予約名"),
        ({"command": "uvx", "cwd": 1}, "MCP cwdは文字列"),
        ({"command": "uvx", "cwd": "workspace"}, "MCP cwdはAgent Plugins schemaのpattern"),
    ],
)
def test_rejects_unportable_mcp_server(manifest_root: Path, server: dict[str, Any], message: str) -> None:
    source = manifest_root / subject.MCP_SOURCE
    source.write_text(json.dumps({"mcpServers": {"pyfltr": server}}), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        subject.sync(manifest_root)


@pytest.mark.parametrize(
    "cwd",
    ["./workspace", "${PLUGIN_ROOT}", "${PLUGIN_ROOT}/workspace", "${PLUGIN_DATA}", "${PLUGIN_DATA}/workspace"],
)
def test_accepts_agent_plugin_cwd_patterns(manifest_root: Path, cwd: str) -> None:
    source = manifest_root / subject.MCP_SOURCE
    source.write_text(json.dumps({"mcpServers": {"pyfltr": {"command": "uvx", "cwd": cwd}}}), encoding="utf-8")
    subject.sync(manifest_root)
    generated = json.loads((manifest_root / subject.AGENT_MCP_TARGET).read_text(encoding="utf-8"))
    assert generated["mcpServers"]["pyfltr"]["cwd"] == cwd


@pytest.mark.parametrize("source", [{"mcpServers": []}, {"mcpServers": {}, "unknown": True}])
def test_rejects_unportable_mcp_root(manifest_root: Path, source: dict[str, Any]) -> None:
    (manifest_root / subject.MCP_SOURCE).write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="agent-toolkit/.mcp.jsonはmcpServersだけ"):
        subject.sync(manifest_root)


def test_sync_replaces_stale_outputs(manifest_root: Path) -> None:
    subject.sync(manifest_root)
    (manifest_root / subject.PLUGIN_TARGET).write_text("{}", encoding="utf-8")
    (manifest_root / subject.AGENT_PLUGIN_TARGET).write_text("{}", encoding="utf-8")
    (manifest_root / subject.AGENT_MCP_TARGET).write_text("{}", encoding="utf-8")
    stale_hooks = manifest_root / subject.HOOKS_TARGET
    stale_hooks.write_text("{}", encoding="utf-8")
    assert subject.sync(manifest_root) is True
    assert json.loads((manifest_root / subject.PLUGIN_TARGET).read_text(encoding="utf-8"))["version"] == "1.2.3"
    assert (
        json.loads((manifest_root / subject.AGENT_PLUGIN_TARGET).read_text(encoding="utf-8"))["$schema"]
        == subject.AGENT_PLUGIN_SCHEMA
    )
    assert (
        json.loads((manifest_root / subject.AGENT_MCP_TARGET).read_text(encoding="utf-8"))["$schema"]
        == subject.AGENT_MCP_SCHEMA
    )
    assert "PermissionRequest" not in json.loads(stale_hooks.read_text(encoding="utf-8"))["hooks"]


def test_check_accepts_current_outputs_without_changes(manifest_root: Path) -> None:
    subject.sync(manifest_root)
    before = {path.relative_to(manifest_root): path.read_bytes() for path in manifest_root.rglob("*") if path.is_file()}

    assert not subject.check_diagnostics(manifest_root)

    after = {path.relative_to(manifest_root): path.read_bytes() for path in manifest_root.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("state", ["missing", "stale"])
def test_check_rejects_missing_or_stale_output_without_repairing(manifest_root: Path, state: str) -> None:
    subject.sync(manifest_root)
    target = manifest_root / subject.PLUGIN_TARGET
    if state == "missing":
        target.unlink()
    else:
        target.write_text("{}", encoding="utf-8")
    before_exists = target.exists()
    before_content = target.read_text(encoding="utf-8") if before_exists else None

    assert subject.check(manifest_root) is False
    assert target.exists() is before_exists
    assert (target.read_text(encoding="utf-8") if target.exists() else None) == before_content


@pytest.mark.parametrize(
    ("source", "target"),
    [(subject.MCP_SOURCE, subject.AGENT_MCP_TARGET), (subject.HOOKS_SOURCE, subject.HOOKS_TARGET)],
)
def test_optional_output_without_source_is_stale_and_sync_removes_it(
    manifest_root: Path,
    source: Path,
    target: Path,
) -> None:
    subject.sync(manifest_root)
    (manifest_root / source).unlink()
    target_path = manifest_root / target
    assert target_path.exists()

    assert subject.check(manifest_root) is False
    assert target_path.exists()
    assert subject.sync(manifest_root) is True
    assert not target_path.exists()
    assert subject.check(manifest_root) is True


@pytest.mark.parametrize("kind", ["欠落", "余剰", "内容差"])
def test_check_reports_normal_output_difference_without_repairing(manifest_root: Path, kind: str) -> None:
    """通常派生物の差を相対パスと種類で返し、検査で修復しない。"""
    subject.sync(manifest_root)
    target = manifest_root / (subject.HOOKS_TARGET if kind == "余剰" else subject.PLUGIN_TARGET)
    if kind == "欠落":
        target.unlink()
    elif kind == "余剰":
        (manifest_root / subject.HOOKS_SOURCE).unlink()
    else:
        target.write_text("{}", encoding="utf-8")
    before = target.read_bytes() if target.exists() else None

    diagnostics = subject.check_diagnostics(manifest_root)

    assert f"{target.relative_to(manifest_root)}: {kind}" in diagnostics
    assert (target.read_bytes() if target.exists() else None) == before


@pytest.mark.parametrize("kind", ["欠落", "余剰", "内容差", "mode差"])
def test_check_reports_codex_root_difference_without_repairing(manifest_root: Path, kind: str) -> None:
    """`agent-toolkit-codex/`の差を相対パスと種類で返し、検査で修復しない。"""
    subject.sync(manifest_root)
    target = manifest_root / subject.CODEX_PLUGIN_ROOT_TARGET / ("extra.md" if kind == "余剰" else "GENERATED.md")
    if kind == "欠落":
        target.unlink()
    elif kind == "余剰":
        target.write_text("extra", encoding="utf-8")
    elif kind == "内容差":
        target.write_text("wrong", encoding="utf-8")
    else:
        target.chmod(0o600)
    before = (target.read_bytes() if target.exists() else None, target.stat().st_mode if target.exists() else None)

    diagnostics = subject.check_diagnostics(manifest_root)

    assert f"{target.relative_to(manifest_root)}: {kind}" in diagnostics
    assert (target.read_bytes() if target.exists() else None, target.stat().st_mode if target.exists() else None) == before


def test_rejects_missing_allowlisted_handler(manifest_root: Path) -> None:
    hooks = json.loads((manifest_root / subject.HOOKS_SOURCE).read_text(encoding="utf-8"))
    hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "unknown"
    (manifest_root / subject.HOOKS_SOURCE).write_text(json.dumps(hooks), encoding="utf-8")
    with pytest.raises(ValueError, match="許可済みハンドラー"):
        subject.sync(manifest_root)


@pytest.mark.parametrize(
    "event",
    ["SessionStart", "SubagentStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SubagentStop", "SessionEnd"],
)
def test_rejects_missing_shared_allowlisted_handler(manifest_root: Path, event: str) -> None:
    hooks = json.loads((manifest_root / subject.HOOKS_SOURCE).read_text(encoding="utf-8"))
    del hooks["hooks"][event]
    (manifest_root / subject.HOOKS_SOURCE).write_text(json.dumps(hooks), encoding="utf-8")
    with pytest.raises(ValueError, match="未知のCodex hookイベント"):
        subject.sync(manifest_root)


def test_rejects_unknown_allowlisted_event(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subject,
        "CODEX_HOOK_ALLOWLIST",
        {"UnknownEvent": subject.CodexHookProjection(("command",))},
    )
    with pytest.raises(ValueError, match="未知のCodex hookイベント"):
        subject.sync(manifest_root)


def test_rejects_mismatched_sources(manifest_root: Path) -> None:
    data = json.loads((manifest_root / subject.MARKETPLACE_SOURCE).read_text(encoding="utf-8"))
    data["plugins"][0]["version"] = "9.9.9"
    (manifest_root / subject.MARKETPLACE_SOURCE).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        subject.sync(manifest_root)


def test_atomic_write_failure_is_reported(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """共通atomic writeが失敗した場合は同期成功として扱わない。"""
    monkeypatch.setattr(common, "atomic_write_text", lambda *args, **kwargs: False)
    with pytest.raises(OSError, match="書き込みに失敗"):
        subject.sync(manifest_root)


__all__ = ["manifest_root_fixture"]
