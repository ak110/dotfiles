"""統合インストーラーの主題別公開契約を検証する。"""

import json
import pathlib
import shutil
import sys

import pytest

from _test_helpers import installer_runners, make_command_stubs, read_log_lines, run_installer
from _test_helpers import rules_url_fixture as _rules_url_fixture  # noqa: F401  # pytestがfixtureとして収集する。
from pytools._internal import common


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
    assert sum("codex plugin list --json" in line for line in lines) == 4
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
    assert not any("codex plugin add agent-toolkit@" in line for line in read_log_lines(stub_log))


def _assert_invalid_plugin_state_stops_before_plugin_add(
    kind: str,
    plugin_state: object,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    """plugin状態の不正応答で失敗し、更新を開始しない共通契約を確認する。"""
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
    assert not any("codex plugin add agent-toolkit@" in line for line in read_log_lines(stub_log))


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh未インストール")
@pytest.mark.parametrize("plugin_state", [{}, {"installed": None}, {"installed": "x"}])
def test_powershell_invalid_plugin_state_stops_before_plugin_add(
    plugin_state: object,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    """PowerShellではinstalledが配列でない応答を取得失敗として扱う。"""
    _assert_invalid_plugin_state_stops_before_plugin_add("ps1", plugin_state, tmp_path, rules_url)


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
    _assert_invalid_plugin_state_stops_before_plugin_add(kind, plugin_state, tmp_path, rules_url)


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


@pytest.mark.parametrize("kind", installer_runners())
def test_cache_restore_failure_reports_retained_backup(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """更新と復元を失敗させ、全退避bytesと失敗理由・保存先・次の操作を保持する。"""
    home = tmp_path / "home"
    home.mkdir()
    cache_root = home / ".codex/plugins/cache/ak110-dotfiles/agent-toolkit"
    original = {"1.2.0/data.bin": b"\x00\xff\r\n", "1.2.1/nested/marker": "旧版".encode()}
    for relative, body in original.items():
        path = cache_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    stub_bin, stub_log = make_command_stubs(tmp_path)
    stub = stub_bin / "codex"
    source = stub.read_text(encoding="utf-8")
    delete = '            rm -rf "$CODEX_PLUGIN_CACHE_ROOT"'
    assert source.count(delete) == 1
    stub.write_text(
        source.replace(delete, delete + '\n            printf blocked > "$CODEX_PLUGIN_CACHE_ROOT"'), encoding="utf-8"
    )

    result = run_installer(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        fail_pattern="codex plugin add agent-toolkit",
        codex_plugin_before=("1.2.2", True),
        codex_home=home / ".codex",
        delete_cache_before_failure=True,
        check=False,
    )

    assert result.returncode != 0
    assert "stub failure: codex plugin add agent-toolkit" in result.stderr
    assert "復旧に失敗" in result.stderr
    assert "退避先を変更せず" in result.stderr and "作者へ報告" in result.stderr
    backups = [path for path in tmp_path.iterdir() if path.is_dir() and (path / "1.2.0/data.bin").is_file()]
    assert len(backups) == 1
    backup = backups[0]
    assert str(backup) in result.stderr
    for relative, body in original.items():
        assert (backup / relative).read_bytes() == body
    assert (backup / "1.2.2/scripts/hook.py").read_bytes() == b"old hook\n"


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
