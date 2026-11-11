"""統合インストーラーの主題別公開契約を検証する。"""

import json
import os
import pathlib
import re
import stat
import subprocess

import pytest

from _test_helpers import INSTALL_PS1, INSTALL_SH, installer_runners, make_command_stubs, read_log_lines, run_installer
from _test_helpers import rules_url_fixture as _rules_url_fixture  # noqa: F401  # pytestがfixtureとして収集する。


@pytest.mark.parametrize("kind", installer_runners())
def test_basics_only_preserves_rules_and_home_operations(kind: str, tmp_path: pathlib.Path) -> None:
    """公開bootstrapの単独導入は既存規範とatkを保持し、uv・download・MCPを要求しない。"""
    home = tmp_path / "home[standalone]"
    home.mkdir()
    kept = {
        ".claude/rules/agent-toolkit/02-claude-code.md": "既存運用規範",
        ".claude/rules/agent-basics/legacy.md": "旧規範",
        ".local/bin/atk": "既存ラッパー",
        ".claude.json": '{"mcpServers":{"codex":{"command":"codex","args":["mcp-server"]}}}',
    }
    for name, body in kept.items():
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    stub_bin, stub_log = make_command_stubs(tmp_path, omit="uv")
    run_installer(kind, home, "http://127.0.0.1:1/never-download", stub_bin=stub_bin, stub_log=stub_log, basics_only=True)
    for name, body in kept.items():
        assert (home / name).read_text(encoding="utf-8") == body
    assert not (home / ".claude/rules-stage").exists()
    assert not (home / ".local/bin/atk-hook").exists()
    assert not (home / ".local/bin/atk-hook.cmd").exists()
    lines = read_log_lines(stub_log)
    assert "codex plugin add agent-basics@ak110-dotfiles --json" in lines
    assert lines.index("codex plugin marketplace upgrade ak110-dotfiles --json") < lines.index(
        "codex plugin add agent-basics@ak110-dotfiles --json"
    )
    assert not any("agent-toolkit@" in line or line.startswith(("uv ", "claude mcp ", "codex app-server ")) for line in lines)


@pytest.mark.parametrize("kind", installer_runners())
def test_bootstrap_installs_basics_before_toolkit(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """公開bootstrapはClaude・Codexの両ホストで品質プラグインを先に導入する。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)
    run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)
    lines = read_log_lines(stub_log)
    assert lines.index("codex plugin marketplace upgrade ak110-dotfiles --json") < lines.index(
        "codex plugin add agent-basics@ak110-dotfiles --json"
    )
    for host, operation, suffix in (("claude", "install", "--scope=user"), ("codex", "add", "--json")):
        assert lines.index(f"{host} plugin {operation} agent-basics@ak110-dotfiles {suffix}") < lines.index(
            f"{host} plugin {operation} agent-toolkit@ak110-dotfiles {suffix}"
        )


@pytest.mark.parametrize("kind", installer_runners())
@pytest.mark.parametrize("fault", ["", "add", "version", "enabled", "skills"])
def test_basics_bootstrap_verifies_update_and_retains_previous_caches(kind: str, tmp_path: pathlib.Path, fault: str) -> None:
    """basics更新の成功・失敗を検収し、CLIが削除する既存cacheを全版復旧する。"""
    home = tmp_path / "home"
    home.mkdir()
    cache = home / ".codex/plugins/cache/ak110-dotfiles/agent-basics"
    for version in ("0.8.0", "0.9.0"):
        marker = cache / version / "marker"
        marker.parent.mkdir(parents=True)
        marker.write_text(version, encoding="utf-8")
    stub_bin, stub_log = make_command_stubs(tmp_path, omit="uv")
    result = run_installer(
        kind,
        home,
        "http://127.0.0.1:1/never-download",
        stub_bin=stub_bin,
        stub_log=stub_log,
        basics_only=True,
        check=False,
        extra_env={"CODEX_BASICS_FAULT": fault},
    )
    assert (result.returncode == 0) is (fault == ""), result.stderr
    for version in ("0.8.0", "0.9.0"):
        assert (cache / version / "marker").read_text(encoding="utf-8") == version
    if not fault:
        assert (cache / "1.0.0/skills/writing-standards/SKILL.md").is_file()
    assert not any(line.startswith("codex app-server ") for line in read_log_lines(stub_log))


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
        "$scriptPath --check-dependencies > $null" in powershell_source
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
    assert len(warmups) == 4
    assert sum("--prepare" in command for command in warmups) == 2
    assert sum("--check-dependencies" in command for command in warmups) == 2
    assert any(str(home / "claude-plugin" / "agent_toolkit" / "agents_server_mcp.py") in line for line in warmups)
    assert any(
        str(home / ".codex" / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3/agent_toolkit/agents_server_mcp.py") in line
        for line in warmups
    )


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
