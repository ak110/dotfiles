"""sync_codex_plugin_manifestsのテスト。"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import sync_codex_plugin_manifests as subject

from pytools._internal import claude_common

_CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
_CODEX_PLUGIN_VALIDATOR = _CODEX_HOME / "skills/.system/plugin-creator/scripts/validate_plugin.py"
_CODEX_PLUGIN_VALIDATOR_AVAILABLE = shutil.which("codex") is not None and _CODEX_PLUGIN_VALIDATOR.is_file()


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
                    "SessionEnd": [
                        {
                            "hooks": [{"type": "command", "command": subject.CODEX_SESSION_END_COMMAND}],
                        }
                    ],
                    "PermissionRequest": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "uv run --no-project --script "
                                    "${CLAUDE_PLUGIN_ROOT}/scripts/hook.py permissionrequest",
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
    return tmp_path


def test_sync_is_deterministic(manifest_root: Path) -> None:
    assert subject.sync(manifest_root) is True
    assert subject.sync(manifest_root) is False
    generated = json.loads((manifest_root / subject.PLUGIN_TARGET).read_text(encoding="utf-8"))
    for key, value in _plugin_data().items():
        assert generated[key] == value
    assert generated["hooks"] == "./hooks/hooks.codex.json"
    agent_plugin_text = (manifest_root / subject.AGENT_PLUGIN_TARGET).read_text(encoding="utf-8")
    agent_plugin = json.loads(agent_plugin_text)
    assert agent_plugin == {"$schema": subject.AGENT_PLUGIN_SCHEMA, **_plugin_data()}
    assert agent_plugin_text.endswith("\n")
    codex_mcp_text = (manifest_root / subject.MCP_CODEX_TARGET).read_text(encoding="utf-8")
    agent_mcp_text = (manifest_root / subject.AGENT_MCP_TARGET).read_text(encoding="utf-8")
    expected_mcp = {
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
    assert json.loads(codex_mcp_text) == expected_mcp
    assert json.loads(agent_mcp_text) == expected_mcp
    assert codex_mcp_text.endswith("\n")
    assert agent_mcp_text.endswith("\n")
    generated_hooks = json.loads((manifest_root / subject.HOOKS_TARGET).read_text(encoding="utf-8"))
    assert generated_hooks == {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": subject.CODEX_HOOK_ALLOWLIST["PreToolUse"].matcher,
                    "hooks": [{"type": "command", "command": subject.CODEX_PRE_TOOL_USE_COMMAND}],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": subject.CODEX_HOOK_ALLOWLIST["PostToolUse"].matcher,
                    "hooks": [{"type": "command", "command": subject.CODEX_POST_TOOL_USE_COMMAND}],
                }
            ],
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
            "SubagentStop": [
                {
                    "hooks": [{"type": "command", "command": subject.CODEX_SUBAGENT_STOP_COMMAND}],
                }
            ],
            "SessionEnd": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": subject.CODEX_SESSION_END_COMMAND,
                            "timeout": subject.CODEX_SESSION_END_TIMEOUT_SECONDS,
                        }
                    ],
                }
            ],
            "SessionStart": [
                {
                    "matcher": "compact",
                    "hooks": [{"type": "command", "command": subject.CODEX_QUALITY_CHECKPOINT_COMMAND}],
                }
            ],
        }
    }
    assert len(generated_hooks["hooks"]) == 7
    assert (manifest_root / subject.PLUGIN_TARGET).read_text(encoding="utf-8").endswith("\n")


def test_codex_interface_descriptions_and_prompts(manifest_root: Path) -> None:
    """Codex向けinterfaceが紹介文と起動プロンプトの契約を満たす。"""
    subject.sync(manifest_root)
    generated = json.loads((manifest_root / subject.PLUGIN_TARGET).read_text(encoding="utf-8"))
    interface = generated["interface"]

    assert isinstance(interface["longDescription"], str)
    assert interface["longDescription"]
    assert isinstance(interface["defaultPrompt"], list)
    assert interface["defaultPrompt"]
    assert all(isinstance(prompt, str) and prompt and len(prompt) <= 128 for prompt in interface["defaultPrompt"])


@pytest.mark.skipif(
    not _CODEX_PLUGIN_VALIDATOR_AVAILABLE,
    reason="Codex CLI又は同梱plugin検証器が存在しない",
)
def test_codex_plugin_validator_reports_only_known_schema_deviations() -> None:
    """Codex検証器の既知の指摘集合だけを許容する。

    Codex 0.151.0同梱の`plugin-creator/references/plugin-json-spec.md`は、`hooks`を正規fieldとして
    定義しながら、検証の節では未対応fieldとして拒否すると述べており、同一資料内で矛盾する。
    `hooks`と`./.mcp.codex.json`を持つ現行manifestは`installed: true`かつ`enabled: true`である。
    資料上の保証がないまま動作中の構成を変えないため、この前提が変わるまで期待値を空にしない。
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_CODEX_PLUGIN_VALIDATOR), str(subject.REPO_ROOT / "agent-toolkit")],
        capture_output=True,
        check=False,
        text=True,
    )
    findings = {line.removeprefix("- ") for line in result.stdout.splitlines() if line.startswith("- ")}
    expected = {
        "plugin.json field `hooks` is not accepted by plugin validation",
        "plugin.json field `mcpServers` must resolve to `.mcp.json`",
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
    assert generated["PostToolUse"][0]["matcher"] == subject.CODEX_HOOK_ALLOWLIST["PostToolUse"].matcher
    assert generated["SessionEnd"][0]["hooks"][0]["timeout"] <= 3
    assert "matcher" not in generated["SubagentStop"][0]
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
    """許可表に無いイベントは正本にあってもCodexへ配布しない。"""
    hooks = json.loads((manifest_root / subject.HOOKS_SOURCE).read_text(encoding="utf-8"))
    hooks["hooks"]["SubagentStart"] = [{"hooks": [{"type": "command", "command": "uv run --no-project --script other.py"}]}]
    (manifest_root / subject.HOOKS_SOURCE).write_text(json.dumps(hooks), encoding="utf-8")

    subject.sync(manifest_root)

    generated = json.loads((manifest_root / subject.HOOKS_TARGET).read_text(encoding="utf-8"))["hooks"]
    assert "SubagentStart" not in generated
    assert "SessionStart" in generated
    assert set(generated) == set(subject.CODEX_HOOK_ALLOWLIST) | {"SessionStart"}


def test_rejects_codex_only_event_collision(manifest_root: Path) -> None:
    """Codex専用イベントをClaude向け正本へ重ねて登録しない。"""
    hooks = json.loads((manifest_root / subject.HOOKS_SOURCE).read_text(encoding="utf-8"))
    hooks["hooks"]["SessionStart"] = [{"matcher": "compact", "hooks": []}]
    (manifest_root / subject.HOOKS_SOURCE).write_text(json.dumps(hooks), encoding="utf-8")

    with pytest.raises(ValueError, match="衝突"):
        subject.sync(manifest_root)


def test_sync_reads_all_json_as_utf8(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同期中の全JSON読取が既定ロケールを使用しないことを確認する。"""
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
    with pytest.raises(ValueError, match="MCP正本はmcpServersだけ"):
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
    assert json.loads(stale_hooks.read_text(encoding="utf-8"))["hooks"]["PermissionRequest"][0]["matcher"] == "Bash"


def test_check_accepts_current_outputs_without_changes(manifest_root: Path) -> None:
    subject.sync(manifest_root)
    before = {
        path: (manifest_root / path).read_text(encoding="utf-8")
        for path in (*subject._outputs(manifest_root), *subject.OPTIONAL_TARGETS)  # pylint: disable=protected-access
        if (manifest_root / path).exists()
    }

    assert subject.check(manifest_root) is True

    after = {
        path: (manifest_root / path).read_text(encoding="utf-8")
        for path in (*subject._outputs(manifest_root), *subject.OPTIONAL_TARGETS)  # pylint: disable=protected-access
        if (manifest_root / path).exists()
    }
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


def test_main_check_exit_codes(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "REPO_ROOT", manifest_root)
    subject.sync(manifest_root)

    assert subject.main(["--check"]) == 0
    (manifest_root / subject.PLUGIN_TARGET).write_text("{}", encoding="utf-8")
    assert subject.main(["--check"]) == 1


def test_main_without_arguments_synchronizes_outputs(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "REPO_ROOT", manifest_root)

    assert subject.main([]) == 0
    assert subject.check(manifest_root) is True


def test_main_rejects_unknown_arguments() -> None:
    with pytest.raises(SystemExit) as error:
        subject.main(["--unknown"])
    assert error.value.code == 2


def test_rejects_missing_allowlisted_handler(manifest_root: Path) -> None:
    hooks = json.loads((manifest_root / subject.HOOKS_SOURCE).read_text(encoding="utf-8"))
    hooks["hooks"]["PermissionRequest"][0]["hooks"][0]["command"] = "unknown"
    (manifest_root / subject.HOOKS_SOURCE).write_text(json.dumps(hooks), encoding="utf-8")
    with pytest.raises(ValueError, match="許可済みハンドラー"):
        subject.sync(manifest_root)


@pytest.mark.parametrize("event", ["UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStop", "SessionEnd"])
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
    monkeypatch.setattr(claude_common, "atomic_write_text", lambda *args, **kwargs: False)
    with pytest.raises(OSError, match="書き込みに失敗"):
        subject.sync(manifest_root)
