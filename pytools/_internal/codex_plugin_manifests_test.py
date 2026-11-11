# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""codex_plugin_manifestsの基本動作の試験。"""

# pylint: disable=protected-access

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agent_toolkit._atk import config as toolkit_config
from agent_toolkit._common import mcp_environment

from pytools._internal import codex_plugin_manifests as subject
from pytools._internal.codex_plugin_manifests_test_helpers import _CODEX_HOOK_PREFIX, _basics_plugin_data, _plugin_data
from pytools._internal.codex_plugin_manifests_test_helpers import _proxy_endpoint as _proxy_endpoint
from pytools._internal.codex_plugin_manifests_test_helpers import _quality_root as _quality_root
from pytools._internal.codex_plugin_manifests_test_helpers import manifest_root_fixture as manifest_root_fixture


def test_basics_outputs_are_generated_without_claude_dependencies(manifest_root: Path) -> None:
    """basicsはskillsだけを持つAgent Plugins/Codex manifestへ投影する。"""
    basics = _basics_plugin_data()
    (manifest_root / subject.BASICS_PLUGIN_SOURCE).parent.mkdir(parents=True, exist_ok=True)
    (manifest_root / subject.BASICS_PLUGIN_SOURCE).write_text(json.dumps(basics), encoding="utf-8")
    marketplace_path = manifest_root / subject.MARKETPLACE_SOURCE
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    marketplace["plugins"][1] = {**basics, "source": "./agent-basics"}
    marketplace_path.write_text(json.dumps(marketplace), encoding="utf-8")
    skills = manifest_root / "agent-basics/skills/writing-standards"
    skills.mkdir(parents=True, exist_ok=True)
    (skills / "SKILL.md").write_text("# writing\n", encoding="utf-8")
    reference = skills / "references"
    reference.mkdir(exist_ok=True)
    (reference / "writing.md").write_text(
        "<!-- portable-quality:start -->\n\n## 基準\n\n本文\n\n<!-- portable-quality:end -->\n", encoding="utf-8"
    )

    subject.sync(manifest_root)

    agent = json.loads((manifest_root / subject.BASICS_AGENT_PLUGIN_TARGET).read_text(encoding="utf-8"))
    codex = json.loads((manifest_root / subject.BASICS_PLUGIN_TARGET).read_text(encoding="utf-8"))
    assert agent == {"$schema": subject.AGENT_PLUGIN_SCHEMA, **basics}
    assert codex["skills"] == "./skills/"
    assert "dependencies" not in agent
    assert "hooks" not in codex
    assert "mcpServers" not in codex
    marketplace = json.loads((manifest_root / subject.MARKETPLACE_TARGET).read_text(encoding="utf-8"))
    assert marketplace["plugins"][0]["source"] == {"source": "local", "path": "./agent-basics"}


def test_skill_quality_resources_follow_one_source_and_work_in_single_skill_copy(quality_root: Path) -> None:
    """共通原本の生成がCodex投影へ届き、スキル単独のコピーでも基準を読める。"""
    expected_body = "# 日本語の書き方\n\n主体と対象を明示する。\n"
    expected_paths = {
        Path(distribution) / "skills" / name / "references/quality-standards.md"
        for distribution in ("agent-toolkit", "agent-toolkit-codex", "agent-basics")
        for name in ("alpha", "beta")
    }

    assert subject.sync(quality_root) is True

    observed = {path.relative_to(quality_root) for path in quality_root.glob("*/skills/*/references/quality-standards.md")}
    assert observed == expected_paths
    before = {path: (quality_root / path).read_bytes() for path in expected_paths}
    assert all(content.decode("utf-8").split("\n\n", maxsplit=1)[1] == expected_body for content in before.values())
    assert subject.sync(quality_root) is False
    assert {path: (quality_root / path).read_bytes() for path in expected_paths} == before
    copied = quality_root / "single-skill"
    original_skill = Path("agent-toolkit/skills") / "alpha"
    shutil.copytree(quality_root / original_skill, copied)
    assert (copied / "references/quality-standards.md").read_bytes() == before[
        original_skill / "references/quality-standards.md"
    ]
    assert not (copied / "writing-standards").exists()
    assert subject.check(quality_root) is True

    source = quality_root / subject.QUALITY_SOURCE
    source.write_text(
        source.read_text(encoding="utf-8").replace("主体と対象を明示する。", "条件と結果を対応付ける。"), encoding="utf-8"
    )
    assert subject.check(quality_root) is False
    assert subject.sync(quality_root) is True
    for path in expected_paths:
        assert (quality_root / path).read_text(encoding="utf-8").split("\n\n", maxsplit=1)[1] == (
            "# 日本語の書き方\n\n条件と結果を対応付ける。\n"
        )


@pytest.mark.parametrize("distribution", ["agent-toolkit", "agent-toolkit-codex", "agent-basics"])
@pytest.mark.parametrize("kind", ["欠落", "内容差", "余剰"])
def test_skill_quality_check_reports_every_difference_and_sync_repairs_it(
    quality_root: Path, distribution: str, kind: str
) -> None:
    """原本側と投影側の欠け・内容差・余分を報告し、同期で同じ集合へ戻す。"""
    subject.sync(quality_root)
    name = "extra" if kind == "余剰" else "alpha"
    target = quality_root / distribution / "skills" / name / "references/quality-standards.md"
    if kind == "欠落":
        target.unlink()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("別の本文\n", encoding="utf-8")
    before = target.read_bytes() if target.exists() else None

    assert f"{target.relative_to(quality_root)}: {kind}" in subject.check_diagnostics(quality_root)
    assert (target.read_bytes() if target.exists() else None) == before
    assert subject.sync(quality_root) is True
    assert subject.check(quality_root) is True
    if kind == "余剰":
        assert not target.exists()


def test_sync_is_deterministic(manifest_root: Path) -> None:
    assert subject.sync(manifest_root) is True
    assert subject.sync(manifest_root) is False
    generated = json.loads((manifest_root / subject.PLUGIN_TARGET).read_text(encoding="utf-8"))
    for key, value in _plugin_data().items():
        assert generated[key] == value
    assert generated["hooks"] == "./hooks/hooks.codex.json"
    marketplace = json.loads((manifest_root / subject.MARKETPLACE_TARGET).read_text(encoding="utf-8"))
    assert marketplace["plugins"][1]["source"] == {"source": "local", "path": "./agent-toolkit-codex"}
    codex_root = manifest_root / subject.CODEX_PLUGIN_ROOT_TARGET
    assert (codex_root / "GENERATED.md").read_text(encoding="utf-8") == subject.CODEX_ROOT_NOTICE
    assert not (codex_root / ".ruff_cache").exists()
    assert not (codex_root / "plugin.json").exists()
    assert not (codex_root / "mcp.json").exists()
    for relative in (Path(".codex-plugin/plugin.json"), Path(".mcp.codex.json")):
        projected = codex_root / relative
        assert projected.is_file()
        assert not projected.is_symlink()
        assert projected.read_text(encoding="utf-8") == (manifest_root / "agent-toolkit" / relative).read_text(encoding="utf-8")
    portable_hooks = json.loads((manifest_root / subject.HOOKS_TARGET).read_text(encoding="utf-8"))["hooks"]
    local_hooks = json.loads((codex_root / "hooks/hooks.codex.json").read_text(encoding="utf-8"))["hooks"]
    portable_commands = [hook["command"] for groups in portable_hooks.values() for group in groups for hook in group["hooks"]]
    local_commands = [hook["command"] for groups in local_hooks.values() for group in groups for hook in group["hooks"]]
    assert all(command.startswith(_CODEX_HOOK_PREFIX) for command in portable_commands)
    assert {command.removeprefix("atk-hook ") for command in local_commands} == {
        command.removesuffix(subject.HOOK_FAILURE_SUFFIX).rsplit(" ", maxsplit=1)[-1] for command in portable_commands
    }
    agent_plugin_text = (manifest_root / subject.AGENT_PLUGIN_TARGET).read_text(encoding="utf-8")
    agent_plugin = json.loads(agent_plugin_text)
    assert agent_plugin == {"$schema": subject.AGENT_PLUGIN_SCHEMA, **_plugin_data()}
    assert agent_plugin_text.endswith("\n")
    codex_mcp_text = (manifest_root / subject.MCP_CODEX_TARGET).read_text(encoding="utf-8")
    agent_mcp_text = (manifest_root / subject.AGENT_MCP_TARGET).read_text(encoding="utf-8")
    expected_mcp: dict[str, Any] = {
        "$schema": subject.AGENT_MCP_SCHEMA,
        "mcpServers": {
            "pyfltr": {
                "type": "stdio",
                "command": "uvx",
                "args": ["--from", "pyfltr>=3.16", "pyfltr", "mcp"],
                "env": {"MODE": "portable"},
                "cwd": "./workspace",
            },
            "agents_server": {
                "type": "stdio",
                "command": "${PLUGIN_ROOT}/bin/agents-server",
                "args": ["--script", "${PLUGIN_ROOT}/scripts/agents_server_mcp.py"],
                "env": {"SCRIPT_ROOT": "${PLUGIN_ROOT}/data"},
                "cwd": "${PLUGIN_ROOT}/workspace",
            },
        },
    }
    expected_codex_mcp = {
        **expected_mcp,
        "mcpServers": {
            "pyfltr": {
                **expected_mcp["mcpServers"]["pyfltr"],
                "env_vars": list(
                    mcp_environment.environment_variable_names(toolkit_config.config_environment_variable_names())
                ),
            },
            "agents_server": {
                "type": "stdio",
                "command": "uv",
                "args": [
                    "run",
                    "--no-config",
                    "--no-project",
                    "--python",
                    "3.15",
                    "python",
                    "scripts/launch.py",
                    "agent_toolkit/agents_server_mcp.py",
                ],
                "cwd": "./",
                "env_vars": list(
                    mcp_environment.environment_variable_names(toolkit_config.config_environment_variable_names())
                ),
            },
        },
    }
    assert json.loads(codex_mcp_text) == expected_codex_mcp
    assert json.loads(agent_mcp_text) == expected_mcp
    assert codex_mcp_text.endswith("\n")
    assert agent_mcp_text.endswith("\n")
    generated_hooks = json.loads((manifest_root / subject.HOOKS_TARGET).read_text(encoding="utf-8"))
    assert generated_hooks == {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": subject.CODEX_HOOK_ALLOWLIST["PreToolUse"].matcher,
                    "hooks": [{"type": "command", "command": _CODEX_HOOK_PREFIX + "pretooluse" + subject.HOOK_FAILURE_SUFFIX}],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": subject.CODEX_HOOK_ALLOWLIST["PostToolUse"].matcher,
                    "hooks": [{"type": "command", "command": _CODEX_HOOK_PREFIX + "posttooluse" + subject.HOOK_FAILURE_SUFFIX}],
                }
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {"type": "command", "command": _CODEX_HOOK_PREFIX + "user_prompt_submit" + subject.HOOK_FAILURE_SUFFIX}
                    ],
                }
            ],
            "SubagentStop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": _CODEX_HOOK_PREFIX + "subagent_stop_advisor" + subject.HOOK_FAILURE_SUFFIX,
                        }
                    ],
                }
            ],
            "Stop": [{"hooks": [{"type": "command", "command": _CODEX_HOOK_PREFIX + "stop" + subject.HOOK_FAILURE_SUFFIX}]}],
            "SessionEnd": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": _CODEX_HOOK_PREFIX + "session_end_cleanup" + subject.HOOK_FAILURE_SUFFIX,
                            "timeout": subject.CODEX_SESSION_END_TIMEOUT_SECONDS,
                        }
                    ],
                }
            ],
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": _CODEX_HOOK_PREFIX + "rules_context_codex" + subject.HOOK_FAILURE_SUFFIX,
                            "additionalContextLimit": 0,
                        }
                    ],
                }
            ],
            "SubagentStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": _CODEX_HOOK_PREFIX + "rules_context_codex" + subject.HOOK_FAILURE_SUFFIX,
                            "additionalContextLimit": 0,
                        }
                    ]
                }
            ],
            "Interrupt": [
                {
                    "hooks": [
                        {"type": "command", "command": _CODEX_HOOK_PREFIX + "session_activity" + subject.HOOK_FAILURE_SUFFIX}
                    ]
                }
            ],
        }
    }
    assert len(generated_hooks["hooks"]) == 9
    assert (
        generated_hooks["hooks"]["SubagentStart"][0]["hooks"][0]["command"]
        == _CODEX_HOOK_PREFIX + "rules_context_codex" + subject.HOOK_FAILURE_SUFFIX
    )
    assert (manifest_root / subject.PLUGIN_TARGET).read_text(encoding="utf-8").endswith("\n")


def test_sync_ignores_tracked_file_deleted_from_worktree(manifest_root: Path) -> None:
    """commit前の追跡ファイル削除をCodex pluginの原本集合から除く。"""
    deleted = manifest_root / "agent-toolkit/deleted.md"
    deleted.write_text("deleted\n", encoding="utf-8")
    subprocess.run(["git", "add", "agent-toolkit/deleted.md"], cwd=manifest_root, check=True)
    deleted.unlink()

    assert subject.sync(manifest_root) is True
    assert not (manifest_root / subject.CODEX_PLUGIN_ROOT_TARGET / "deleted.md").exists()


def test_sync_includes_readme_and_license(manifest_root: Path) -> None:
    """既存の原本列挙で導入文書・ライセンスをCodex配布物へ同梱し、更新にも追随する。"""
    originals = {"README.md": "# toolkit\n\n[ライセンス](LICENSE)\n", "LICENSE": "MIT toolkit\n"}
    for name, body in originals.items():
        (manifest_root / "agent-toolkit" / name).write_text(body, encoding="utf-8")
    subject.sync(manifest_root)
    for name, body in originals.items():
        assert (manifest_root / subject.CODEX_PLUGIN_ROOT_TARGET / name).read_text(encoding="utf-8") == body
        (manifest_root / "agent-toolkit" / name).write_text(body + "更新\n", encoding="utf-8")
    assert subject.sync(manifest_root)
    for name, body in originals.items():
        assert (manifest_root / subject.CODEX_PLUGIN_ROOT_TARGET / name).read_text(encoding="utf-8") == body + "更新\n"


__all__ = ["manifest_root_fixture"]
