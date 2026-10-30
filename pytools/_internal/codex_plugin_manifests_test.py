"""pytools._internal.codex_plugin_manifests のテスト。"""

import asyncio
import contextlib
import http.server
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._atk import config as toolkit_config
from agent_toolkit._common import mcp_environment, proxy_environment
from agent_toolkit._testing import isolation

from pytools._internal import codex_plugin_manifests as subject
from pytools._internal import common

_CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
_CODEX_PLUGIN_VALIDATOR = _CODEX_HOME / "skills/.system/plugin-creator/scripts/validate_plugin.py"
_CODEX_PLUGIN_VALIDATOR_AVAILABLE = shutil.which("codex") is not None and _CODEX_PLUGIN_VALIDATOR.is_file()
_CODEX_HOOK_PREFIX = (
    'uv run --no-config --no-project --python 3.15 python "${PLUGIN_ROOT}/scripts/launch.py" agent_toolkit/hook.py '
)


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


def _basics_plugin_data() -> dict[str, Any]:
    return {**_plugin_data(), "name": "agent-basics", "version": "1.0.0"}


@pytest.fixture(name="manifest_root")
def manifest_root_fixture(tmp_path: Path) -> Path:
    """同期の入力となるplugin.json、marketplace.jsonなどの最小fixtureを作成する。"""
    plugin = _plugin_data()
    basics = _basics_plugin_data()
    marketplace = {
        "name": "ak110-dotfiles",
        "plugins": [{**plugin, "source": "./agent-toolkit"}, {**basics, "source": "./agent-basics"}],
    }
    fixtures: tuple[tuple[Path, dict[str, Any]], ...] = (
        (subject.PLUGIN_SOURCE, {**plugin, "dependencies": ["agent-basics"]}),
        (subject.BASICS_PLUGIN_SOURCE, basics),
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
                    },
                    "agents_server": {
                        "command": "${CLAUDE_PLUGIN_ROOT}/bin/agents-server",
                        "args": ["--script", "${CLAUDE_PLUGIN_ROOT}/scripts/agents_server_mcp.py"],
                        "env": {"SCRIPT_ROOT": "${CLAUDE_PLUGIN_ROOT}/data"},
                        "cwd": "${CLAUDE_PLUGIN_ROOT}/workspace",
                    },
                }
            },
        ),
        (
            subject.HOOKS_SOURCE,
            {
                "hooks": {
                    "SessionStart": [{"hooks": [{"type": "command", "command": subject.CODEX_SESSION_START_COMMAND}]}],
                    "SubagentStart": [{"hooks": [{"type": "command", "command": subject.CODEX_RULES_CONTEXT_COMMAND}]}],
                    "PreToolUse": [
                        {
                            "matcher": "",
                            "hooks": [{"type": "command", "command": subject.CODEX_PRE_TOOL_USE_COMMAND}],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "Write|Edit|MultiEdit|Bash|Skill",
                            "hooks": [{"type": "command", "command": subject.CODEX_POST_TOOL_USE_COMMAND}],
                        }
                    ],
                    "SubagentStop": [
                        {
                            "hooks": [{"type": "command", "command": subject.CODEX_SUBAGENT_STOP_COMMAND}],
                        }
                    ],
                    "Stop": [{"hooks": [{"type": "command", "command": subject.CODEX_STOP_COMMAND}]}],
                    "SessionEnd": [
                        {
                            "hooks": [{"type": "command", "command": subject.CODEX_SESSION_END_COMMAND, "async": True}],
                        }
                    ],
                    "PermissionRequest": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "uv run --no-config --no-project --python 3.15 python "
                                    '"${CLAUDE_PLUGIN_ROOT}/scripts/launch.py" agent_toolkit/hook.py permissionrequest'
                                    " || exit 1",
                                }
                            ],
                        },
                    ],
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {"type": "command", "command": subject.CODEX_USER_PROMPT_SUBMIT_COMMAND},
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
    (tmp_path / ".gitignore").write_text("agent-toolkit/.ruff_cache/\n", encoding="utf-8")
    cache = tmp_path / "agent-toolkit/.ruff_cache/cache-entry"
    cache.parent.mkdir(parents=True)
    cache.write_text("ignored", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    quality = tmp_path / subject.QUALITY_SOURCE
    quality.parent.mkdir(parents=True)
    quality.write_text(
        "<!-- portable-quality:start -->\n\n## 基準\n\n本文\n\n<!-- portable-quality:end -->\n", encoding="utf-8"
    )
    return tmp_path


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


@pytest.fixture(name="quality_root")
def _quality_root(manifest_root: Path) -> Path:
    """品質原本と、単独配布する2スキルを持つ最小の配布元を用意する。"""
    for distribution, name in (
        (distribution, name)
        for distribution in ("agent-toolkit", "agent-basics")
        for name in ("alpha", "beta", "writing-standards")
    ):
        path = manifest_root / distribution / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        reference = "writing.md" if name == "writing-standards" else "quality-standards.md"
        path.write_text(f"# {name}\n\n[品質基準](references/{reference})\n", encoding="utf-8")
    source = manifest_root / subject.QUALITY_SOURCE
    source.parent.mkdir(exist_ok=True)
    source.write_text(
        "# 原本\n\n配布の説明。\n\n<!-- portable-quality:start -->\n\n"
        "## 日本語の書き方\n\n主体と対象を明示する。\n\n"
        "<!-- portable-quality:end -->\n\n別の設計資料へ接続する。\n",
        encoding="utf-8",
    )
    return manifest_root


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
                    "hooks": [
                        {
                            "type": "command",
                            "command": _CODEX_HOOK_PREFIX + "posttooluse" + subject.HOOK_FAILURE_SUFFIX,
                            "additionalContextLimit": 0,
                        }
                    ],
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


def test_codex_interface_descriptions_and_prompts(manifest_root: Path) -> None:
    """Codex向けinterfaceが`longDescription`と`defaultPrompt`の契約を満たす。"""
    subject.sync(manifest_root)
    generated = json.loads((manifest_root / subject.PLUGIN_TARGET).read_text(encoding="utf-8"))
    interface = generated["interface"]

    assert isinstance(interface["longDescription"], str)
    assert interface["longDescription"]
    assert isinstance(interface["defaultPrompt"], list)
    assert interface["defaultPrompt"]
    assert all(isinstance(prompt, str) and prompt and len(prompt) <= 128 for prompt in interface["defaultPrompt"])


async def _codex_hooks(codex_home: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    async def ignore(_message: dict[str, Any]) -> None:
        return None

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(codex_backend, "APP_SERVER_WORKING_DIRECTORY", str(root))
    async with codex_backend.JsonRpcProcess(ignore, ignore) as client:
        await client.start()
        return await client.request("hooks/list")


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLIが存在しない")
async def test_codex_0154_registers_all_hooks_independent_of_project_trust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex 0.154.0は`agent-toolkit-codex/`から9イベントを登録し、project trustで集合を変えない。"""
    # 実機のCLIを起動するため、テストの共通設定が隔離するホームとPATHを戻す。
    # CLIの有無はモジュール読込時にホストのPATHで判定済み。
    isolation.restore_host_environment(monkeypatch)
    version = subprocess.run(  # noqa: S603
        ["codex", "--version"], capture_output=True, check=True, text=True
    ).stdout.strip()
    if version != "codex-cli 0.154.0":
        pytest.skip(f"Codex 0.154.0専用のテスト: {version}")

    expected = {event[0].lower() + event[1:] for event in (*subject.CODEX_HOOK_ALLOWLIST, *subject.CODEX_ONLY_HOOKS)}
    observed: list[set[str]] = []
    for trust in (False, True):
        codex_home = tmp_path / ("trusted" if trust else "default")
        codex_home.mkdir()
        if trust:
            quoted_root = str(subject.repo_root()).replace("\\", "\\\\").replace('"', '\\"')
            (codex_home / "config.toml").write_text(f'[projects."{quoted_root}"]\ntrust_level = "trusted"\n', encoding="utf-8")
        environment = {**os.environ, "CODEX_HOME": str(codex_home)}
        subprocess.run(  # noqa: S603
            ["codex", "plugin", "marketplace", "add", str(subject.repo_root())],
            capture_output=True,
            check=True,
            env=environment,
            text=True,
        )
        subprocess.run(  # noqa: S603
            ["codex", "plugin", "add", "agent-toolkit@ak110-dotfiles"],
            capture_output=True,
            check=True,
            env=environment,
            text=True,
        )
        result = await _codex_hooks(codex_home, subject.repo_root(), monkeypatch)
        data = result.get("data")
        assert isinstance(data, list) and len(data) == 1
        hooks = data[0].get("hooks")
        assert isinstance(hooks, list)
        assert data[0].get("warnings") == []
        assert data[0].get("errors") == []
        observed.append({hook["eventName"] for hook in hooks})
        if not trust:
            hook_environment = {
                **environment,
                "TMPDIR": str(tmp_path / "hook-temp"),
                "XDG_STATE_HOME": str(tmp_path / "hook-state"),
            }
            Path(hook_environment["TMPDIR"]).mkdir()
            session_start = next(hook for hook in hooks if hook["eventName"] == "sessionStart")
            start_command = shlex.split(session_start["command"].removesuffix(subject.HOOK_FAILURE_SUFFIX))
            uv = common.resolve_uv_path()
            assert uv is not None
            start_command[0] = str(uv)
            start_result = subprocess.run(  # noqa: S603
                start_command,
                input=json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "integration-session"}),
                capture_output=True,
                check=False,
                env=hook_environment,
                text=True,
            )
            assert start_result.returncode == 0, start_result.stderr
            additional_context = json.loads(start_result.stdout)["hookSpecificOutput"]["additionalContext"]
            state_files = list(Path(hook_environment["XDG_STATE_HOME"]).rglob("*.json"))
            assert len(state_files) == 1
            managed_path = Path(json.loads(state_files[0].read_text(encoding="utf-8"))["path"])
            assert managed_path.is_dir()
            assert str(managed_path) in additional_context

            session_end = next(hook for hook in hooks if hook["eventName"] == "sessionEnd")
            end_command = shlex.split(session_end["command"].removesuffix(subject.HOOK_FAILURE_SUFFIX))
            end_command[0] = str(uv)
            subprocess.run(  # noqa: S603
                end_command,
                input=json.dumps({"hook_event_name": "SessionEnd", "session_id": "integration-session", "reason": "other"}),
                capture_output=True,
                check=True,
                env=hook_environment,
                text=True,
            )
            assert not managed_path.exists()

    assert observed == [expected, expected]


@pytest.mark.skipif(
    not _CODEX_PLUGIN_VALIDATOR_AVAILABLE,
    reason="Codex CLIまたは同梱plugin検証器が存在しない",
)
def test_codex_plugin_validator_reports_only_known_schema_deviations() -> None:
    """Codex検証器の既知の指摘集合だけを許容する。

    Codex 0.154.0同梱の`plugin-creator/references/plugin-json-spec.md`は、`hooks`を正規fieldとして
    定義しながら、検証の節では未対応fieldとして拒否すると述べており、同一資料内で矛盾する。
    `hooks`と`./.mcp.codex.json`を持つ現行manifestは`installed: true`かつ`enabled: true`である。
    Claude向けfrontmatterの`disable-model-invocation: true`は、Codex向けの
    `agents/openai.yaml`で同じ明示起動限定を表しても検証器が指摘する。
    資料上の保証がないまま動作中の構成を変えないため、この前提が変わるまで期待値を空にしない。
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_CODEX_PLUGIN_VALIDATOR), str(subject.repo_root() / subject.CODEX_PLUGIN_ROOT_TARGET)],
        capture_output=True,
        check=False,
        text=True,
    )
    findings = {line.removeprefix("- ") for line in result.stdout.splitlines() if line.startswith("- ")}
    expected = {
        "plugin.json field `hooks` is not accepted by plugin validation",
        "plugin.json field `mcpServers` must resolve to `.mcp.json`",
        "skill `add-awi-by-user` frontmatter field `disable-model-invocation` must be false",
    }
    details = f"終了コード: {result.returncode}\n標準出力:\n{result.stdout}\n標準エラー:\n{result.stderr}"
    if result.returncode == 0:
        details = "意図的な逸脱が解消されたため、期待値の更新が必要である。\n" + details

    assert result.returncode == 1 and findings == expected, details


def test_codex_projection_limits_matchers_and_timeout(manifest_root: Path) -> None:
    """Claude向けの空matcherと広いmatcherを引き継がず、SessionEndへ上限を明示する。"""
    subject.sync(manifest_root)
    generated = json.loads((manifest_root / subject.HOOKS_TARGET).read_text(encoding="utf-8"))["hooks"]

    assert generated["PreToolUse"][0]["matcher"] == subject.CODEX_HOOK_ALLOWLIST["PreToolUse"].matcher
    assert "spawn_agent" in generated["PreToolUse"][0]["matcher"].split("|")
    assert generated["PostToolUse"][0]["matcher"] == subject.CODEX_HOOK_ALLOWLIST["PostToolUse"].matcher
    assert generated["SessionEnd"][0]["hooks"][0]["timeout"] <= 3
    assert "matcher" not in generated["SubagentStop"][0]
    assert generated["Stop"] == [
        {"hooks": [{"type": "command", "command": _CODEX_HOOK_PREFIX + "stop" + subject.HOOK_FAILURE_SUFFIX}]}
    ]
    assert "Bash" in generated["PostToolUse"][0]["matcher"].split("|")
    assert all(handler["additionalContextLimit"] == 0 for handler in generated["PostToolUse"][0]["hooks"])
    assert all("timeout" not in handler for handler in generated["PreToolUse"][0]["hooks"])


def test_codex_projection_replaces_output_command() -> None:
    projection = subject.CodexHookProjection(("source",), output_command="target")

    assert projection.project({"hooks": []}, [{"type": "command", "command": "source"}]) == {
        "hooks": [{"type": "command", "command": "target"}]
    }


def test_codex_projection_preserves_command_without_replacement() -> None:
    projection = subject.CodexHookProjection(("source",))

    assert projection.project({"hooks": []}, [{"type": "command", "command": "source"}]) == {
        "hooks": [{"type": "command", "command": "source"}]
    }


def test_codex_projection_omits_events_without_allowlisted_handler(manifest_root: Path) -> None:
    """許可表に無いイベントはhooks.jsonにあってもCodexへ配布しない。"""
    hooks = json.loads((manifest_root / subject.HOOKS_SOURCE).read_text(encoding="utf-8"))
    hooks["hooks"]["PreCompact"] = [{"hooks": [{"type": "command", "command": "uv run --no-project --script other.py"}]}]
    (manifest_root / subject.HOOKS_SOURCE).write_text(json.dumps(hooks), encoding="utf-8")

    subject.sync(manifest_root)

    generated = json.loads((manifest_root / subject.HOOKS_TARGET).read_text(encoding="utf-8"))["hooks"]
    assert "PreCompact" not in generated
    assert set(generated) == set(subject.CODEX_HOOK_ALLOWLIST) | set(subject.CODEX_ONLY_HOOKS)


def test_codex_projection_sets_additional_context_limit() -> None:
    group: dict[str, Any] = {"hooks": []}
    handler = [{"type": "command", "command": "source"}]
    projected = subject.CodexHookProjection(("source",), additional_context_limit=0).project(group, handler)
    assert projected["hooks"][0]["additionalContextLimit"] == 0
    projected = subject.CodexHookProjection(("source",)).project(group, handler)
    assert "additionalContextLimit" not in projected["hooks"][0]


def test_sync_reads_all_json_as_utf8(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同期中の全JSON読取がロケールで決まる文字コードを使用しないことを確認する。"""
    subject.sync(manifest_root)
    original_read_text = Path.read_text
    encodings: list[str | None] = []

    def read_text(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        encodings.append(encoding)
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", read_text)

    assert subject.sync(manifest_root) is False
    assert encodings
    assert set(encodings) == {"utf-8"}


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


@pytest.mark.asyncio
async def test_proxy_contract_is_shared_with_codex_backend(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """静的な共通名の追加と動的な親環境の配送を分け、環境によらず共通の生成物を保つ。"""
    names = (*mcp_environment.PROXY_ENVIRONMENT_VARIABLES, "TEST_PROXY")
    monkeypatch.setattr(mcp_environment, "PROXY_ENVIRONMENT_VARIABLES", names)
    monkeypatch.setitem(toolkit_config._MUTABLE_KEY_DEFAULTS, "future_engine", "codex")  # pylint: disable=protected-access
    expected_names = list(mcp_environment.environment_variable_names(toolkit_config.config_environment_variable_names()))
    assert "AGENT_TOOLKIT_CONFIG_FUTURE_ENGINE" in expected_names
    source_before = (manifest_root / subject.MCP_SOURCE).read_bytes()
    subject.sync(manifest_root)
    codex = json.loads((manifest_root / subject.MCP_CODEX_TARGET).read_text(encoding="utf-8"))
    portable = json.loads((manifest_root / subject.AGENT_MCP_TARGET).read_text(encoding="utf-8"))
    for name in ("agents_server", "pyfltr"):
        assert codex["mcpServers"][name]["env_vars"] == expected_names
        assert "env_vars" not in portable["mcpServers"][name]
    before = {path.relative_to(manifest_root): path.read_bytes() for path in manifest_root.rglob("*") if path.is_file()}
    monkeypatch.setenv("TEST_PROXY", "synthetic-proxy")
    monkeypatch.setenv("UNKNOWN_VENDOR_API_KEY", "synthetic-credential")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "synthetic-token")
    async with codex_backend.AppServerManager() as manager:
        backend = manager._agents_server_config("owner", "writer")  # pylint: disable=protected-access
    runtime_names = backend["mcp_servers"]["agents_server"]["env_vars"]
    assert {"TEST_PROXY", "UNKNOWN_VENDOR_API_KEY", "ANTHROPIC_AUTH_TOKEN"} <= set(runtime_names)
    assert "UNKNOWN_VENDOR_API_KEY" not in expected_names
    subject.sync(manifest_root)
    after = {path.relative_to(manifest_root): path.read_bytes() for path in manifest_root.rglob("*") if path.is_file()}
    assert after == before
    assert (manifest_root / subject.MCP_SOURCE).read_bytes() == source_before


@pytest.fixture(name="proxy_endpoint")
def _proxy_endpoint() -> Iterator[tuple[str, list[str]]]:
    """loopbackの専用proxyを試験全体で保持し、失敗時も回収する。"""
    requests: list[str] = []

    class ProxyHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            requests.append(self.path)
            body = b"proxy-observed"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002  # pylint: disable=redefined-builtin
            del format, args  # noqa

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler) as proxy, contextlib.ExitStack() as cleanup:
        proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        proxy_thread.start()
        cleanup.callback(proxy_thread.join, timeout=5)
        cleanup.callback(proxy.shutdown)
        yield f"http://127.0.0.1:{proxy.server_port}", requests
    assert not proxy_thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLIが存在しない")
@pytest.mark.parametrize("index", [0, 1, 2], ids=["plugin-agents-server", "plugin-pyfltr", "backend-agents-server"])
async def test_codex_stdio_inherits_proxy_environment(
    manifest_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, proxy_endpoint: tuple[str, list[str]], index: int
) -> None:
    """実Codexの起動・再開で保存先と公開overrideを保持し、専用proxyへ要求が届く。

    実Codexの起動は1件あたり2回かかるため、設定ごとに試験を分けて60秒の上限を各設定が個別に持つ。
    """
    isolation.restore_host_environment(monkeypatch)
    proxy_url, requests = proxy_endpoint
    inherited = {
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "HTTP_PROXY": proxy_url,
        "no_proxy": "localhost,127.0.0.1",
        "GLOBAL_AGENT_HTTP_PROXY": proxy_url,
    }
    for name in proxy_environment.PROXY_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    namespace = {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "AGENT_TOOLKIT_PRIVATE_NOTES": str(tmp_path / "notes"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "CODEX_HOME": str(codex_home),
        "AGENT_TOOLKIT_CONFIG_BASE_ENGINE": "codex",
        "ANTHROPIC_AUTH_TOKEN": "synthetic-auth-token",
        "ANTHROPIC_AWS_BASE_URL": "https://synthetic.invalid",
        "ANTHROPIC_AWS_WORKSPACE_ID": "synthetic-workspace",
        "CLAUDE_CODE_USE_ANTHROPIC_AWS": "1",
        "CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH": "1",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "synthetic-opus",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "synthetic-sonnet",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "synthetic-haiku",
        "OPENAI_API_KEY": "synthetic-openai-key",
        "GEMINI_API_KEY": "synthetic-gemini-key",
        "DBUS_SESSION_BUS_ADDRESS": "synthetic-keyring-address",
        "LC_ALL": "C",
    }
    for name, value in namespace.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("UNKNOWN_VENDOR_API_KEY", "synthetic-unknown-key")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    monkeypatch.delenv("UNSET_VENDOR_SETTING", raising=False)
    observed_names = [*namespace, "UNKNOWN_VENDOR_API_KEY", "UNSET_VENDOR_SETTING", "CLAUDE_CODE_SESSION_ID"]
    monkeypatch.setattr(codex_backend, "APP_SERVER_WORKING_DIRECTORY", str(tmp_path))
    child = tmp_path / "proxy-child.py"
    child.write_text(
        "import json, os, urllib.request\n"
        "from pathlib import Path\n"
        "response = urllib.request.urlopen('http://proxy-probe.invalid/resource', timeout=5).read().decode()\n"
        "Path(os.environ['OBS_PATH']).write_text(json.dumps({'env': {name: os.environ[name] for name in "
        f"{list(proxy_environment.PROXY_ENVIRONMENT_VARIABLES)!r} if name in os.environ}}, "
        f"'namespace': {{name: os.environ.get(name) for name in {observed_names!r}}}, "
        "'response': response}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "proxy-mcp.py"
    launcher.write_text(
        "import os, subprocess, sys\nfrom pathlib import Path\n"
        "sys.stderr = open(os.environ['OBS_PATH'] + '.stderr', 'w', encoding='utf-8')\n"
        "from mcp.server.mcpserver import MCPServer\n"
        f"subprocess.run([sys.executable, {str(child)!r}], check=True, timeout=10)\n"
        "server = MCPServer('proxy-probe')\n"
        "@server.tool()\ndef observed() -> str:\n    return 'ready'\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    subject.sync(manifest_root)
    plugin_servers = json.loads((manifest_root / subject.MCP_CODEX_TARGET).read_text(encoding="utf-8"))["mcpServers"]
    async with codex_backend.AppServerManager() as manager:
        backend = manager._agents_server_config("owner", "writer")["mcp_servers"]["agents_server"]  # pylint: disable=protected-access
    configurations = [plugin_servers["agents_server"], plugin_servers["pyfltr"], backend]
    processes: list[asyncio.subprocess.Process] = []
    async with contextlib.AsyncExitStack() as resources:
        client = await resources.enter_async_context(_ProxyCodexClient(_ignore_proxy_notification, _ignore_proxy_notification))
        async with asyncio.timeout(60):
            await client.start()
            assert client.process is not None
            processes.append(client.process)
            configuration = configurations[index]
            expected_namespace = {
                **namespace,
                "UNKNOWN_VENDOR_API_KEY": "synthetic-unknown-key" if index == 2 else None,
                "UNSET_VENDOR_SETTING": None,
                "CLAUDE_CODE_SESSION_ID": None,
            }
            output = tmp_path / f"child-{index}.json"
            server = {
                **configuration,
                "command": sys.executable,
                "args": [str(launcher)],
                "cwd": str(tmp_path),
                "env": {**configuration.get("env", {}), "OBS_PATH": str(output)},
            }
            server.pop("type", None)
            response = await client.request(
                "thread/start",
                {
                    "cwd": str(tmp_path),
                    "approvalPolicy": "never",
                    "sandbox": "danger-full-access",
                    "config": {"mcp_servers": {"probe": server}, "features": {"plugins": False, "apps": False}},
                },
            )
            thread_id = response["thread"]["id"]
            inventory = await client.request("mcpServerStatus/list", {"threadId": thread_id, "serverName": "probe"})
            assert inventory["data"], inventory
            diagnostic = Path(str(output) + ".stderr")
            assert inventory["data"][0]["runtimeStatus"] == "connected", json.dumps(inventory) + (
                diagnostic.read_text(encoding="utf-8") if diagnostic.exists() else ""
            )
            observation = json.loads(output.read_text(encoding="utf-8"))
            assert observation == {"env": inherited, "namespace": expected_namespace, "response": "proxy-observed"}
            # モデルを呼ばず履歴を保存する0.162.1の公開実験APIで、cold resumeの入力を準備する。
            await client.request(
                "thread/inject_items",
                {
                    "threadId": thread_id,
                    "items": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fixture"}]}],
                },
            )
            await client.close()
            output.unlink()
            client = await resources.enter_async_context(
                _ProxyCodexClient(_ignore_proxy_notification, _ignore_proxy_notification)
            )
            await client.start()
            assert client.process is not None
            processes.append(client.process)
            await client.request(
                "thread/resume",
                {
                    "threadId": thread_id,
                    "cwd": str(tmp_path),
                    "approvalPolicy": "never",
                    "sandbox": "danger-full-access",
                    "config": {"mcp_servers": {"probe": server}},
                },
            )
            inventory = await client.request("mcpServerStatus/list", {"threadId": thread_id, "serverName": "probe"})
            assert inventory["data"][0]["runtimeStatus"] == "connected", json.dumps(inventory)
            assert json.loads(output.read_text(encoding="utf-8")) == {
                "env": inherited,
                "namespace": expected_namespace,
                "response": "proxy-observed",
            }
            await client.request("thread/unsubscribe", {"threadId": thread_id})
    assert processes and all(process.returncode is not None for process in processes)
    assert requests.count("http://proxy-probe.invalid/resource") == 2


async def _ignore_proxy_notification(_message: dict[str, Any]) -> None:
    return None


class _ProxyCodexClient(codex_backend.JsonRpcProcess):
    """試験用履歴の注入だけに実験APIを有効化する。"""

    async def request(self, method: str, params: dict[str, Any] | None = None, *, on_sent: Any = None) -> dict[str, Any]:
        if method == "initialize":
            params = {**(params or {}), "capabilities": {"experimentalApi": True}}
        return await super().request(method, params, on_sent=on_sent)
