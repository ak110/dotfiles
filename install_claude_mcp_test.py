"""統合インストーラーの主題別公開契約を検証する。"""

import json
import pathlib

import pytest

from _test_helpers import REPO_ROOT, installer_runners, make_command_stubs, read_log_lines, run_installer
from _test_helpers import rules_url_fixture as _rules_url_fixture  # noqa: F401  # pytestがfixtureとして収集する。

LEGACY_CODEX_MCP_CASES = REPO_ROOT / "pytools" / "_internal" / "legacy_codex_mcp_cases.json"


def _write_claude_config(home: pathlib.Path, value: object) -> None:
    (home / ".claude.json").write_text(json.dumps(value), encoding="utf-8")


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
