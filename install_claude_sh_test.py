"""agent-toolkit統合インストーラーの公開契約を検証する。"""

import functools
import http.server
import json
import os
import pathlib
import re
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
        mkdir -p "$CODEX_PLUGIN_CACHE_ROOT/$CODEX_PLUGIN_AFTER_VERSION/scripts"
        printf 'current hook\n' > "$CODEX_PLUGIN_CACHE_ROOT/$CODEX_PLUGIN_AFTER_VERSION/scripts/claude_hook.py"
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


def _run(
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
    manifest = marketplace_root / "agent-toolkit" / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"version": after_version}), encoding="utf-8")
    if before_version != "__missing__":
        old_cache = effective_codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit" / before_version / "scripts"
        old_cache.mkdir(parents=True, exist_ok=True)
        (old_cache / "claude_hook.py").write_text("old hook\n", encoding="utf-8")
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


def _write_claude_config(home: pathlib.Path, value: object) -> None:
    (home / ".claude.json").write_text(json.dumps(value), encoding="utf-8")


def _log_lines(log_path: pathlib.Path) -> list[str]:
    return [line for line in log_path.read_text(encoding="utf-8").splitlines() if line]


def _run_powershell_function_probe(
    tmp_path: pathlib.Path,
    function_names: list[str],
    body: str,
) -> subprocess.CompletedProcess[str]:
    """インストーラーから指定関数だけを読み込み、分離したprobeで実行する。"""
    source_path = str(INSTALL_PS1).replace("'", "''")
    names = json.dumps(function_names).replace("'", "''")
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{source_path}', [ref]$tokens, [ref]$errors)
$functionNames = ConvertFrom-Json -InputObject '{names}'
foreach ($functionName in $functionNames) {{
    $definition = $ast.Find({{
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $functionName
    }}, $true)
    if ($null -eq $definition) {{ throw "関数が見つかりません: $functionName" }}
    Invoke-Expression $definition.Extent.Text
}}
{body}
""".lstrip(),
        encoding="utf-8",
    )
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(probe)],
        check=True,
        capture_output=True,
        text=True,
    )


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

    result = _run(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

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
    ]
    last_index = -1
    for command in expected:
        index = joined.find(command, last_index + 1)
        assert index > last_index, f"未呼び出しまたは順序違反: {command!r}\nlog={joined}"
        last_index = index
    assert not any("claude mcp add" in line or "claude mcp remove" in line for line in _log_lines(stub_log))
    assert not any("claude mcp remove" in line for line in _log_lines(stub_log))
    assert result.stderr.splitlines()[-1] == "codex app-server daemon restart"
    assert result.stderr.count("Codex pluginを更新しました。") == 1


@pytest.mark.parametrize("kind", _runners())
@pytest.mark.parametrize(
    ("before_state", "after_state", "daemon_running", "expect_notice"),
    [
        pytest.param(("1.2.3", True), ("1.2.3", True), True, False, id="unchanged-running"),
        pytest.param(("1.2.3", True), ("1.2.3", True), False, False, id="unchanged-stopped"),
        pytest.param(("1.2.2", True), ("1.2.3", True), True, True, id="version-updated-running"),
        pytest.param(("1.2.2", True), ("1.2.3", True), False, False, id="version-updated-stopped"),
        pytest.param(("1.2.3", False), ("1.2.3", True), True, True, id="enabled-running"),
        pytest.param(("1.2.3", False), ("1.2.3", True), False, False, id="enabled-stopped"),
    ],
)
def test_restart_notice_requires_codex_plugin_state_change(
    kind: str,
    before_state: tuple[str, bool],
    after_state: tuple[str, bool],
    daemon_running: bool,
    expect_notice: bool,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    """導入前後のversionまたはenabledが変化した場合だけ再起動を案内する。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    result = _run(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=before_state,
        codex_plugin_after=after_state,
        codex_daemon_running=daemon_running,
    )

    lines = _log_lines(stub_log)
    assert sum("codex plugin list --json" in line for line in lines) == 2
    assert ("codex app-server daemon version" in lines) is (before_state != after_state)
    assert ("codex app-server daemon restart" in result.stderr) is expect_notice


@pytest.mark.parametrize("kind", _runners())
def test_plugin_update_restores_old_cache_path(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """更新で削除された旧version名を現行cache実体へのリンクとして復元する。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "custom-codex"
    cache_root = codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit"
    old_compat = cache_root / "1.2.1"
    old_compat.parent.mkdir(parents=True)
    old_compat.symlink_to("1.2.2", target_is_directory=True)
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    _run(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=("1.2.2", True),
        codex_plugin_after=("1.2.3", True),
        codex_home=codex_home,
    )

    versions = codex_home / "plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions"
    assert versions.read_text(encoding="utf-8") == "1.2.1\n1.2.2\n"
    for version in ("1.2.1", "1.2.2"):
        old_path = cache_root / version
        assert old_path.is_symlink()
        assert old_path.resolve() == (cache_root / "1.2.3").resolve()
        assert (old_path / "scripts/claude_hook.py").read_text(encoding="utf-8") == "current hook\n"


def test_shell_restores_dot_version_to_hyphen_version(tmp_path: pathlib.Path, rules_url: str) -> None:
    """先頭dotの旧versionを収集し、先頭hyphenの現行versionへ復元する。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "custom-codex"
    cache_root = codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit"
    dot_version = cache_root / ".1.2/scripts"
    dot_version.mkdir(parents=True)
    (dot_version / "claude_hook.py").write_text("dot hook\n", encoding="utf-8")
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    _run(
        "sh",
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=("1.2.2", True),
        codex_plugin_after=("-1.2", True),
        codex_home=codex_home,
    )

    restored = cache_root / ".1.2"
    assert restored.is_symlink()
    assert restored.readlink() == pathlib.Path("-1.2")
    assert (restored / "scripts/claude_hook.py").read_text(encoding="utf-8") == "current hook\n"


@pytest.mark.parametrize("kind", _runners())
def test_plugin_state_failure_stops_before_plugin_add(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """更新前状態を取得できない場合はpluginを変更しない。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    result = _run(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        fail_pattern="codex plugin list --json",
        codex_plugin_before=("1.2.2", True),
        check=False,
    )

    assert result.returncode != 0
    assert not any("codex plugin add" in line for line in _log_lines(stub_log))


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh未インストール")
@pytest.mark.parametrize("plugin_state", [{}, {"installed": None}, {"installed": "x"}])
def test_powershell_invalid_plugin_state_stops_before_plugin_add(
    plugin_state: object,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    """PowerShellではinstalledが配列でない応答を取得失敗として扱う。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    result = _run(
        "ps1",
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=("1.2.2", True),
        codex_plugin_before_json=json.dumps(plugin_state),
        check=False,
    )

    assert result.returncode != 0
    assert not any("codex plugin add" in line for line in _log_lines(stub_log))


@pytest.mark.parametrize("kind", _runners())
@pytest.mark.parametrize(
    "plugin_state",
    [
        {"installed": [{"version": "1.2.2", "enabled": True}]},
        {"installed": [{"pluginId": "agent-toolkit@ak110-dotfiles", "version": 122, "enabled": True}]},
        {"installed": [{"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.2", "enabled": "true"}]},
    ],
)
def test_invalid_target_plugin_entry_stops_before_plugin_add(
    kind: str,
    plugin_state: object,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    """対象plugin要素の必須項目が不正なら更新を中止する。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    result = _run(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=("1.2.2", True),
        codex_plugin_before_json=json.dumps(plugin_state),
        check=False,
    )

    assert result.returncode != 0
    assert not any("codex plugin add" in line for line in _log_lines(stub_log))


@pytest.mark.parametrize("kind", _runners())
def test_other_plugin_details_do_not_block_install(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """対象外pluginのversionとenabledは対象pluginの状態判定へ影響させない。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path)
    plugin_state = {"installed": [{"pluginId": "other@marketplace", "version": 1, "enabled": "true"}]}

    _run(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before_json=json.dumps(plugin_state),
    )

    assert any("codex plugin add agent-toolkit@ak110-dotfiles --json" in line for line in _log_lines(stub_log))


def test_shell_ledger_replace_failure_keeps_existing_ledger(tmp_path: pathlib.Path, rules_url: str) -> None:
    """shellの台帳置換失敗時は既存内容を保持して更新を中止する。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "custom-codex"
    versions = codex_home / "plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions"
    versions.parent.mkdir(parents=True)
    versions.write_text("1.2.1\n", encoding="utf-8")
    stub_bin, stub_log = _make_command_stubs(tmp_path)
    mv_stub = stub_bin / "mv"
    mv_stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '    *"/plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions") exit 9 ;;\n'
        "esac\n"
        'exec /usr/bin/mv "$@"\n',
        encoding="utf-8",
    )
    mv_stub.chmod(mv_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    result = _run(
        "sh",
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=("1.2.2", True),
        codex_plugin_after=("1.2.3", True),
        codex_home=codex_home,
        check=False,
    )

    assert result.returncode != 0
    assert versions.read_text(encoding="utf-8") == "1.2.1\n"
    assert not any("codex plugin add" in line for line in _log_lines(stub_log))


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh未インストール")
def test_powershell_ledger_replace_failure_keeps_existing_ledger(tmp_path: pathlib.Path) -> None:
    """PowerShellの台帳置換失敗時は既存内容を保持して更新を中止する。"""
    versions = tmp_path / "cache-compat/versions"
    versions.parent.mkdir(parents=True)
    versions.write_text("1.2.1\n", encoding="utf-8")
    versions_path = str(versions).replace("'", "''")
    result = _run_powershell_function_probe(
        tmp_path,
        ["Save-CodexCacheVersionLedger", "Install-CodexPlugin"],
        f"""
$codexCacheCompatVersions = '{versions_path}'
$codexPluginId = 'agent-toolkit@ak110-dotfiles'
$script:utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$script:pluginAddCalled = $false
function codex {{}}
function Get-CodexCacheVersionSet {{ @('1.2.1', '1.2.2') }}
function Get-CodexExpectedPluginVersion {{ '1.2.3' }}
function Get-CodexPluginState {{ [PSCustomObject]@{{ Present = $true; Version = '1.2.2'; Enabled = $true }} }}
function Invoke-RequiredNativeCommand {{
    param([string]$command, [string[]]$arguments)
    if ($arguments[0] -eq 'plugin' -and $arguments[1] -eq 'add') {{ $script:pluginAddCalled = $true }}
}}
$saveDefinition = $ast.Find({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Save-CodexCacheVersionLedger'
}}, $true)
$replaceCall = '[System.IO.File]::Replace($temporary, $codexCacheCompatVersions, $backup)'
$saveText = $saveDefinition.Extent.Text.Replace(
    $replaceCall,
    "throw 'injected ledger replace failure'"
)
if ($saveText -eq $saveDefinition.Extent.Text) {{ throw '置換失敗箇所を注入できません。' }}
Invoke-Expression $saveText
try {{ $null = Install-CodexPlugin }} catch {{ $failureMessage = $_.Exception.Message }}
[PSCustomObject]@{{
    Content = [System.IO.File]::ReadAllText($codexCacheCompatVersions)
    PluginAddCalled = $script:pluginAddCalled
    FailureMessage = $failureMessage
}} | ConvertTo-Json -Compress
""".strip(),
    )

    assert json.loads(result.stdout) == {
        "Content": "1.2.1\n",
        "PluginAddCalled": False,
        "FailureMessage": "injected ledger replace failure",
    }


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh未インストール")
def test_powershell_existing_ledger_is_replaced_atomically(tmp_path: pathlib.Path) -> None:
    """PowerShellでは既存台帳をbackup付きのatomic置換で更新する。"""
    versions = tmp_path / "cache-compat/versions"
    versions.parent.mkdir(parents=True)
    versions.write_text("1.2.1\n", encoding="utf-8")
    versions_path = str(versions).replace("'", "''")
    result = _run_powershell_function_probe(
        tmp_path,
        ["Save-CodexCacheVersionLedger"],
        f"""
$codexCacheCompatVersions = '{versions_path}'
$script:utf8NoBom = [System.Text.UTF8Encoding]::new($false)
function Get-CodexCacheVersionSet {{ @('1.2.1', '1.2.2') }}
Save-CodexCacheVersionLedger
[PSCustomObject]@{{
    Content = [System.IO.File]::ReadAllText($codexCacheCompatVersions)
    Files = @((Get-ChildItem -LiteralPath (Split-Path $codexCacheCompatVersions -Parent)).Name)
}} | ConvertTo-Json -Compress
""".strip(),
    )

    assert json.loads(result.stdout) == {"Content": "1.2.1\n1.2.2\n", "Files": ["versions"]}


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh未インストール")
def test_powershell_cache_link_platform_branches(tmp_path: pathlib.Path) -> None:
    """PowerShellのjunctionとsymbolic link分岐へ決定論的に到達する。"""
    result = _run_powershell_function_probe(
        tmp_path,
        ["Invoke-CodexCacheLinkCreation"],
        """
$script:calls = @()
function New-Item {
    param([string]$ItemType, [string]$Path, [string]$Target)
    $script:calls += [PSCustomObject]@{ ItemType = $ItemType; Path = $Path; Target = $Target }
}
Invoke-CodexCacheLinkCreation 'old-win' 'C:\\cache\\current' '2.0.0' $true
Invoke-CodexCacheLinkCreation 'old-posix' '/cache/current' '2.0.0' $false
$script:calls | ConvertTo-Json -Compress
""".strip(),
    )

    assert json.loads(result.stdout) == [
        {"ItemType": "Junction", "Path": "old-win", "Target": "C:\\cache\\current"},
        {"ItemType": "SymbolicLink", "Path": "old-posix", "Target": "2.0.0"},
    ]


@pytest.mark.parametrize("kind", _runners())
def test_same_version_recovers_from_compat_ledger(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """復元途中の中断後は同version再実行で台帳から旧名を回復する。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "custom-codex"
    versions = codex_home / "plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions"
    versions.parent.mkdir(parents=True)
    versions.write_text("1.2.1\n", encoding="utf-8")
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    _run(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=("1.2.3", True),
        codex_plugin_after=("1.2.3", True),
        codex_home=codex_home,
    )

    old_path = codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.1"
    assert old_path.resolve() == (codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3").resolve()


@pytest.mark.parametrize("kind", _runners())
def test_same_version_without_ledger_does_not_create_one(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """互換対象がない同version再実行では台帳を新設しない。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "custom-codex"
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    _run(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=("1.2.3", True),
        codex_plugin_after=("1.2.3", True),
        codex_home=codex_home,
    )

    versions = codex_home / "plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions"
    assert not versions.exists()


@pytest.mark.parametrize("kind", _runners())
def test_update_fails_when_current_cache_is_missing(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """更新後の現行version実体が無ければ互換リンクを作成せず失敗する。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "custom-codex"
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    result = _run(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=("1.2.2", True),
        codex_plugin_after=("1.2.3", True),
        codex_home=codex_home,
        create_cache=False,
        check=False,
    )

    assert result.returncode != 0
    assert not (codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2").exists()


@pytest.mark.parametrize("kind", _runners())
def test_cache_compat_conflict_fails_without_replacing_entry(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """旧名に通常エントリがある場合は上書きせず非0で終了する。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "custom-codex"
    versions = codex_home / "plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions"
    versions.parent.mkdir(parents=True)
    versions.write_text("1.2.1\n", encoding="utf-8")
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    result = _run(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=("1.2.3", True),
        codex_plugin_after=("1.2.3", True),
        codex_home=codex_home,
        conflict_version="1.2.1",
        check=False,
    )

    assert result.returncode != 0
    sentinel = codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.1/keep"
    assert sentinel.read_text(encoding="utf-8") == "keep\n"


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
    assert not any("claude mcp add" in line or "claude mcp remove" in line for line in _log_lines(stub_log))


_UNREGISTERED_STATES = [
    pytest.param(None, id="missing-file"),
    pytest.param({}, id="empty-object"),
    pytest.param({"mcpServers": {}}, id="empty-user"),
    pytest.param({"projects": {"/repo": {"mcpServers": {"codex": {}}}}}, id="local-only"),
    pytest.param({"projectMarker": True}, id="project-only"),
]


@pytest.mark.parametrize("kind", _runners())
@pytest.mark.parametrize("config", _UNREGISTERED_STATES)
def test_does_not_register_user_codex_mcp_when_only_non_user_state_exists(
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

    assert not any("claude mcp add" in line for line in _log_lines(stub_log))
    assert not any("claude mcp remove" in line for line in _log_lines(stub_log))


_LEGACY_USER_CODEX_STATES = [
    pytest.param({"mcpServers": {"codex": {"command": "codex", "args": ["mcp-server"]}}}, id="without-type-timeout"),
    pytest.param(
        {"mcpServers": {"codex": {"type": "stdio", "command": "codex", "args": ["mcp-server"], "timeout": 7200000}}},
        id="with-managed-timeout",
    ),
]


_POWERSHELL_CUSTOM_USER_CODEX_STATES = [
    pytest.param(
        {"mcpServers": {"codex": {"command": "codex", "args": "mcp-server"}}},
        id="scalar-args",
    ),
    pytest.param(
        {"mcpServers": {"codex": {"command": "codex", "args": ["mcp-server"], "timeout": "7200000"}}},
        id="string-timeout",
    ),
]


@pytest.mark.parametrize("kind", _runners())
@pytest.mark.parametrize("config", _LEGACY_USER_CODEX_STATES)
def test_removes_exact_legacy_user_codex_mcp(
    kind: str,
    config: object,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    """完全一致する旧User scope定義だけをUser scope限定で削除する。"""
    home = tmp_path / "home"
    home.mkdir()
    _write_claude_config(home, config)
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    _run(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    assert sum("claude mcp remove --scope user codex" in line for line in _log_lines(stub_log)) == 1
    assert not any("claude mcp add" in line for line in _log_lines(stub_log))


@pytest.mark.parametrize("kind", _runners())
@pytest.mark.parametrize("config", _POWERSHELL_CUSTOM_USER_CODEX_STATES)
def test_platforms_preserve_scalar_args_and_string_timeout(
    kind: str,
    config: object,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    """両プラットフォーム版は型に一致しない利用者定義を旧定義として削除しない。"""
    home = tmp_path / "home"
    home.mkdir()
    _write_claude_config(home, config)
    before = (home / ".claude.json").read_bytes()
    stub_bin, stub_log = _make_command_stubs(tmp_path)

    _run(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    assert (home / ".claude.json").read_bytes() == before
    assert not any("claude mcp add" in line or "claude mcp remove" in line for line in _log_lines(stub_log))


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
@pytest.mark.parametrize(
    ("fail_pattern", "block_atk_wrapper", "expect_notice"),
    [
        pytest.param("claude plugin marketplace update", False, False, id="before-codex-plugin"),
        pytest.param("codex plugin marketplace upgrade", False, False, id="marketplace-upgrade"),
        pytest.param("codex plugin add", False, False, id="plugin-add"),
        pytest.param("__never_match__", True, True, id="after-plugin-atk-wrapper"),
    ],
)
def test_notice_contract_by_failure_stage(
    kind: str,
    fail_pattern: str,
    block_atk_wrapper: bool,
    expect_notice: bool,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    """Codex plugin更新後だけ、後続失敗時も最終案内を保持する。"""
    home = tmp_path / "home"
    home.mkdir()
    if block_atk_wrapper:
        local_dir = home / ".local"
        local_dir.mkdir()
        (local_dir / "bin").write_text("ディレクトリ作成を阻害する", encoding="utf-8")
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
    stderr_lines = result.stderr.splitlines()
    assert (bool(stderr_lines) and stderr_lines[-1] == "codex app-server daemon restart") is expect_notice
    assert ("Codex pluginを更新しました。" in result.stderr) is expect_notice
    if expect_notice and not block_atk_wrapper:
        assert result.stderr.index("stub failure:") < result.stderr.index("Codex pluginを更新しました。")


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

    assert not any("claude mcp add" in line or "claude mcp remove" in line for line in _log_lines(stub_log))
    assert not (working_directory / ".venv").exists()
    assert not (working_directory / "uv.lock").exists()


@pytest.mark.parametrize(
    ("cached_versions", "expected_version"),
    [
        pytest.param(
            (("market", "2.0.0"),),
            "2.0.0",
            id="single-version",
        ),
        pytest.param(
            (("market", "1.2.0"), ("market", "1.9.0"), ("market", "1.10.0")),
            "1.10.0",
            id="natural-version-order",
        ),
        pytest.param(
            (("a-market", "9.0.0"), ("z-market", "1.0.0")),
            "9.0.0",
            id="marketplace-order-conflicts-with-version-order",
        ),
    ],
)
def test_bash_atk_wrapper_selects_latest_natural_version(
    tmp_path: pathlib.Path,
    cached_versions: tuple[tuple[str, str], ...],
    expected_version: str,
) -> None:
    """生成するBashラッパーは複数版から自然順で最新実体を選択する。"""
    source = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(r"cat >\"\$wrapper\" <<'EOF'\n(?P<body>.*?)\nEOF", source, re.DOTALL)
    assert match is not None
    home = tmp_path / "home"
    wrapper = tmp_path / "atk"
    wrapper.write_text(match.group("body") + "\n", encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    for marketplace, version in cached_versions:
        executable = home / ".claude/plugins/cache" / marketplace / "agent-toolkit" / version / "bin/atk"
        executable.parent.mkdir(parents=True)
        executable.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n", encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        [str(wrapper)],
        env={**os.environ, "HOME": str(home)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == f"{expected_version}\n"


def test_bash_atk_wrapper_reports_missing_plugin(tmp_path: pathlib.Path) -> None:
    """生成するBashラッパーは実体が無い場合に案内を返して失敗する。"""
    source = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(r"cat >\"\$wrapper\" <<'EOF'\n(?P<body>.*?)\nEOF", source, re.DOTALL)
    assert match is not None
    home = tmp_path / "home"
    home.mkdir()
    wrapper = tmp_path / "atk"
    wrapper.write_text(match.group("body") + "\n", encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        [str(wrapper)],
        env={**os.environ, "HOME": str(home)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "agent-toolkit プラグインが見つかりません" in result.stderr


def test_powershell_atk_wrapper_uses_version_objects_and_existing_paths() -> None:
    """生成するcmdラッパーは版数型による自然順と実体存在を検査する。"""
    source = INSTALL_PS1.read_text(encoding="utf-8-sig")
    match = re.search(r"\$body = @'\n(?P<body>.*?)\n'@", source.replace("\r\n", "\n"), re.DOTALL)
    assert match is not None
    body = match.group("body")
    assert "[version]$_.Name" in body
    assert "Sort-Object Version -Descending" in body
    assert "Select-Object -First 1 -ExpandProperty Path" in body
    assert "Get-ChildItem -LiteralPath $root -Directory" in body
    assert "Test-Path -LiteralPath $candidate -PathType Leaf" in body
    assert "\\*\\agent-toolkit" not in body
    assert 'if not exist "%LATEST%" goto :not_found' in body
    versions = ["1.2.0", "1.9.0", "1.10.0"]
    assert max(versions) == "1.9.0"
