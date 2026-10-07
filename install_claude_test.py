"""agent-toolkit統合インストーラーの公開契約を検証する。"""

import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import typing

import pytest

from _test_helpers import (
    INSTALL_PS1,
    INSTALL_SH,
    REPO_ROOT,
    installer_runners,
    make_command_stubs,
    read_log_lines,
    run_installer,
    serve_rules,
)

LEGACY_CODEX_MCP_CASES = REPO_ROOT / "pytools" / "_internal" / "legacy_codex_mcp_cases.json"


@pytest.fixture(name="rules_url", scope="module")
def rules_url_fixture() -> typing.Iterator[str]:
    """テスト用のルール配布先URLを返す。"""
    yield from serve_rules()


def _write_claude_config(home: pathlib.Path, value: object) -> None:
    (home / ".claude.json").write_text(json.dumps(value), encoding="utf-8")


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


def test_agents_server_warmup_closes_standard_input() -> None:
    """MCPのウォームアップは親端末の標準入力を待たずに終了する。"""
    shell_source = INSTALL_SH.read_text(encoding="utf-8")
    powershell_source = INSTALL_PS1.read_text(encoding="utf-8-sig")

    assert '"$script_path" --check-dependencies </dev/null >/dev/null' in shell_source
    assert (
        "$null | & uv run --project $projectRoot --locked --no-default-groups $scriptPath --check-dependencies *> $null"
        in powershell_source
    )


@pytest.mark.parametrize("kind", installer_runners())
def test_warms_claude_and_codex_plugin_scripts(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """Claude CodeとCodexが実際に参照する両方のスクリプトを事前取得する。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)

    run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    warmups = [line for line in read_log_lines(stub_log) if line.startswith("uv run --project ")]
    assert len(warmups) == 2
    assert any(str(home / "claude-plugin" / "agent_toolkit" / "agents_server_mcp.py") in line for line in warmups)
    assert any(
        str(home / ".codex" / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3/agent_toolkit/agents_server_mcp.py") in line
        for line in warmups
    )


@pytest.mark.parametrize("kind", installer_runners())
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
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=before_state,
        codex_plugin_after=after_state,
        codex_daemon_running=daemon_running,
    )

    lines = read_log_lines(stub_log)
    assert sum("codex plugin list --json" in line for line in lines) == 2
    assert ("codex app-server daemon version" in lines) is (before_state != after_state)
    assert ("codex app-server daemon restart" in result.stderr) is expect_notice


@pytest.mark.parametrize("kind", installer_runners())
def test_plugin_update_preserves_previous_cache_for_first_wrapper_transition(
    kind: str, tmp_path: pathlib.Path, rules_url: str
) -> None:
    """初回のラッパー配置では旧プラグイン実体を保持し、互換リンクは復元しない。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "custom-codex"
    cache_root = codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit"
    old_compat = cache_root / "1.2.1"
    old_compat.parent.mkdir(parents=True)
    old_compat.symlink_to("1.2.2", target_is_directory=True)
    stub_bin, stub_log = make_command_stubs(tmp_path)

    run_installer(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before=("1.2.2", True),
        codex_plugin_after=("1.2.3", True),
        codex_home=codex_home,
    )

    assert not (codex_home / "plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions").exists()
    assert not old_compat.is_symlink()
    assert (cache_root / "1.2.2" / "scripts" / "hook.py").read_text(encoding="utf-8") == "old hook\n"


@pytest.mark.parametrize("kind", installer_runners())
def test_plugin_state_failure_stops_before_plugin_add(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """更新前状態を取得できない場合はpluginを変更しない。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(
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
    assert not any("codex plugin add" in line for line in read_log_lines(stub_log))


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
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(
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
    assert not any("codex plugin add" in line for line in read_log_lines(stub_log))


@pytest.mark.parametrize("kind", installer_runners())
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
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(
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
    assert not any("codex plugin add" in line for line in read_log_lines(stub_log))


@pytest.mark.parametrize("kind", installer_runners())
def test_other_plugin_details_do_not_block_install(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """対象外pluginのversionとenabledは対象pluginの状態判定へ影響させない。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)
    plugin_state = {"installed": [{"pluginId": "other@marketplace", "version": 1, "enabled": "true"}]}

    run_installer(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        codex_plugin_before_json=json.dumps(plugin_state),
    )

    assert any("codex plugin add agent-toolkit@ak110-dotfiles --json" in line for line in read_log_lines(stub_log))


@pytest.mark.parametrize("kind", installer_runners())
def test_same_version_does_not_create_compat_ledger(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """同version再実行でも廃止した互換台帳を新設しない。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "custom-codex"
    stub_bin, stub_log = make_command_stubs(tmp_path)

    run_installer(
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


_REGISTERED_STATES = [
    pytest.param({"mcpServers": {"codex": {}}}, id="user"),
    pytest.param({"mcpServers": {"codex": {}}, "projects": {"/repo": {"mcpServers": {"codex": {}}}}}, id="user-local"),
    pytest.param({"mcpServers": {"codex": {}}, "projectMarker": True}, id="user-project"),
    pytest.param(
        {"mcpServers": {"codex": {}}, "projects": {"/repo": {"mcpServers": {"codex": {}}}}, "projectMarker": True},
        id="user-local-project",
    ),
]


@pytest.mark.parametrize("kind", installer_runners())
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
    stub_bin, stub_log = make_command_stubs(tmp_path)

    run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    assert (home / ".claude.json").read_bytes() == before
    assert not any("claude mcp add" in line or "claude mcp remove" in line for line in read_log_lines(stub_log))


_UNREGISTERED_STATES = [
    pytest.param(None, id="missing-file"),
    pytest.param({}, id="empty-object"),
    pytest.param({"mcpServers": {}}, id="empty-user"),
    pytest.param({"projects": {"/repo": {"mcpServers": {"codex": {}}}}}, id="local-only"),
    pytest.param({"projectMarker": True}, id="project-only"),
]


@pytest.mark.parametrize("kind", installer_runners())
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
    stub_bin, stub_log = make_command_stubs(tmp_path)

    run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    assert not any("claude mcp add" in line for line in read_log_lines(stub_log))
    assert not any("claude mcp remove" in line for line in read_log_lines(stub_log))


def _legacy_codex_mcp_cases() -> list[object]:
    """3実装が共有する旧Codex MCP定義の判定ケースを、定義を持つ`~/.claude.json`の内容として返す。"""
    data = json.loads(LEGACY_CODEX_MCP_CASES.read_text(encoding="utf-8"))
    return [
        pytest.param({"mcpServers": {"codex": case["definition"]}}, case["legacy"], id=case["name"]) for case in data["cases"]
    ]


@pytest.mark.parametrize("kind", installer_runners())
@pytest.mark.parametrize(("config", "legacy"), _legacy_codex_mcp_cases())
def test_legacy_codex_mcp_cases_match_installers(
    kind: str,
    config: object,
    legacy: bool,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    """両インストーラーは共有ケース表の旧定義だけをUser scope限定で削除し、それ以外の定義を保持する。"""
    home = tmp_path / "home"
    home.mkdir()
    _write_claude_config(home, config)
    before = (home / ".claude.json").read_bytes()
    stub_bin, stub_log = make_command_stubs(tmp_path)

    run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    removals = sum("claude mcp remove --scope user codex" in line for line in read_log_lines(stub_log))
    assert removals == (1 if legacy else 0)
    assert not any("claude mcp add" in line for line in read_log_lines(stub_log))
    # 削除は`claude mcp remove`へ委ね、インストーラー自身は設定ファイルを書き換えない。
    assert (home / ".claude.json").read_bytes() == before


_INVALID_STATES = [
    pytest.param("{", id="invalid-json"),
    pytest.param("[]", id="root-array"),
    pytest.param('{"mcpServers": null}', id="mcp-null"),
    pytest.param('{"mcpServers": []}', id="mcp-array"),
]


@pytest.mark.parametrize("kind", installer_runners())
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
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log, check=False)

    assert result.returncode != 0
    assert not any("claude mcp add" in line for line in read_log_lines(stub_log))


@pytest.mark.parametrize("kind", installer_runners())
def test_fails_closed_when_claude_config_is_unreadable(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log, check=False)

    assert result.returncode != 0
    assert not any("claude mcp add" in line for line in read_log_lines(stub_log))


@pytest.mark.parametrize("kind", installer_runners())
@pytest.mark.parametrize("missing_command", ["claude", "codex", "uv"])
def test_exits_before_writing_when_required_command_is_missing(
    kind: str,
    missing_command: str,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path, omit=missing_command)

    result = run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log, check=False)

    assert result.returncode != 0
    assert not (home / ".claude" / "rules" / "agent-toolkit").exists()


@pytest.mark.parametrize("kind", installer_runners())
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
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        fail_pattern=fail_pattern,
        check=False,
    )

    assert result.returncode != 0


@pytest.mark.parametrize("kind", installer_runners())
@pytest.mark.parametrize(
    ("fail_pattern", "block_atk_wrapper", "expect_notice"),
    [
        pytest.param("claude plugin marketplace update", False, False, id="before-codex-plugin"),
        pytest.param("codex plugin marketplace upgrade", False, False, id="marketplace-upgrade"),
        pytest.param("codex plugin add", False, False, id="plugin-add"),
        pytest.param("__never_match__", True, False, id="before-plugin-atk-wrapper"),
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
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(
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


@pytest.mark.parametrize("kind", installer_runners())
def test_cleans_stage_directory_on_download_failure(kind: str, tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)
    rules_dir = home / ".claude" / "rules" / "agent-toolkit"
    rules_dir.mkdir(parents=True)
    sentinel = rules_dir / "01-agent.md"
    sentinel.write_text("# 既存内容\n", encoding="utf-8")

    result = run_installer(
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
    stub_bin, stub_log = make_command_stubs(tmp_path, omit="uv")
    uv_executable = os.environ.get("UV") or shutil.which("uv")
    assert uv_executable is not None
    (stub_bin / "uv").symlink_to(uv_executable)
    working_directory = tmp_path / "invalid-project"
    working_directory.mkdir()
    (working_directory / "pyproject.toml").write_text("invalid", encoding="utf-8")
    (working_directory / ".python-version").write_text("3.99", encoding="utf-8")

    run_installer("sh", home, rules_url, stub_bin=stub_bin, stub_log=stub_log, cwd=working_directory)

    assert not any("claude mcp add" in line or "claude mcp remove" in line for line in read_log_lines(stub_log))
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
