"""agent-toolkit統合インストーラーの公開契約を検証する。"""

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

REPO_ROOT = pathlib.Path(__file__).resolve().parent
RULES_SRC = REPO_ROOT / "agent-toolkit" / "rules"
INSTALL_SH = REPO_ROOT / "install-claude.sh"
INSTALL_PS1 = REPO_ROOT / "install-claude.ps1"

_COMMAND_STUB = """#!/bin/sh
command_name=$(basename "$0")
printf '%s %s\\n' "$command_name" "$*" >> "$CLI_STUB_LOG"
case "$command_name $*" in
    *"$STUB_FAIL_PATTERN"*) exit 9 ;;
esac
exit 0
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        del args, kwargs


@pytest.fixture(name="rules_url", scope="module")
def rules_url_fixture() -> typing.Iterator[str]:
    handler = functools.partial(_QuietHandler, directory=str(RULES_SRC))

    class _Server(socketserver.TCPServer):
        allow_reuse_address = True

    with _Server(("127.0.0.1", 0), handler) as server:
        port = typing.cast(tuple[str, int], server.server_address)[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.shutdown()
            thread.join()


def _runners() -> list[object]:
    params: list[object] = [pytest.param("sh", id="sh")]
    if shutil.which("pwsh"):
        params.append(pytest.param("ps1", id="ps1"))
    else:
        params.append(pytest.param("ps1", id="ps1", marks=pytest.mark.skip(reason="pwsh未インストール")))
    return params


def _make_command_stubs(
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
            'if [ "$#" -ne 8 ] || [ "$1" != run ] || [ "$2" != --no-config ] || '
            '[ "$3" != --no-project ] || [ "$4" != --python ] || [ "$5" != 3 ] || '
            '[ "$6" != python ] || [ "$7" != - ]; then\n'
            "    exit 8\n"
            "fi\n"
            f'exec {python_command} "$7" "$8"\n',
            encoding="utf-8",
        )
        uv_stub.chmod(uv_stub.stat().st_mode | executable_mode)

    return bin_dir, log_path


def _run(
    kind: str,
    home: pathlib.Path,
    rules_url: str,
    *,
    stub_bin: pathlib.Path | None,
    stub_log: pathlib.Path,
    cwd: pathlib.Path | None = None,
    fail_pattern: str = "__never_match__",
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    path_parts = [str(stub_bin)] if stub_bin is not None else []
    path_parts.extend(["/usr/bin", "/bin"])
    if kind == "ps1" and (pwsh := shutil.which("pwsh")):
        pwsh_dir = str(pathlib.Path(pwsh).parent)
        if pwsh_dir not in path_parts:
            path_parts.insert(1 if stub_bin is not None else 0, pwsh_dir)

    env = {
        "HOME": str(home),
        "PATH": os.pathsep.join(path_parts),
        "DOTFILES_RULES_URL": rules_url,
        "CLI_STUB_LOG": str(stub_log),
        "STUB_FAIL_PATTERN": fail_pattern,
    }
    command = (
        ["bash", str(INSTALL_SH)] if kind == "sh" else ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(INSTALL_PS1)]
    )
    return subprocess.run(command, cwd=cwd, env=env, check=check, capture_output=True, text=True)


def _write_claude_config(home: pathlib.Path, value: object) -> None:
    (home / ".claude.json").write_text(json.dumps(value), encoding="utf-8")


def _log_lines(log_path: pathlib.Path) -> list[str]:
    return [line for line in log_path.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize("kind", _runners())
def test_deploys_rules_and_configures_both_agents(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path)
    legacy_dir = home / ".claude" / "rules" / "agent-basics"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "01-agent.md").write_text("# 旧配布\n", encoding="utf-8")
    rules_dir = home / ".claude" / "rules" / "agent-toolkit"
    rules_dir.mkdir(parents=True)
    (rules_dir / "obsolete.md").write_text("# 旧ファイル\n", encoding="utf-8")

    _run(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    assert (rules_dir / "01-agent.md").read_text(encoding="utf-8") == (RULES_SRC / "01-agent.md").read_text(encoding="utf-8")
    assert not legacy_dir.exists()
    assert not (rules_dir / "obsolete.md").exists()
    joined = "\n".join(_log_lines(stub_log))
    expected = [
        "claude plugin marketplace add ak110/dotfiles --scope=user",
        "claude plugin marketplace update ak110-dotfiles",
        "claude plugin uninstall edit-guardrails@ak110-dotfiles",
        "claude plugin install agent-toolkit@ak110-dotfiles --scope=user",
        "claude plugin update agent-toolkit@ak110-dotfiles --scope=user",
        "codex plugin marketplace add ak110/dotfiles --json",
        "codex plugin marketplace upgrade ak110-dotfiles --json",
        "codex plugin add agent-toolkit@ak110-dotfiles --json",
        "claude mcp add --scope user codex -- codex mcp-server",
    ]
    last_index = -1
    for command in expected:
        index = joined.find(command, last_index + 1)
        assert index > last_index, f"未呼び出しまたは順序違反: {command!r}\nlog={joined}"
        last_index = index
    assert "claude mcp get" not in joined


_REGISTERED_STATES = [
    pytest.param({"mcpServers": {"codex": {}}}, id="user"),
    pytest.param({"mcpServers": {"codex": {}}, "projects": {"/repo": {"mcpServers": {"codex": {}}}}}, id="user-local"),
    pytest.param({"mcpServers": {"codex": {}}, "projectMarker": True}, id="user-project"),
    pytest.param(
        {"mcpServers": {"codex": {}}, "projects": {"/repo": {"mcpServers": {"codex": {}}}}, "projectMarker": True},
        id="user-local-project",
    ),
]


@pytest.mark.parametrize("kind", _runners())
@pytest.mark.parametrize("config", _REGISTERED_STATES)
def test_preserves_existing_user_codex_mcp(
    kind: str,
    config: object,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_claude_config(home, config)
    before = (home / ".claude.json").read_bytes()
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    _run(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    assert (home / ".claude.json").read_bytes() == before
    assert not any("claude mcp add" in line for line in _log_lines(stub_log))


_UNREGISTERED_STATES = [
    pytest.param(None, id="missing-file"),
    pytest.param({}, id="empty-object"),
    pytest.param({"mcpServers": {}}, id="empty-user"),
    pytest.param({"projects": {"/repo": {"mcpServers": {"codex": {}}}}}, id="local-only"),
    pytest.param({"projectMarker": True}, id="project-only"),
]


@pytest.mark.parametrize("kind", _runners())
@pytest.mark.parametrize("config", _UNREGISTERED_STATES)
def test_adds_user_codex_mcp_when_only_non_user_state_exists(
    kind: str,
    config: object | None,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    if config is not None:
        _write_claude_config(home, config)
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    _run(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    assert sum("claude mcp add --scope user codex -- codex mcp-server" in line for line in _log_lines(stub_log)) == 1


_INVALID_STATES = [
    pytest.param("{", id="invalid-json"),
    pytest.param("[]", id="root-array"),
    pytest.param('{"mcpServers": null}', id="mcp-null"),
    pytest.param('{"mcpServers": []}', id="mcp-array"),
]


@pytest.mark.parametrize("kind", _runners())
@pytest.mark.parametrize("content", _INVALID_STATES)
def test_fails_closed_for_invalid_claude_config(
    kind: str,
    content: str,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(content, encoding="utf-8")
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    result = _run(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log, check=False)

    assert result.returncode != 0
    assert not any("claude mcp add" in line for line in _log_lines(stub_log))


@pytest.mark.parametrize("kind", _runners())
def test_fails_closed_when_claude_config_is_unreadable(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    result = _run(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log, check=False)

    assert result.returncode != 0
    assert not any("claude mcp add" in line for line in _log_lines(stub_log))


@pytest.mark.parametrize("kind", _runners())
@pytest.mark.parametrize("missing_command", ["claude", "codex", "uv"])
def test_exits_before_writing_when_required_command_is_missing(
    kind: str,
    missing_command: str,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path, omit=missing_command)

    result = _run(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log, check=False)

    assert result.returncode != 0
    assert not (home / ".claude" / "rules" / "agent-toolkit").exists()


@pytest.mark.parametrize("kind", _runners())
@pytest.mark.parametrize(
    "fail_pattern",
    [
        "claude plugin marketplace update",
        "claude plugin update",
        "codex plugin marketplace upgrade",
        "codex plugin add",
        "claude mcp add",
    ],
)
def test_propagates_required_setup_failures(
    kind: str,
    fail_pattern: str,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    result = _run(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        fail_pattern=fail_pattern,
        check=False,
    )

    assert result.returncode != 0


@pytest.mark.parametrize("kind", _runners())
def test_cleans_stage_directory_on_download_failure(kind: str, tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path)
    rules_dir = home / ".claude" / "rules" / "agent-toolkit"
    rules_dir.mkdir(parents=True)
    sentinel = rules_dir / "01-agent.md"
    sentinel.write_text("# 既存内容\n", encoding="utf-8")

    result = _run(
        kind,
        home,
        "http://127.0.0.1:1/does-not-exist",
        stub_bin=stub_bin,
        stub_log=stub_log,
        check=False,
    )

    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "# 既存内容\n"
    stage_root = home / ".claude" / "rules-stage"
    assert not stage_root.exists() or not list(stage_root.iterdir())


def test_bash_json_check_uses_explicit_python_with_real_uv(tmp_path: pathlib.Path, rules_url: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path, omit="uv")
    uv_executable = os.environ.get("UV") or shutil.which("uv")
    assert uv_executable is not None
    (stub_bin / "uv").symlink_to(uv_executable)
    working_directory = tmp_path / "invalid-project"
    working_directory.mkdir()
    (working_directory / "pyproject.toml").write_text("invalid", encoding="utf-8")
    (working_directory / ".python-version").write_text("3.99", encoding="utf-8")

    _run("sh", home, rules_url, stub_bin=stub_bin, stub_log=stub_log, cwd=working_directory)

    assert any("claude mcp add --scope user codex -- codex mcp-server" in line for line in _log_lines(stub_log))
    assert not (working_directory / ".venv").exists()
    assert not (working_directory / "uv.lock").exists()
