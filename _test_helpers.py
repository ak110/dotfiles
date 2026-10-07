"""リポジトリ直下のテストが共有する補助関数と定数。

`scripts/`配下のテストの補助は`scripts/_scripts_test_helpers.py`が持つ。pytestのprependモードでは直下と`scripts/`が
ともに`sys.path`へ入り、同名のトップレベルモジュールは先に読み込んだ方だけが`sys.modules`に残るため、名前を分ける。
"""

import functools
import http.server
import json
import os
import pathlib
import shlex
import shutil
import socketserver
import stat
import subprocess
import sys
import threading
import typing

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent
RULES_SRC = REPO_ROOT / "agent-toolkit" / "rules"
INSTALL_SH = REPO_ROOT / "install-claude.sh"
INSTALL_PS1 = REPO_ROOT / "install-claude.ps1"

_COMMAND_STUB = """#!/bin/sh
command_name=$(basename "$0")
printf '%s %s\\n' "$command_name" "$*" >> "$CLI_STUB_LOG"
case "$command_name $*" in
    *"$STUB_FAIL_PATTERN"*)
        printf 'stub failure: %s %s\n' "$command_name" "$*" >&2
        exit 9
        ;;
esac
if [ "$command_name $*" = "codex plugin list --json" ]; then
    state_index=0
    if [ -f "$CODEX_PLUGIN_STATE_FILE" ]; then
        state_index=$(cat "$CODEX_PLUGIN_STATE_FILE")
    fi
    if [ "$state_index" -eq 0 ]; then
        plugin_version="$CODEX_PLUGIN_BEFORE_VERSION"
        plugin_enabled="$CODEX_PLUGIN_BEFORE_ENABLED"
    else
        plugin_version="$CODEX_PLUGIN_AFTER_VERSION"
        plugin_enabled="$CODEX_PLUGIN_AFTER_ENABLED"
    fi
    printf '%s\n' "$((state_index + 1))" > "$CODEX_PLUGIN_STATE_FILE"
    if [ "$state_index" -eq 0 ] && [ -n "$CODEX_PLUGIN_BEFORE_JSON" ]; then
        printf '%s\n' "$CODEX_PLUGIN_BEFORE_JSON"
        exit 0
    fi
    if [ "$plugin_version" = "__missing__" ]; then
        printf '{"installed":[]}\n'
    else
        printf '{"installed":[{"pluginId":"agent-toolkit@ak110-dotfiles","version":"%s","enabled":%s}]}\n' \
            "$plugin_version" "$plugin_enabled"
    fi
    exit 0
fi
if [ "$command_name $*" = "codex plugin marketplace list --json" ]; then
    printf '{"marketplaces":[{"name":"ak110-dotfiles","root":"%s"}]}\n' "$STUB_MARKETPLACE_ROOT"
    exit 0
fi
if [ "$command_name $*" = "codex plugin add agent-toolkit@ak110-dotfiles --json" ]; then
    rm -rf "$CODEX_PLUGIN_CACHE_ROOT"
    if [ "$CODEX_STUB_CREATE_CACHE" = "1" ]; then
        mkdir -p "$CODEX_PLUGIN_CACHE_ROOT/$CODEX_PLUGIN_AFTER_VERSION/agent_toolkit"
        printf 'current hook\n' > "$CODEX_PLUGIN_CACHE_ROOT/$CODEX_PLUGIN_AFTER_VERSION/agent_toolkit/hook.py"
        printf 'agents server\n' > "$CODEX_PLUGIN_CACHE_ROOT/$CODEX_PLUGIN_AFTER_VERSION/agent_toolkit/agents_server_mcp.py"
    fi
    if [ -n "$CODEX_STUB_CONFLICT_VERSION" ]; then
        mkdir -p "$CODEX_PLUGIN_CACHE_ROOT/$CODEX_STUB_CONFLICT_VERSION"
        printf 'keep\n' > "$CODEX_PLUGIN_CACHE_ROOT/$CODEX_STUB_CONFLICT_VERSION/keep"
    fi
    exit 0
fi
if [ "$command_name $*" = "codex app-server daemon version" ]; then
    [ "$CODEX_DAEMON_RUNNING" = "1" ]
    exit $?
fi
exit 0
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        del args, kwargs


def serve_rules() -> typing.Iterator[str]:
    handler = functools.partial(_QuietHandler, directory=str(REPO_ROOT))

    class _Server(socketserver.TCPServer):
        allow_reuse_address = True

    with _Server(("127.0.0.1", 0), handler) as server:
        port = typing.cast(tuple[str, int], server.server_address)[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}/agent-toolkit/rules"
        finally:
            server.shutdown()
            thread.join()


def installer_runners() -> list[object]:
    params: list[object] = [pytest.param("sh", id="sh")]
    if shutil.which("pwsh"):
        params.append(pytest.param("ps1", id="ps1"))
    else:
        params.append(pytest.param("ps1", id="ps1", marks=pytest.mark.skip(reason="pwsh未インストール")))
    return params


def make_command_stubs(
    tmp_path: pathlib.Path,
    *,
    omit: str | None = None,
) -> tuple[pathlib.Path, pathlib.Path]:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "cli.log"
    log_path.touch()
    executable_mode = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH

    for command_name in ("claude", "codex"):
        if command_name == omit:
            continue
        stub = bin_dir / command_name
        stub.write_text(_COMMAND_STUB, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | executable_mode)

    if omit != "uv":
        uv_stub = bin_dir / "uv"
        python_command = shlex.quote(sys.executable)
        uv_stub.write_text(
            "#!/bin/sh\n"
            'printf \'uv %s\\n\' "$*" >> "$CLI_STUB_LOG"\n'
            'if [ "$#" -lt 8 ] || [ "$1" != run ] || [ "$2" != --no-config ] || '
            '[ "$3" != --no-project ] || [ "$4" != --python ] || [ "$5" != 3 ] || '
            '[ "$6" != python ] || [ "$7" != - ]; then\n'
            "    exit 8\n"
            "fi\n"
            "shift 6\n"
            f'exec {python_command} "$@"\n',
            encoding="utf-8",
        )
        uv_stub.chmod(uv_stub.stat().st_mode | executable_mode)

    return bin_dir, log_path


def run_installer(
    kind: str,
    home: pathlib.Path,
    rules_url: str,
    *,
    stub_bin: pathlib.Path | None,
    stub_log: pathlib.Path,
    cwd: pathlib.Path | None = None,
    fail_pattern: str = "__never_match__",
    codex_plugin_before: tuple[str, bool] | None = None,
    codex_plugin_before_json: str = "",
    codex_plugin_after: tuple[str, bool] | None = ("1.2.3", True),
    codex_daemon_running: bool = True,
    codex_home: pathlib.Path | None = None,
    conflict_version: str = "",
    create_cache: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    path_parts = [str(stub_bin)] if stub_bin is not None else []
    path_parts.extend(["/usr/bin", "/bin"])
    if kind == "ps1" and (pwsh := shutil.which("pwsh")):
        pwsh_dir = str(pathlib.Path(pwsh).parent)
        if pwsh_dir not in path_parts:
            path_parts.insert(1 if stub_bin is not None else 0, pwsh_dir)

    before_version, before_enabled = (
        ("__missing__", "false")
        if codex_plugin_before is None
        else (codex_plugin_before[0], str(codex_plugin_before[1]).lower())
    )
    after_version, after_enabled = (
        ("__missing__", "false") if codex_plugin_after is None else (codex_plugin_after[0], str(codex_plugin_after[1]).lower())
    )
    effective_codex_home = codex_home or home / ".codex"
    marketplace_root = home / "codex-marketplace"
    marketplace_manifest = marketplace_root / ".agents" / "plugins" / "marketplace.json"
    marketplace_manifest.parent.mkdir(parents=True, exist_ok=True)
    marketplace_manifest.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "agent-toolkit",
                        "source": {"source": "local", "path": "./agent-toolkit-codex"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = marketplace_root / "agent-toolkit-codex" / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"version": after_version}), encoding="utf-8")
    claude_plugin_root = home / "claude-plugin"
    claude_plugin_script = claude_plugin_root / "agent_toolkit" / "agents_server_mcp.py"
    claude_plugin_script.parent.mkdir(parents=True, exist_ok=True)
    claude_plugin_script.write_text("agents server\n", encoding="utf-8")
    installed_plugins = home / ".claude" / "plugins" / "installed_plugins.json"
    installed_plugins.parent.mkdir(parents=True, exist_ok=True)
    installed_plugins.write_text(
        json.dumps(
            {
                "plugins": {
                    "agent-toolkit@ak110-dotfiles": [
                        {"installPath": str(claude_plugin_root)},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    if before_version != "__missing__":
        old_cache = effective_codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit" / before_version / "scripts"
        old_cache.mkdir(parents=True, exist_ok=True)
        (old_cache / "hook.py").write_text("old hook\n", encoding="utf-8")
    env = {
        "HOME": str(home),
        "PATH": os.pathsep.join(path_parts),
        "DOTFILES_RULES_URL": rules_url,
        "CLI_STUB_LOG": str(stub_log),
        "STUB_FAIL_PATTERN": fail_pattern,
        "CODEX_PLUGIN_STATE_FILE": str(stub_log.with_suffix(".codex-plugin-state")),
        "CODEX_PLUGIN_BEFORE_VERSION": before_version,
        "CODEX_PLUGIN_BEFORE_ENABLED": before_enabled,
        "CODEX_PLUGIN_BEFORE_JSON": codex_plugin_before_json,
        "CODEX_PLUGIN_AFTER_VERSION": after_version,
        "CODEX_PLUGIN_AFTER_ENABLED": after_enabled,
        "CODEX_DAEMON_RUNNING": "1" if codex_daemon_running else "0",
        "CODEX_HOME": str(effective_codex_home),
        "CODEX_PLUGIN_CACHE_ROOT": str(effective_codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit"),
        "CODEX_STUB_CONFLICT_VERSION": conflict_version,
        "CODEX_STUB_CREATE_CACHE": "1" if create_cache else "0",
        "STUB_MARKETPLACE_ROOT": str(marketplace_root),
    }
    command = (
        ["bash", str(INSTALL_SH)] if kind == "sh" else ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(INSTALL_PS1)]
    )
    return subprocess.run(command, cwd=cwd, env=env, check=check, capture_output=True, text=True)


def read_log_lines(log_path: pathlib.Path) -> list[str]:
    return [line for line in log_path.read_text(encoding="utf-8").splitlines() if line]


WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yaml"


def workflow_mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return typing.cast(dict[str, object], value)


def workflow_steps(job: dict[str, object]) -> list[dict[str, object]]:
    value = job["steps"]
    assert isinstance(value, list)
    return [workflow_mapping(step) for step in value]


def workflow_jobs(workflow: dict[str, object]) -> dict[str, object]:
    return workflow_mapping(workflow["jobs"])


def load_workflow() -> dict[str, object]:
    with WORKFLOW_PATH.open(encoding="utf-8") as stream:
        # BaseLoaderはYAML 1.1の`on`キーを真偽値へ変換せず、安全なスカラー値だけを構築する。
        value = yaml.load(stream, Loader=yaml.BaseLoader)
    return workflow_mapping(value)


def direct_pytest_targets(workflow: dict[str, object]) -> list[str]:
    targets: list[str] = []
    for value in workflow_jobs(workflow).values():
        for step in workflow_steps(workflow_mapping(value)):
            command = step.get("run")
            if not isinstance(command, str) or "pytest" not in command.split():
                continue
            tokens = shlex.split(command)
            if "pytest" not in tokens:
                continue
            pytest_index = tokens.index("pytest")
            targets.extend(
                token
                for token in tokens[pytest_index + 1 :]
                if not token.startswith("-") and ("/" in token or token.endswith(".py"))
            )
    return targets


def flag_options(tokens: list[str]) -> set[str]:
    """値を取る`--project`を除いた`--`始まりのオプションを返す。"""
    return {token for token in tokens if token.startswith("--") and token != "--project"}
