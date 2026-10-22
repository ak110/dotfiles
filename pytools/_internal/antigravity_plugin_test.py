"""Antigravity 1.4.4の組込customizationsガイドに基づく配布・登録の結合試験。"""

import json
import shlex
import shutil
from pathlib import Path
from typing import Any

import pytest
from agent_toolkit._common import plugin_environment

from pytools._internal import antigravity_plugin as subject

_CHECK_SKILL = Path("skills") / "check" / "SKILL.md"


@pytest.fixture(name="source")
def _source(tmp_path: Path) -> Path:
    root = tmp_path / "空白を含む source"
    files = {
        ".claude-plugin/plugin.json": json.dumps({"name": "agent-toolkit", "version": "1.2.3", "description": "共通指針"}),
        ".mcp.json": json.dumps(
            {
                "mcpServers": {
                    "agents_server": {
                        "command": "uv",
                        "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/launch.py", "agent_toolkit/agents_server_mcp.py"],
                    },
                    "pyfltr": {"command": "uvx", "args": ["pyfltr", "mcp"]},
                    "claude_only": {"command": "private"},
                }
            }
        ),
        "rules/01-agent.md": "共通規範を守る。\n" * 1100,
        _CHECK_SKILL.as_posix(): "---\nname: check\ndescription: 検査する\n---\n${CLAUDE_PLUGIN_ROOT}/scripts/check.py\n",
        "agents/worker.md": "---\nname: worker\ndescription: 作業を行う\n---\n作業する。\n",
        "share/rules-main.md": "メインの指針。\n",
        "share/execute.subagent.md": "---\nrequired_inputs: [対象]\n---\n担当の入力を受け取って実装する。\n",
        "skills/long/SKILL.md": "---\r\nname: long\r\ndescription: 長い工程を実行する\r\n---\r\n" + "全文を読む。\r\n" * 2000,
        "agent_toolkit/hook.py": "print('{}')\n",
        "agent_toolkit/hook_test.py": "# 配布しない試験。\n",
        "scripts/atk-hook": "# hook入口。\n",
        "scripts/launch.py": "# MCP入口。\n",
        "pyproject.toml": "[project]\nname = 'fixture'\nversion = '1.2.3'\n",
        "uv.lock": "version = 1\n",
    }
    for relative, content in files.items():
        path = root / "agent-toolkit" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    basics = root / "agent-basics"
    (basics / ".claude-plugin").mkdir(parents=True)
    (basics / ".claude-plugin/plugin.json").write_text(
        json.dumps(
            {
                "name": "agent-basics",
                "version": "4.5.6",
                "description": "品質基準",
            }
        )
    )
    (basics / "skills/bugfix/references").mkdir(parents=True)
    (basics / "skills/bugfix/SKILL.md").write_text(
        "---\nname: bugfix\ndescription: 品質の問題を直す\n---\n" + "品質基準の全文を保持する。\n" * 1000,
    )
    (basics / "skills/bugfix/references/reason.md").write_text("判断の根拠。\n")
    return root


def test_sync_delivers_native_plugin_and_repairs_drift(source: Path) -> None:
    """root配置、全6イベント、MCP、規範全文とサイズ、および再同期を同時に確認する。"""
    assert subject.sync(source)
    target = source / "agent-toolkit-agy"
    assert json.loads((target / "plugin.json").read_text())["version"] == "1.2.3"
    # scripts/launch.pyが環境選択で必ず読む配布版のmanifestも保持する。
    assert (target / ".claude-plugin/plugin.json").read_bytes() == (
        source / "agent-toolkit/.claude-plugin/plugin.json"
    ).read_bytes()
    # 本番bootstrapと同じ環境選択を、依存の導入や起動なしで通す。
    runtime = source / "runtime-data"
    environment = plugin_environment.launch_environment(target, {plugin_environment.RUNTIME_DATA_ENV: str(runtime)})
    assert Path(environment["UV_PROJECT_ENVIRONMENT"]).is_relative_to(runtime)
    assert not (target / ".venv").exists()
    events = json.loads((target / "hooks.json").read_text())["agent-toolkit"]
    assert set(events) == {"SessionStart", "PreInvocation", "PreToolUse", "PostToolUse", "PostInvocation", "Stop"}
    for event, groups in events.items():
        handler = groups[0]["hooks"][0] if event in {"PreToolUse", "PostToolUse"} else groups[0]
        arguments = shlex.split(handler["command"])
        assert arguments[-3:] == [str(target / "scripts/atk-hook"), "antigravity", event]
        assert "${" not in handler["command"]
    mcp = json.loads((target / "mcp_config.json").read_text())["mcpServers"]
    assert set(mcp) == {"agents_server", "pyfltr"}
    assert mcp["agents_server"]["args"][0] == str(target / "scripts/launch.py")
    assert (target / "agents/worker.md").is_file()
    assert (target / "agents/execute.md").is_file()
    assert str(target / "share/execute.subagent.md") in (target / "agents/execute.md").read_text()
    assert (target / "skills/long/SKILL.md").stat().st_size < 24_000
    assert (target / "skills/long/references/antigravity-full-instructions.md").read_text() == "全文を読む。\n" * 2000
    assert (target / "share/rules-main.md").is_file()
    assert (target / "uv.lock").is_file()
    assert not (target / "agent_toolkit/hook_test.py").exists()
    rules = sorted((target / "rules").glob("*.md"), key=lambda path: path.name != "AGENTS.md")
    assert len(rules) == 2
    assert all(path.stat().st_size <= 24_000 for path in rules)
    assert (
        "".join(path.read_text().removeprefix("---\ntrigger: always_on\n---\n\n") for path in rules)
        == (source / "agent-toolkit/rules/01-agent.md").read_text()
    )
    assert not subject.check_diagnostics(source)
    assert not subject.sync(source)
    stale = target / "skills/obsolete/SKILL.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("古い定義")
    (source / "agent-toolkit" / _CHECK_SKILL).write_text("新しい定義")
    assert subject.check_diagnostics(source)
    assert subject.sync(source)
    assert not stale.exists()
    assert (target / _CHECK_SKILL).read_text() == "新しい定義"


@pytest.mark.parametrize(
    "body", ["あ" * 8001, "あ\n" * 11000, "あ\n" * 10000], ids=["long-line", "total-budget", "headers-budget"]
)
def test_size_failure_keeps_previous_distribution(source: Path, body: str) -> None:
    """単行上限と全体予算の超過は、前回の生成物を書き換える前に拒否する。"""
    subject.sync(source)
    manifest = source / "agent-toolkit-agy/plugin.json"
    before = manifest.read_bytes()
    (source / "agent-toolkit/rules/01-agent.md").write_text(body)
    with pytest.raises(ValueError):
        subject.sync(source)
    assert manifest.read_bytes() == before


def test_post_apply_preserves_other_registrations_and_updates(source: Path, tmp_path: Path, monkeypatch) -> None:
    """登録を追加して原本の更新を反映し、再実行で登録を重複させない。"""
    home = tmp_path / "home"
    registry = home / ".gemini/config/plugins.json"
    registry.parent.mkdir(parents=True)
    original = {"entries": [{"path": "/other/plugins"}], "inherits": [{"path": "/team/plugins.json"}]}
    registry.write_text(json.dumps(original))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(subject.common, "find_dotfiles_root", lambda: source)
    monkeypatch.setattr(subject.common, "resolve_executable", lambda _name: Path("agy"))
    assert subject.run().changed
    registered = json.loads(registry.read_text())
    assert registered["entries"][0] == original["entries"][0]
    assert registered["inherits"] == original["inherits"]
    assert registered["entries"][1] == {
        "path": str(source),
        "include_only": ["agent-toolkit-agy", "agent-basics-agy"],
    }
    assert not subject.run().changed
    (source / "agent-toolkit/share/rules-main.md").write_text("更新後のメイン指針")
    assert subject.run().changed
    assert (source / "agent-toolkit-agy/share/rules-main.md").read_text() == "更新後のメイン指針"
    assert len(json.loads(registry.read_text())["entries"]) == 2


def test_basics_standalone_keeps_full_body_and_namespace(source: Path) -> None:
    """toolkitが無いsourceでも、独立した品質プラグインの全文と名前空間を配送する。"""
    shutil.rmtree(source / "agent-toolkit")
    original = (source / "agent-basics/skills/bugfix/SKILL.md").read_text()
    assert subject.sync(source, "agent-basics")
    target = source / "agent-basics-agy"
    manifest = json.loads((target / "plugin.json").read_text())
    assert manifest["name"] == "agent-basics" and manifest["version"] == "4.5.6"
    entry = (target / "skills/bugfix/SKILL.md").read_text()
    body = (target / "skills/bugfix/references/antigravity-full-instructions.md").read_text()
    assert len(entry.encode()) <= 24_000
    metadata, expected_body = original[4:].split("\n---\n", 1)
    assert entry.startswith(f"---\n{metadata}\n---\n")
    assert body == expected_body
    assert str(target / "skills/bugfix/references/antigravity-full-instructions.md") in entry
    assert str(target / "skills/bugfix") in entry
    assert (target / "skills/bugfix/references/reason.md").read_text() == "判断の根拠。\n"
    assert not (target / "hooks.json").exists()
    assert not (target / "mcp_config.json").exists()
    assert not subject.check_diagnostics(source, "agent-basics")
    assert not subject.sync(source, "agent-basics")


def test_registration_upgrades_managed_entry_and_preserves_preferences(source: Path, tmp_path: Path, monkeypatch) -> None:
    """旧toolkit登録にbasicsを加え、他の登録・継承・ユーザーの値を保持する。"""
    home = tmp_path / "home"
    registry = home / ".gemini/config/plugins.json"
    registry.parent.mkdir(parents=True)
    original: dict[str, Any] = {
        "entries": [
            {"path": str(source), "include_only": ["team-.*"]},
            {"path": str(source), "include_only": ["agent-toolkit-agy"]},
        ],
        "inherits": [{"path": "/team/plugins.json"}],
        "custom": {"keep": True},
    }
    registry.write_text(json.dumps(original))
    config = registry.with_name("config.json")
    config.write_text('{"plugins":{"agent-toolkit-agy":{"enabled":false}}}')
    before = config.read_bytes()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(subject.common, "find_dotfiles_root", lambda: source)
    monkeypatch.setattr(subject.common, "resolve_executable", lambda _name: Path("agy"))
    assert subject.run().changed
    value = json.loads(registry.read_text())
    assert len(value["entries"]) == 2
    assert value["entries"][0] == original["entries"][0]
    assert value["entries"][1]["include_only"] == ["agent-toolkit-agy", "agent-basics-agy"]
    assert value["inherits"] == original["inherits"] and value["custom"] == original["custom"]
    assert config.read_bytes() == before
    assert not subject.run().changed


def test_standalone_registration_does_not_require_toolkit(source: Path, tmp_path: Path, monkeypatch) -> None:
    """品質プラグインだけを登録する利用はtoolkitのsourceと実行環境に依存しない。"""
    shutil.rmtree(source / "agent-toolkit")
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(subject.common, "find_dotfiles_root", lambda: source)
    monkeypatch.setattr(subject.common, "resolve_executable", lambda _name: Path("agy"))
    assert subject.run(("agent-basics",)).changed
    value = json.loads((home / ".gemini/config/plugins.json").read_text())
    assert value["entries"] == [{"path": str(source), "include_only": ["agent-basics-agy"]}]
