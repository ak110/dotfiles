"""PATH隔離で別名になったmise shimも公開prepareの前に除外する。"""

import os
import pathlib
import shutil
import subprocess

import pytest

from agent_toolkit._common import plugin_environment
from agent_toolkit._testing import isolation, plugin_environment_support


@pytest.mark.parametrize("masked", [False, True], ids=["direct-shims", "isolated-alias"])
def test_prepare_skips_mise_uv_after_path_isolation(tmp_path, monkeypatch, masked):
    """agent CLIを隠したsymlink経由でも、設定を要しないuv実体を選択する。"""
    suffix = ".exe" if os.name == "nt" else ""
    shims = tmp_path / "shims"
    shims.mkdir()
    mise = tmp_path / ("mise" + suffix)
    mise.write_text("mise fixture", encoding="utf-8")
    mise.chmod(0o755)
    codex = shims / ("codex" + suffix)
    codex.write_text("codex fixture", encoding="utf-8")
    codex.chmod(0o755)
    try:
        (shims / ("uv" + suffix)).symlink_to(mise)
    except OSError:
        pytest.skip("PATH隔離で使うsymlinkを作成できない環境")
    tools = tmp_path / "tools"
    tools.mkdir()
    direct_uv = tools / ("uv" + suffix)
    direct_uv.write_text("uv fixture", encoding="utf-8")
    direct_uv.chmod(0o755)
    first = isolation.build_isolated_path(str(shims), tmp_path / "isolated-path") if masked else str(shims)
    environment = {"PATH": first + os.pathsep + str(tools), plugin_environment.RUNTIME_DATA_ENV: str(tmp_path / "runtime")}

    def run(arguments, *, env, **_kwargs):
        selected = shutil.which("uv", path=env["PATH"])
        assert selected is not None
        assert pathlib.Path(selected).resolve() == direct_uv
        assert arguments[1:] == [str(tmp_path / "scripts/launch.py"), "--prepare", "agent_toolkit/agents_server_mcp.py"]
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(plugin_environment_support.process_tree, "run_with_timeout", run)
    monkeypatch.setattr(plugin_environment, "prepared_python", lambda *_args: tmp_path / "ready-python")
    assert plugin_environment_support.prepare_plugin_environment(tmp_path, environment) == tmp_path / "ready-python"
