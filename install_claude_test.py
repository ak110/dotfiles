"""agent-toolkit統合インストーラーの公開契約を検証する。"""

import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
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
from pytools._internal import common

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
        "$null | & uv run --no-config --no-project --python 3.15 python (Join-Path $projectRoot 'scripts/launch.py') "
        "$scriptPath --check-dependencies *> $null" in powershell_source
    )


@pytest.mark.parametrize("kind", installer_runners())
def test_warms_claude_and_codex_plugin_scripts(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """Claude CodeとCodexが実際に参照する両方のスクリプトを事前取得する。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)

    run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    warmups = [
        line
        for line in read_log_lines(stub_log)
        if line.startswith("uv run --no-config --no-project --python 3.15 python ") and "scripts/launch.py" in line
    ]
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
def test_plugin_update_preserves_all_previous_cache_versions(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """更新時は全ての通常versionディレクトリを保持し、互換リンクは復元しない。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "custom-codex"
    cache_root = codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit"
    old_compat = cache_root / "1.2.1"
    old_compat.parent.mkdir(parents=True)
    old_compat.symlink_to("1.2.2", target_is_directory=True)
    older = cache_root / "1.2.0" / "scripts"
    older.mkdir(parents=True)
    (older / "hook.py").write_text("older hook\n", encoding="utf-8")
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
    assert (cache_root / "1.2.0" / "scripts" / "hook.py").read_text(encoding="utf-8") == "older hook\n"
    assert (cache_root / "1.2.2" / "scripts" / "hook.py").read_text(encoding="utf-8") == "old hook\n"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="実行名をsymlinkで与える常駐プロセスはLinuxで起動する")
@pytest.mark.parametrize("kind", installer_runners())
def test_running_codex_updates_and_preserves_previous_cache(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """Codexセッションの稼働中も更新し、CLIが削除した旧cacheを同じ絶対パスへ戻す。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = home / ".codex"
    old_hook = codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2/scripts/hook.py"
    stub_bin, stub_log = make_command_stubs(tmp_path)
    sleep = shutil.which("sleep")
    assert sleep is not None
    session_executable = tmp_path / "resident-bin" / "codex"
    session_executable.parent.mkdir()
    session_executable.symlink_to(sleep)
    with common.process_scope([str(session_executable), "600"]):
        result = run_installer(
            kind,
            home,
            rules_url,
            stub_bin=stub_bin,
            stub_log=stub_log,
            codex_plugin_before=("1.2.2", True),
            codex_plugin_after=("1.2.3", True),
            codex_home=codex_home,
        )
        assert any("codex plugin add agent-toolkit@ak110-dotfiles --json" in line for line in read_log_lines(stub_log))
        assert old_hook.read_text(encoding="utf-8") == "old hook\n"
        assert (codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3/agent_toolkit/hook.py").is_file()
        assert "更新を延期" not in result.stderr


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
def test_plugin_add_failure_restores_previous_cache(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """plugin addが旧cacheを削除して失敗した場合は、更新前の全versionを復旧する。"""
    home = tmp_path / "home"
    home.mkdir()
    codex_home = home / ".codex"
    cache_root = codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit"
    older = cache_root / "1.2.1" / "scripts"
    older.mkdir(parents=True)
    (older / "hook.py").write_text("older hook\n", encoding="utf-8")
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        fail_pattern="codex plugin add",
        codex_plugin_before=("1.2.2", True),
        codex_home=codex_home,
        delete_cache_before_failure=True,
        check=False,
    )

    assert result.returncode != 0
    assert (older / "hook.py").read_text(encoding="utf-8") == "older hook\n"
    assert (cache_root / "1.2.2/scripts/hook.py").read_text(encoding="utf-8") == "old hook\n"


def test_cache_restore_failure_reports_retained_backup() -> None:
    """両OSのインストーラーは復旧失敗時に退避先の扱いと報告先を案内する。"""
    instruction = "退避先を変更せず、表示された内容とパスをdotfilesの作者へ報告してください。"

    assert instruction in INSTALL_SH.read_text(encoding="utf-8")
    assert instruction in INSTALL_PS1.read_text(encoding="utf-8-sig")


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
    kept_file = rules_dir / "01-agent.md"
    kept_file.write_text("# 既存内容\n", encoding="utf-8")

    result = run_installer(
        kind,
        home,
        "http://127.0.0.1:1/does-not-exist",
        stub_bin=stub_bin,
        stub_log=stub_log,
        check=False,
    )

    assert result.returncode != 0
    assert kept_file.read_text(encoding="utf-8") == "# 既存内容\n"
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


_MOVE_FAILURE_SH = """#!/bin/bash
# 配置先への移動だけを失敗させる。MOVE_LEAVE_TARGET=1では配置先を残してから失敗する。
# MOVE_FAIL_ONCEに記録ファイルを指定した場合は、最初の1回（新配置）だけを失敗させる。
for last; do :; done
if [ "$last" = "$MOVE_FAIL_TARGET" ] && { [ -z "${MOVE_FAIL_ONCE:-}" ] || [ ! -e "$MOVE_FAIL_ONCE" ]; }; then
    if [ -n "${MOVE_FAIL_ONCE:-}" ]; then
        : > "$MOVE_FAIL_ONCE"
    fi
    if [ "${MOVE_LEAVE_TARGET:-0}" = 1 ]; then
        /bin/mkdir -p "$last"
    fi
    echo "mv: cannot move to '$last': Permission denied" >&2
    exit 1
fi
exec /bin/mv "$@"
"""

_MOVE_FAILURE_PS1 = """
function Move-Item {
    [CmdletBinding()]
    param([string]$LiteralPath, [string]$Destination)
    $once = $env:MOVE_FAIL_ONCE
    if ($Destination -eq $env:MOVE_FAIL_TARGET -and (-not $once -or -not (Test-Path -LiteralPath $once))) {
        if ($once) { New-Item -ItemType File -Path $once -Force | Out-Null }
        if ($env:MOVE_LEAVE_TARGET -eq '1') { New-Item -ItemType Directory -Path $Destination -Force | Out-Null }
        throw "Move-Item: access denied: $Destination"
    }
    Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath -Destination $Destination
}
& '{installer}'
exit $LASTEXITCODE
"""


def _run_with_move_failure(
    kind: str, tmp_path: pathlib.Path, rules_url: str, *, leave_target: bool, fail_once: bool = False
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """既存規範を置いた隔離HOMEで、配置先への移動（新配置と復元）を失敗させてインストーラーを実行する。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)
    rules_dir = home / ".claude" / "rules" / "agent-toolkit"
    rules_dir.mkdir(parents=True)
    kept_file = rules_dir / "01-agent.md"
    kept_file.write_text("# 既存内容\n", encoding="utf-8")
    ps1_script = None
    if kind == "sh":
        mover = stub_bin / "mv"
        mover.write_text(_MOVE_FAILURE_SH, encoding="utf-8")
        mover.chmod(mover.stat().st_mode | stat.S_IXUSR)
    else:
        ps1_script = tmp_path / "move-failure.ps1"
        ps1_script.write_text(_MOVE_FAILURE_PS1.replace("{installer}", str(INSTALL_PS1).replace("'", "''")), encoding="utf-8")
    result = run_installer(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        check=False,
        extra_env={
            "MOVE_FAIL_TARGET": str(rules_dir),
            "MOVE_LEAVE_TARGET": "1" if leave_target else "0",
            "MOVE_FAIL_ONCE": str(tmp_path / "move-failed-once") if fail_once else "",
        },
        ps1_script=ps1_script,
    )
    return result, home, rules_dir


@pytest.mark.parametrize("leave_target", [False, True], ids=["restore-fails", "target-remains"])
@pytest.mark.parametrize("kind", installer_runners())
def test_keeps_old_rules_when_replacement_fails_before_commit(
    kind: str, tmp_path: pathlib.Path, rules_url: str, leave_target: bool
) -> None:
    """新配置の確定前に配置へ失敗し旧規範を戻せない場合も、旧規範の実体を退避先に残して復旧操作を示す。

    配置先が残って復元を試みられない場合（target-remains）も同じく保持する。
    回収の判断が新配置の確定状態へ接続されていないと、配置先と退避先の両方から旧規範が消える。
    """
    result, home, rules_dir = _run_with_move_failure(kind, tmp_path, rules_url, leave_target=leave_target)

    assert result.returncode != 0
    kept = [path for path in (home / ".claude" / "rules-stage").glob("agent-toolkit.old.*") if path.is_dir()]
    assert len(kept) == 1, result.stderr
    assert (kept[0] / "01-agent.md").read_text(encoding="utf-8") == "# 既存内容\n"
    assert f"退避先に保持しました: {kept[0]}" in result.stderr
    # 最後の診断行は、原因を解消した後に旧規範を配置先へ戻すコマンドである。
    assert any(str(kept[0]) in line and str(rules_dir) in line for line in result.stderr.splitlines())
    assert not list((home / ".claude" / "rules-stage").glob("agent-toolkit.stage.*"))


@pytest.mark.parametrize("kind", installer_runners())
def test_restores_old_rules_when_only_replacement_fails(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """新配置だけが失敗し復元が成功した場合は、旧規範を配置先へ戻し退避先とstageを残さない。"""
    result, home, rules_dir = _run_with_move_failure(kind, tmp_path, rules_url, leave_target=False, fail_once=True)

    assert result.returncode != 0
    assert (rules_dir / "01-agent.md").read_text(encoding="utf-8") == "# 既存内容\n"
    stage_root = home / ".claude" / "rules-stage"
    assert not list(stage_root.glob("agent-toolkit.old.*"))
    assert not list(stage_root.glob("agent-toolkit.stage.*"))
    assert "退避先に保持しました" not in result.stderr


@pytest.mark.parametrize("kind", installer_runners())
def test_successful_install_replaces_rules_and_removes_old_and_stage(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """通常の成功では新規範を配置し、退避した旧規範とstageを回収する。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)
    rules_dir = home / ".claude" / "rules" / "agent-toolkit"
    rules_dir.mkdir(parents=True)
    (rules_dir / "01-agent.md").write_text("# 既存内容\n", encoding="utf-8")

    run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    assert (rules_dir / "01-agent.md").read_text(encoding="utf-8") != "# 既存内容\n"
    stage_root = home / ".claude" / "rules-stage"
    assert not stage_root.exists() or not list(stage_root.iterdir())
