"""prune_installation_test.py、prune_installation_npm_test.py、prune_installation_maintenance_test.pyが共有する、隔離したホームと副作用の代替。"""

import json
import subprocess
from pathlib import Path

import platformdirs
import pytest
from agent_toolkit._common import dotfiles_mode

from pytools import post_apply
from pytools._internal import common, post_apply_outcome, prune_installation, setup_mise, warmup_pyfltr_mcp

UV_CACHE_MAINTENANCE = ["uv-cache-maintenance"]
"""`prepare`が`calls`へ記録する、キャッシュ整理の独立プロセスの起動。"""


def state_path(name: str) -> Path:
    """更新・整理・独立プロセスが共有する状態領域のパスを、公開の配置規則（platformdirs）で求める。"""
    return Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / name


def prepare(tmp_path, monkeypatch, mode, installed=None):
    """隔離したホームとdotfilesの作業ツリーを用意し、整理の外部コマンドを記録する代替へ差し替える。"""
    home = tmp_path / "isolated-home"
    home.mkdir()
    # HOMEだけでは、ホストから継承した共有ランタイムの明示配置が残る。
    for name, path in {
        "HOME": home,
        "USERPROFILE": home,
        "RUSTUP_HOME": home / ".rustup",
        "CARGO_HOME": home / ".cargo",
        "CODEX_HOME": home / ".codex",
        "CLAUDE_CONFIG_DIR": home / ".claude",
        "MISE_DATA_DIR": home / "mise",
        "MISE_DOTNET_ROOT": home / "mise/dotnet-root",
        "AGENT_TOOLKIT_RUNTIME_DATA": home / "runtime",
        "LOCALAPPDATA": home / "AppData/Local",
        "XDG_DATA_HOME": home / ".local/share",
        "XDG_STATE_HOME": home / ".local/state",
        "XDG_CACHE_HOME": home / ".cache",
    }.items():
        monkeypatch.setenv(name, str(path))
    monkeypatch.setattr(Path, "home", lambda: home)
    root = tmp_path / "dotfiles"
    root.mkdir()
    (root / "mise.toml").write_text('[tools]\nprek="latest"\nrust="1"\nactionlint="latest"\n')
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, mode)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(prune_installation, "_stop_daemon", lambda: None)
    monkeypatch.setattr(prune_installation, "_active_users", lambda paths: [])
    monkeypatch.setattr(prune_installation, "_remove", lambda _path: False)
    monkeypatch.setattr(setup_mise, "find_mise_binary", lambda: Path("/fixture/mise"))
    monkeypatch.setattr(common, "resolve_executable", lambda name: Path("/fixture") / name)
    monkeypatch.setattr(prune_installation.shutil, "which", lambda name, path=None: str(Path("/fixture") / name))
    npm_modules = tmp_path / "npm-prefix" / "lib" / "node_modules"
    for package in ("opencode-ai", "@google/gemini-cli", "aws-cdk"):
        manifest = npm_modules / package / "package.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": package}), encoding="utf-8")
    calls = []

    def start_cache_maintenance() -> int:
        calls.append(UV_CACHE_MAINTENANCE)
        return 4242

    monkeypatch.setattr(prune_installation, "start_cache_maintenance", start_cache_maintenance)
    monkeypatch.setattr(warmup_pyfltr_mcp, "run", post_apply_outcome.PostApplyOutcome)

    def run(command, **_kwargs):
        calls.append(command)
        if command[-2:] == ["root", "--global"]:
            return subprocess.CompletedProcess(command, 0, str(npm_modules), "")
        output = (
            str(tmp_path / "uv-cache")
            if command[1:] == ["cache", "dir"]
            else (
                json.dumps(
                    installed
                    if installed is not None
                    else {
                        "node": [{"version": "24", "install_path": str(tmp_path / "mise-node-24")}],
                        "rust": [{"version": "1"}],
                        "actionlint": [{"version": "1"}],
                    }
                )
                if command[1:4] == ["ls", "--installed", "--json"]
                else ""
            )
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(common, "run_subprocess", run)
    return root, calls


def maintain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """独立プロセスの公開入口を実行し、永続ログの本文を返す。"""
    log = tmp_path / "update-dotfiles.log"
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", log)
    post_apply.main(["--internal-uv-cache-maintenance"])
    return log.read_text(encoding="utf-8") if log.exists() else ""
