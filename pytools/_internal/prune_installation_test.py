"""隔離ホームで対象分類、使用中保護とpruneの実際の削除を確かめる。"""

# pylint: disable=protected-access  # 対象分類・削除境界の内部関数を隔離環境で検証する。

import json
import os
import stat
import subprocess
import types
from pathlib import Path

import pytest
from agent_toolkit._common import dotfiles_mode

from pytools._internal import common, prune_installation, setup_mise


def _prepare(tmp_path, monkeypatch, mode):
    root = tmp_path / "dotfiles"
    root.mkdir()
    (root / "mise.toml").write_text('[tools]\nprek="latest"\nrust="1"\nactionlint="latest"\n')
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, mode)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(prune_installation, "_stop_daemon", lambda: None)
    monkeypatch.setattr(prune_installation, "_active_users", lambda paths: [])
    monkeypatch.setattr(setup_mise, "find_mise_binary", lambda: Path("/fixture/mise"))
    monkeypatch.setattr(common, "resolve_executable", lambda name: Path("/fixture") / name)
    monkeypatch.setattr(prune_installation.shutil, "which", lambda name: str(Path("/fixture") / name))
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        output = (
            json.dumps({"node": [{"version": "24"}], "rust": [{"version": "1"}], "actionlint": [{"version": "1"}]})
            if command[1:4] == ["ls", "--installed", "--json"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(common, "run_subprocess", run)
    return root, calls


def test_minimal_prune_removes_only_listed_paths_and_clears_caches(tmp_path, monkeypatch):
    _, calls = _prepare(tmp_path, monkeypatch, "minimal")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "dependency").write_text("installed")
    retained = tmp_path / "settings.json"
    retained.write_text("keep")
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: (runtime,))
    result = prune_installation.run()
    assert result.failure is None
    assert result.changed
    assert not runtime.exists()
    assert retained.read_text() == "keep"
    assert ["/fixture/mise", "uninstall", "--all", "node"] in calls
    assert ["/fixture/mise", "uninstall", "--all", "rust"] in calls
    assert ["/fixture/mise", "prune", "-y"] in calls
    assert ["/fixture/mise", "reshim", "--force"] in calls
    assert ["/fixture/mise", "cache", "clear"] in calls
    assert ["/fixture/uv", "cache", "prune"] in calls


def test_active_old_environment_survives_prune(tmp_path, monkeypatch):
    _, calls = _prepare(tmp_path, monkeypatch, "minimal")
    environment = tmp_path / "old-environment"
    environment.mkdir()
    (environment / "python").write_text("in use")
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: (environment,))
    monkeypatch.setattr(prune_installation, "_active_users", lambda paths: ["agents_server (pid 123)"])
    result = prune_installation.run()
    assert result.failure is not None
    assert result.next_action is not None
    assert "update-dotfiles --prune" in result.next_action
    assert (environment / "python").read_text() == "in use"
    assert not calls


@pytest.mark.parametrize("legacy_uvx", [False, True])
def test_process_scan_detects_python_from_old_shared_environment(tmp_path, monkeypatch, legacy_uvx):
    """実行ファイルの実体が共通Pythonでも、argvの仮想環境パスで参照を判定する。"""
    environment = tmp_path / "environments/old"
    process = types.SimpleNamespace(
        pid=123,
        info={
            "name": "python",
            "exe": "/runtime/python",
            "cmdline": (
                [str(tmp_path / "uv-cache/bin/pyfltr"), "mcp"]
                if legacy_uvx
                else [str(environment / "bin/python"), "/plugin/agent_toolkit/agents_server_mcp.py"]
            ),
            "uids": types.SimpleNamespace(real=os.getuid()) if os.name != "nt" else None,
            "username": prune_installation.psutil.Process().username(),
            "status": "running",
        },
    )
    monkeypatch.setattr(prune_installation.psutil, "process_iter", lambda attributes: [process])
    monkeypatch.setattr(prune_installation.codex_processes, "codex_processes", lambda: ())
    assert prune_installation._active_users((environment,)) == ["python (pid 123)"]


def test_full_keeps_project_development_tools_and_old_environments(tmp_path, monkeypatch):
    root, calls = _prepare(tmp_path, monkeypatch, "full")
    assert "actionlint" not in prune_installation.excluded_tools(root, minimal=False)
    result = prune_installation.run()
    assert result.failure is None
    assert all("uninstall" not in command for command in calls)
    assert all("uv" not in command[0] for command in calls)


def test_invalid_runtime_home_preserves_entire_worktree(tmp_path, monkeypatch):
    """ランタイム用の環境変数が作業ツリー全体を指した場合は削除前に止める。"""
    root, calls = _prepare(tmp_path, monkeypatch, "minimal")
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: (root,))
    result = prune_installation.run()
    assert result.failure is not None
    assert "作業ツリー全体" in result.failure
    assert (root / "mise.toml").is_file()
    assert not calls


def test_symlink_removal_preserves_external_target(tmp_path):
    target = tmp_path / "retained"
    target.mkdir()
    (target / "content").write_text("keep")
    link = tmp_path / "runtime"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinkを作成できない環境")
    assert prune_installation._remove(link)
    assert (target / "content").read_text() == "keep"


def test_removal_paths_follow_installer_roots_and_keep_opencode_data(tmp_path, monkeypatch):
    """実インストーラーの配置とmiseの上書き先を削除対象へ対応付ける。"""
    local = tmp_path / "local"
    data = tmp_path / "mise-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("MISE_DATA_DIR", str(data))
    monkeypatch.delenv("MISE_DOTNET_ROOT", raising=False)
    paths = prune_installation.removal_paths()
    assert local / "agy" in paths
    assert data / "dotnet-root" in paths
    assert Path.home() / ".local/share/opencode" not in paths


def test_prune_command_failure_reports_original_error(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch, "minimal")
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: ())

    def fail(command, **_kwargs):
        return subprocess.CompletedProcess(command, 3, "", "fixture failure")

    monkeypatch.setattr(common, "run_subprocess", fail)
    result = prune_installation.run()
    assert result.failure is not None
    assert "fixture failure" in result.failure


@pytest.mark.parametrize(("mode", "npm_fails"), [("minimal", False), ("minimal", True), ("full", False)])
def test_mise_only_npm_prune_keeps_order_and_mode_boundary(tmp_path, monkeypatch, mode, npm_fails):
    """npmの唯一の提供元を使い、Node削除前の順序・失敗停止・full除外を保つ。"""
    normal_lookup = prune_installation.shutil.which
    real_resolve = common.resolve_executable
    _, calls = _prepare(tmp_path, monkeypatch, mode)
    shims = tmp_path / "mise-data" / "shims"
    shims.mkdir(parents=True)
    npm = shims / ("npm.cmd" if os.name == "nt" else "npm")
    npm.write_text("shim", encoding="utf-8")
    npm.chmod(npm.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(shims))
    monkeypatch.setenv("MISE_DATA_DIR", str(shims.parent))
    monkeypatch.setattr(prune_installation.shutil, "which", normal_lookup)
    monkeypatch.setattr(common, "resolve_executable", lambda name: Path("/fixture/uv") if name == "uv" else real_resolve(name))
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: ())
    original_run = common.run_subprocess

    def execute(command, **kwargs):
        if command[0] == str(npm) and npm_fails:
            calls.append(command)
            return subprocess.CompletedProcess(command, 3, "", "npm uninstall failed")
        return original_run(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", execute)
    result = prune_installation.run()
    if mode == "full":
        assert all(command[0] != str(npm) for command in calls)
        assert result.failure is None
    else:
        assert calls[0][:3] == [str(npm), "uninstall", "--global"]
        if npm_fails:
            assert len(calls) == 1
            assert result.failure is not None
            assert "npm uninstall failed" in result.failure
        else:
            assert result.failure is None
            assert calls[1][:3] == ["/fixture/mise", "ls", "--installed"]
            assert ["/fixture/mise", "uninstall", "--all", "node"] in calls
