"""結果受領のホストとプロジェクトを、判断資料の解決まで保持する。"""

import json
import subprocess

import pytest

from agent_toolkit._common import plugin_roots
from agent_toolkit._hooks import improvement_status_advisor
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.read_delivery_receipts_support import _scenario

__all__ = ["_scenario"]


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_received_result_uses_host_installation(scenario, tmp_path, monkeypatch, host):
    """旧Claude版と現Codex版の共存を、Post受領から次のPre案内まで通す。"""
    _state, _rows, _path, _rollout, invoke, delivered = scenario
    home = tmp_path / "codex"
    roots = {
        "codex": home / "plugins/cache/mine/agent-basics/2",
        "claude": tmp_path / "claude-project/agent-basics/1",
        "user": tmp_path / "claude-user/agent-basics/0",
    }
    for name, root in roots.items():
        document = root / "skills/review-standards/references/reviewee.md"
        document.parent.mkdir(parents=True)
        document.write_text(f"# {name}の基準\n", encoding="utf-8")

    toolkit = tmp_path / "cache/mine/agent-toolkit/9"
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", toolkit)
    monkeypatch.setenv("CODEX_HOME", str(home))
    codex = {
        "installed": [
            {"name": name, "marketplaceName": "mine", "version": "2", "installed": True, "enabled": True}
            for name in ("agent-toolkit", "agent-basics")
        ]
    }
    claude = [
        {"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(toolkit)},
        {"id": "agent-basics@mine", "scope": "user", "enabled": True, "installPath": str(roots["user"])},
        {
            "id": "agent-basics@mine",
            "scope": "project",
            "projectPath": str(tmp_path),
            "enabled": True,
            "installPath": str(roots["claude"]),
        },
    ]
    inventories = {}
    for name, data in (("codex", codex), ("claude", claude)):
        registry = tmp_path / f"{name}-installed.json"
        registry.write_text(json.dumps(data), encoding="utf-8")
        inventories[name] = registry

    def acquire(args, **kwargs):
        assert kwargs["cwd"] == str(tmp_path)
        body = (
            json.dumps({"marketplaces": [{"name": "mine"}]})
            if args[1:4] == ["plugin", "marketplace", "list"]
            else inventories[args[0]].read_text(encoding="utf-8")
        )
        return subprocess.CompletedProcess(args, 0, body, "")

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", acquire)
    response = {
        "session_id": "child",
        "status": "completed",
        "agent_message": "指摘なし",
        "read_requirements": ["agent-basics:review-standards/references/reviewee.md"],
    }
    invoke(host, "PostToolUse", "mcp__agents_server__wait", {}, {"structuredContent": response})
    if host == "codex":
        delivered(json.dumps({"structuredContent": response}))
    guidance = invoke(host, "PreToolUse", "Do", {})
    assert str(roots[host]) in guidance
    assert str(roots["claude" if host == "codex" else "codex"]) not in guidance
    assert str(roots["user"]) not in guidance


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_improvement_guidance_uses_host_installation(tmp_path, monkeypatch, host):
    """別の生成元でも改善判定資料に実効ホストを渡す。"""
    root = tmp_path / host
    document = root / "skills/bugfix/references/root-cause-prevention.md"
    document.parent.mkdir(parents=True)
    document.write_text("# 判定基準\n", encoding="utf-8")

    def resolve(_name, _root, selected="claude", project=None, **_kwargs):
        assert selected == host and project == str(tmp_path)
        return root

    monkeypatch.setattr(improvement_status_advisor, "resolve_plugin_root", resolve)
    monkeypatch.setattr(improvement_status_advisor, "read_state", lambda _session: {})
    monkeypatch.setattr(improvement_status_advisor, "is_main_agent_context", lambda _payload: True)
    body = improvement_status_advisor.read_guidance_notice(
        {
            "session_id": "host",
            "cwd": str(tmp_path),
            "last_assistant_message": "気付いた改善点: 対象（AWI化ステータス: 不要 処置済み）",
            **({"turn_id": "turn"} if host == "codex" else {}),
        }
    )
    assert str(document) in body
