"""codex_plugin_manifests試験の共通fixture・入力と補助関数。"""

import contextlib
import http.server
import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_toolkit._agents_server import codex as codex_backend

from pytools._internal import codex_plugin_manifests as subject

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


async def _codex_hooks(codex_home: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    async def ignore(_message: dict[str, Any]) -> None:
        return None

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(codex_backend, "APP_SERVER_WORKING_DIRECTORY", str(root))
    async with codex_backend.JsonRpcProcess(ignore, ignore) as client:
        await client.start()
        return await client.request("hooks/list")


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


async def _ignore_proxy_notification(_message: dict[str, Any]) -> None:
    return None


class _ProxyCodexClient(codex_backend.JsonRpcProcess):
    """試験用履歴の注入だけに実験APIを有効化する。"""

    async def request(self, method: str, params: dict[str, Any] | None = None, *, on_sent: Any = None) -> dict[str, Any]:
        if method == "initialize":
            params = {**(params or {}), "capabilities": {"experimentalApi": True}}
        return await super().request(method, params, on_sent=on_sent)
