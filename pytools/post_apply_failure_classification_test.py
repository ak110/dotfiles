"""post-applyの工程の失敗を、失敗した操作の種類で分類することを`post_apply.main`の終了コードで確かめる。

分類の基準は`pytools._internal.post_apply_outcome.PostApplyOutcome`が定める。
取得・導入の失敗は警告とスキップで終了コード0、設定の書き換え・撤去の失敗は失敗で終了コード1になる。
`_DEFAULT_STEPS`の全工程について、各工程の失敗経路を模す手段を`_SIMULATIONS`に持ち、
工程を加えたときに表への追加漏れを`test_simulations_cover_all_default_steps`が検出する。
"""

import datetime
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal import (
    claude_common,
    cleanup_paths,
    cleanup_user_path,
    common,
    install_claude_plugins,
    install_codex_plugins,
    install_libarchive,
    prune_claude_plugin_cache,
    remove_codex_claude_mcp,
    remove_legacy_codex_mcp_from_claude,
    restore_codex_logs,
    setup_agy_cli,
    setup_cli_common,
    setup_codex_cli,
    setup_codex_links,
    setup_dotfiles_autoupdate,
    setup_herdr_cli,
    setup_media_remote,
    setup_mise,
    setup_registry,
    setup_sendto_shortcuts,
    setup_statusline_binary,
    setup_tmux_plugins,
    sync_agent_toolkit_rules,
    update_claude_settings,
    update_npmrc,
    update_vscode_settings,
    warmup_agents_server,
    warmup_pyfltr_mcp,
    winutils,
)

# pylint: disable=protected-access

_Simulation = Callable[[pytest.MonkeyPatch, Path], None]
_PROGRAMMING_ERRORS = ("AttributeError", "TypeError", "KeyError", "NameError", "AssertionError", "StopIteration")


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _raise(error: Exception) -> Callable[..., object]:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise error

    return fail


# --- 取得・導入の失敗（スキップ、終了コード0） ---


def _mise_install_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del tmp_path
    monkeypatch.setattr(setup_mise, "find_mise_binary", lambda: None)
    monkeypatch.setattr(setup_mise, "_ensure_mise_installed", lambda: False)


def _codex_cli_installer_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del tmp_path
    monkeypatch.setattr(setup_cli_common, "is_windows_cli_running", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(setup_codex_cli, "_run_installer", lambda _client: _completed(1, stderr="network"))


def _claude_cli_installer_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_cli_common, "is_windows_cli_running", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(setup_cli_common, "run_official_installer", lambda *_args, **_kwargs: (None, "取得に失敗"))


def _agy_cli_installer_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(setup_agy_cli, "_launcher_path", lambda: tmp_path / "agy")
    monkeypatch.setattr(setup_cli_common, "run_official_installer", lambda *_args, **_kwargs: (None, "取得に失敗"))


def _herdr_cli_installer_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(setup_herdr_cli, "_launcher_path", lambda: tmp_path / "herdr")
    monkeypatch.setattr(setup_cli_common, "run_official_installer", lambda *_args, **_kwargs: (None, "取得に失敗"))


def _tmux_clone_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(setup_tmux_plugins, "_TMUX_PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(
        setup_tmux_plugins,
        "_PLUGINS",
        (
            setup_tmux_plugins._Plugin(
                dest=tmp_path / "plugins" / "p", origin="https://example.invalid/p.git", pin="v1", pin_is_tag=True
            ),
        ),
    )
    monkeypatch.setattr(common, "run_subprocess", lambda *_args, **_kwargs: _completed(128, stderr="unreachable"))


def _claude_plugins_install_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del tmp_path
    monkeypatch.setattr(install_claude_plugins, "_install_plugins", _raise(RuntimeError("agent-toolkit (install)")))


def _codex_plugins_add_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(common, "resolve_executable", lambda _name, **_kwargs: tmp_path / "codex")
    monkeypatch.setattr(install_codex_plugins, "_remove_unused_plugins", _raise(RuntimeError("Codex plugin addに失敗")))


def _uv_environment_build_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = tmp_path / "plugin" / "agent_toolkit" / "agents_server_mcp.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(common, "resolve_executable", lambda name, **_kwargs: tmp_path / name)
    monkeypatch.setattr(warmup_agents_server, "_targets", lambda: [script])
    monkeypatch.setattr(common, "run_subprocess", lambda *_args, **_kwargs: _completed(1, stderr="resolution failed"))


def _uvx_environment_build_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    definition = tmp_path / ".mcp.json"
    definition.write_text(
        json.dumps({"mcpServers": {"pyfltr": {"command": "uvx", "args": ["--from", "pyfltr", "pyfltr", "mcp"]}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(common, "resolve_executable", lambda name, **_kwargs: tmp_path / name)
    monkeypatch.setattr(warmup_pyfltr_mcp.plugin_warmup, "agent_toolkit_targets", lambda *_args, **_kwargs: [definition])
    monkeypatch.setattr(common, "run_subprocess", lambda *_args, **_kwargs: None)


def _libarchive_download_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del tmp_path
    monkeypatch.setattr(install_libarchive, "_is_already_available", lambda: False)
    monkeypatch.setattr(install_libarchive, "_download_dlls", _raise(OSError("network unreachable")))


def _statusline_build_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del tmp_path
    monkeypatch.setattr(setup_statusline_binary, "_find_development_tree", _raise(RuntimeError("ビルドに失敗")))


# --- 設定の書き換え・撤去の失敗（失敗、終了コード1） ---


def _bin_path_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del tmp_path
    monkeypatch.setattr(winutils, "append_user_path", _raise(PermissionError("denied")))


def _msys_env_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del tmp_path
    monkeypatch.setattr(winutils, "read_user_env_var", lambda _name: (None, 1))
    monkeypatch.setattr(winutils, "import_winreg", lambda: type("_Winreg", (), {"REG_SZ": 1}))
    monkeypatch.setattr(winutils, "write_user_env_var", _raise(PermissionError("denied")))


def _user_env_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "share").mkdir()
    (tmp_path / "share" / "user.env").write_text("KEY=1\n", encoding="utf-8")
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(winutils, "read_user_env_var", lambda _name: (None, 1))
    monkeypatch.setattr(winutils, "import_winreg", lambda: type("_Winreg", (), {"REG_SZ": 1}))
    monkeypatch.setattr(winutils, "write_user_env_var", _raise(PermissionError("denied")))


def _vscode_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(update_vscode_settings, "_settings_path", lambda **_kwargs: settings)
    monkeypatch.setattr(common, "write_settings_hybrid", lambda *_args, **_kwargs: False)


def _ssh_config_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    conf_d = tmp_path / ".ssh" / "conf.d"
    conf_d.mkdir(parents=True)
    (conf_d / "a.conf").write_text("Host a\n", encoding="utf-8")
    monkeypatch.setattr(common, "atomic_write_text", lambda *_args, **_kwargs: False)


def _cleanup_removal_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base = tmp_path / "home" / ".claude"
    (base / "old").mkdir(parents=True)
    entry = cleanup_paths.RemovedPath(Path("old"), datetime.date.today())
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS", {base: [entry]})
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS_IF_CONTENT", {})
    monkeypatch.setattr(cleanup_paths.shutil, "rmtree", _raise(PermissionError("denied")))


def _npm_config_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(update_npmrc.shutil, "which", lambda _name: str(tmp_path / "pnpm"))
    monkeypatch.setattr(common, "run_subprocess", lambda *_args, **_kwargs: _completed(1, stderr="EACCES"))


def _codex_claude_mcp_removal_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(common, "resolve_executable", lambda _name, **_kwargs: tmp_path / "codex")
    results = iter([_completed(0, stdout="{}"), _completed(1, stderr="permission denied")])
    monkeypatch.setattr(remove_codex_claude_mcp.common, "run_subprocess", lambda *_args, **_kwargs: next(results))


def _rules_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rules = tmp_path / "dotfiles" / "agent-toolkit" / "rules"
    rules.mkdir(parents=True)
    (rules / "01.md").write_text("rule\n", encoding="utf-8")
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: tmp_path / "dotfiles")
    monkeypatch.setattr(claude_common, "CLAUDE_HOME", tmp_path / ".claude")
    monkeypatch.setattr(sync_agent_toolkit_rules, "CODEX_HOME", tmp_path / ".codex")
    monkeypatch.setattr(common, "atomic_write_text", lambda *_args, **_kwargs: False)


def _codex_link_blocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "dotfiles" / "src").mkdir(parents=True)
    (tmp_path / ".codex" / "skills" / "x").mkdir(parents=True)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: tmp_path / "dotfiles")
    monkeypatch.setattr(setup_codex_links, "CODEX_HOME", tmp_path / ".codex")
    monkeypatch.setattr(setup_codex_links, "_LINKS", {Path("skills/x"): Path("src")})


def _codex_logs_copy_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del tmp_path
    monkeypatch.setattr(restore_codex_logs, "_restore", _raise(OSError("No space left on device")))


def _plugin_cache_removal_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = tmp_path / "plugins" / "cache"
    old = cache / "market" / "plugin" / "0.1.0"
    old.mkdir(parents=True)
    monkeypatch.setattr(prune_claude_plugin_cache, "_INSTALLED_PLUGINS_PATH", tmp_path / "plugins" / "installed_plugins.json")
    monkeypatch.setattr(prune_claude_plugin_cache, "_current_install_paths", lambda: {("market", "plugin"): set()})
    monkeypatch.setattr(prune_claude_plugin_cache, "_expired_versions", lambda *_args: [old])
    monkeypatch.setattr(prune_claude_plugin_cache.shutil, "rmtree", _raise(PermissionError("denied")))


def _codex_snapshot_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del tmp_path
    monkeypatch.setattr(post_apply.codex_plugin_manifests, "sync", _raise(OSError("派生JSONの書き込みに失敗")))


def _legacy_codex_mcp_removal_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(common, "resolve_executable", lambda _name, **_kwargs: tmp_path / "claude")
    monkeypatch.setattr(
        remove_legacy_codex_mcp_from_claude, "_load_user_codex", lambda: {"command": "codex", "args": ["mcp-server"]}
    )
    monkeypatch.setattr(claude_common, "run_claude", lambda *_args, **_kwargs: _completed(1, stderr="locked"))


def _claude_settings_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(update_claude_settings, "_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(update_claude_settings, "_CONFIG_PATH", tmp_path / ".claude.json")
    monkeypatch.setattr(common, "write_settings_hybrid", lambda *_args, **_kwargs: False)


def _euryale_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "dotfiles"
    manifest = root / "agent-toolkit" / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
    uv = tmp_path / "uv"
    uv.write_text("", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(claude_common, "is_euryale", lambda: True)
    monkeypatch.setattr(common, "resolve_uv_path", lambda: uv)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(common, "atomic_write_text", lambda *_args, **_kwargs: False)
    return root


def _atk_serve_launcher_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _euryale_root(monkeypatch, tmp_path)


def _autoupdate_unit_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = _euryale_root(monkeypatch, tmp_path)
    script = root / setup_dotfiles_autoupdate._SCRIPT_RELATIVE
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("", encoding="utf-8")


def _registry_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del tmp_path
    monkeypatch.setattr(setup_registry, "_apply_all", _raise(PermissionError("access denied")))


def _sendto_shortcut_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    setup_sendto_shortcuts._sendto_dir().mkdir(parents=True)
    for shortcut in setup_sendto_shortcuts._SHORTCUTS:
        target = tmp_path / shortcut.target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    monkeypatch.setattr(common, "run_subprocess", lambda *_args, **_kwargs: None)


def _media_remote_removal_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    startup = setup_media_remote._startup_dir()
    startup.mkdir(parents=True)
    lnk = startup / setup_media_remote.LNK_NAME
    lnk.write_text("", encoding="utf-8")
    monkeypatch.setattr(setup_media_remote.socket, "gethostname", lambda: "other")
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == lnk:
            raise PermissionError("in use")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def _user_path_write_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(winutils, "read_user_env_var", lambda _name: (f"{tmp_path};{tmp_path}", 2))
    monkeypatch.setattr(winutils, "read_system_env_var", lambda _name: ("", 2))
    monkeypatch.setattr(winutils, "write_user_env_var", _raise(PermissionError("denied")))
    monkeypatch.setattr(cleanup_user_path.os, "environ", {"USERPROFILE": str(tmp_path)})


# 工程名ごとに、失敗を模す手段と期待する終了コードの組を並べる。
# 1つの工程が取得と書き換えの両方を行う場合は、両方の組を並べる。
_SIMULATIONS: dict[str, list[tuple[_Simulation, int]]] = {
    "bin PATH 登録 (Windows)": [(_bin_path_write_fails, 1)],
    "MSYS 環境変数 (Windows)": [(_msys_env_write_fails, 1)],
    "user.env 環境変数 (Windows)": [(_user_env_write_fails, 1)],
    "VSCode 設定": [(_vscode_write_fails, 1)],
    "SSH config": [(_ssh_config_write_fails, 1)],
    "旧配布物の削除": [(_cleanup_removal_fails, 1)],
    "npm/pnpm サプライチェーン対策": [(_npm_config_write_fails, 1)],
    "mise セットアップ": [(_mise_install_fails, 0)],
    "Codex CLI の導入と更新": [(_codex_cli_installer_fails, 0)],
    "Codex の Claude MCP 登録削除": [(_codex_claude_mcp_removal_fails, 1)],
    "Claude Code CLI の導入と更新": [(_claude_cli_installer_fails, 0)],
    "Antigravity CLI の導入": [(_agy_cli_installer_fails, 0)],
    "Herdr CLI の導入と更新": [(_herdr_cli_installer_fails, 0)],
    "agent-toolkit ルールの同期": [(_rules_write_fails, 1)],
    "Codex リンクの同期": [(_codex_link_blocked, 1)],
    "Codex 診断ログの通常ストレージ復元 (Linux)": [(_codex_logs_copy_fails, 1)],
    "tmux プラグインの導入 (Linux)": [(_tmux_clone_fails, 0)],
    "Claude Code plugin のインストール": [(_claude_plugins_install_fails, 0)],
    "Claude Code plugin cache の旧版削除": [(_plugin_cache_removal_fails, 1)],
    "Codex plugin snapshot の生成": [(_codex_snapshot_write_fails, 1)],
    "Codex plugin のインストール": [(_codex_plugins_add_fails, 0)],
    "agents_serverのuv環境ウォームアップ": [(_uv_environment_build_fails, 0)],
    "pyfltr MCPのuv環境ウォームアップ": [(_uvx_environment_build_fails, 0)],
    "旧Codex User scope MCP登録の移行": [(_legacy_codex_mcp_removal_fails, 1)],
    "Claude 設定": [(_claude_settings_write_fails, 1)],
    "libarchive (Windows)": [(_libarchive_download_fails, 0)],
    "claude-statusline バイナリの取得": [(_statusline_build_fails, 0)],
    "atk serve 自動起動セットアップ (Linux)": [(_atk_serve_launcher_write_fails, 1)],
    "dotfiles自動更新タイマー セットアップ (Linux)": [(_autoupdate_unit_write_fails, 1)],
    "Windowsレジストリ設定": [(_registry_write_fails, 1)],
    "SendTo ショートカット (Windows)": [(_sendto_shortcut_fails, 1)],
    "メディアリモコン自動起動 (Windows/stheno)": [(_media_remote_removal_fails, 1)],
    "ユーザー PATH 整理 (Windows)": [(_user_path_write_fails, 1)],
}

_CASES = [
    pytest.param(name, simulation, exit_code, id=f"{name}-{getattr(simulation, '__name__', '')}")
    for name, simulations in _SIMULATIONS.items()
    for simulation, exit_code in simulations
]


def test_simulations_cover_all_default_steps() -> None:
    """分類の表が`_DEFAULT_STEPS`の全工程を持つ。"""
    assert set(_SIMULATIONS) == {step.name for step in post_apply._DEFAULT_STEPS}


@pytest.mark.parametrize(("name", "simulation", "exit_code"), _CASES)
def test_failure_classification_per_step(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    simulation: _Simulation,
    exit_code: int,
) -> None:
    """取得・導入の失敗はスキップで終了コード0、設定の書き換え・撤去の失敗は失敗で終了コード1になる。"""
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", tmp_path / "update-dotfiles.log")
    monkeypatch.setattr(post_apply.sync_report, "REPORT_PATH", tmp_path / "state" / "sync-report.json")
    step = next(step for step in post_apply._DEFAULT_STEPS if step.name == name)
    simulation(monkeypatch, tmp_path)

    # 対象OSの判定と先行工程を外し、工程の`run`だけを実行する。
    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(runner=lambda: post_apply.run([(step.name, step.run)]))

    assert exc_info.value.code == exit_code
    report = json.loads((tmp_path / "state" / "sync-report.json").read_text(encoding="utf-8"))
    failed_steps = report["post_apply"]["failed_steps"]
    assert [failed["name"] for failed in failed_steps] == ([name] if exit_code else [])
    # 模した失敗とは別の不具合（属性の誤りなど）で失敗していないことを確かめる。
    assert not [failed for failed in failed_steps if (failed["reason"] or "").startswith(_PROGRAMMING_ERRORS)]


def test_install_and_write_failures_in_one_step_are_classified_separately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """取得と書き換えの両方を行う工程は、書き換えの失敗だけを失敗と数える。"""
    del tmp_path
    monkeypatch.setattr(install_libarchive, "_is_already_available", lambda: True)
    monkeypatch.setattr(install_libarchive, "_persist_libarchive_env_var", _raise(PermissionError("denied")))
    outcome = install_libarchive.run()
    assert outcome.failure is not None
    assert "LIBARCHIVE" in outcome.failure
