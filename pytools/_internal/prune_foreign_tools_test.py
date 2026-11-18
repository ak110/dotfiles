"""dotfiles外の重複導入物の判定と削除を検証する。"""

import json
import os
import subprocess
import sys
import typing
from pathlib import Path

import pytest

from pytools._internal import common, prune_foreign_tools, setup_mise_binary, winutils

_EXE = ".exe" if sys.platform == "win32" else ""


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)
    return path


def _layout(tmp_path: Path) -> prune_foreign_tools.Layout:
    return prune_foreign_tools.Layout(
        mise_installs=tmp_path / "mise" / "installs",
        mise_shims=frozenset({tmp_path / "mise" / "shims"}),
        winget_packages=tmp_path / "WinGet" / "Packages",
        pnpm_home=tmp_path / "pnpm",
    )


def _package(modules: Path, name: str, bins: dict[str, str] | str) -> None:
    (modules / name).mkdir(parents=True)
    (modules / name / "package.json").write_text(json.dumps({"name": name, "bin": bins}), encoding="utf-8")


def test_classify_selects_removal_by_installer(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    packages = tmp_path / "WinGet" / "Packages"
    winget_rg = _executable(packages / "BurntSushi.ripgrep.MSVC_Microsoft.Winget.Source_8wekyb3d8bbwe" / "rg-14" / "rg.exe")
    standalone = _executable(layout.pnpm_home / "pnpm.exe")
    windows_prefix = tmp_path / "npm"
    _package(windows_prefix / "node_modules", "pnpm", {"pnpm": "bin/pnpm.cjs", "pnpx": "bin/pnpx.cjs"})
    windows_launcher = _executable(windows_prefix / "pnpm.cmd")
    posix_prefix = tmp_path / "usr-local"
    _package(posix_prefix / "lib" / "node_modules", "typescript", {"tsc": "bin/tsc"})
    posix_launcher = _executable(posix_prefix / "bin" / "tsc")
    machine = _executable(tmp_path / "Program Files" / "GitHub CLI" / "gh.exe")

    assert prune_foreign_tools.classify(winget_rg, "rg", layout) == prune_foreign_tools.Removal(
        "winget", "BurntSushi.ripgrep.MSVC", common.safe_resolve(winget_rg)
    )
    assert prune_foreign_tools.classify(standalone, "pnpm", layout) == prune_foreign_tools.Removal(
        "pnpm-standalone", str(layout.pnpm_home), layout.pnpm_home
    )
    assert prune_foreign_tools.classify(windows_launcher, "pnpm", layout) == prune_foreign_tools.Removal(
        "npm", "pnpm", windows_prefix
    )
    assert prune_foreign_tools.classify(posix_launcher, "tsc", layout) == prune_foreign_tools.Removal(
        "npm", "typescript", posix_prefix
    )
    assert prune_foreign_tools.classify(machine, "gh", layout) is None


class _Host(typing.NamedTuple):
    pnpm_home: Path
    registry: dict[str, str]
    resolvable: dict[str, bool]


@pytest.fixture(name="host")
def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Host:
    """miseがpnpmを導入済みで、PNPM_HOMEにstandalone版が残るホストを組み立てる。"""
    data = tmp_path / "mise"
    shims = data / "shims"
    shims.mkdir(parents=True)
    managed_bin = data / "installs" / "pnpm" / "12.19.0"
    _executable(managed_bin / f"pnpm{_EXE}")
    pnpm_home = tmp_path / "pnpm"
    _executable(pnpm_home / f"pnpm{_EXE}")
    (pnpm_home / "store").mkdir()
    machine = tmp_path / "machine"
    _executable(machine / f"pnpm{_EXE}")
    monkeypatch.setenv("MISE_DATA_DIR", str(data))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("PNPM_HOME", str(pnpm_home))
    monkeypatch.setenv("PATH", os.pathsep.join([str(machine), str(shims), str(pnpm_home)]))
    monkeypatch.setattr(setup_mise_binary, "find_mise_binary", lambda: tmp_path / "mise.exe")
    resolvable = {"value": True}

    def run_subprocess(cmd: list[str], **_kwargs: typing.Any) -> subprocess.CompletedProcess[str]:
        if cmd[1:] == ["bin-paths"]:
            return subprocess.CompletedProcess(cmd, 0, f"{managed_bin}\n", "")
        if cmd[1] == "which":
            return subprocess.CompletedProcess(cmd, 0 if resolvable["value"] else 1, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(common, "run_subprocess", run_subprocess)
    registry = {"PNPM_HOME": str(pnpm_home), "Path": f"%PNPM_HOME%;{tmp_path / 'keep'}"}

    def read_user_env_var(name: str) -> tuple[str | None, int]:
        return registry.get(name), 2

    def write_user_env_var(name: str, value: str, _reg_type: int) -> None:
        registry[name] = value

    def delete_user_env_var(name: str) -> None:
        registry.pop(name, None)

    def broadcast_environment_change() -> None:
        pass

    monkeypatch.setattr(winutils, "read_user_env_var", read_user_env_var)
    monkeypatch.setattr(winutils, "write_user_env_var", write_user_env_var)
    monkeypatch.setattr(winutils, "delete_user_env_var", delete_user_env_var)
    monkeypatch.setattr(winutils, "broadcast_environment_change", broadcast_environment_change)
    return _Host(pnpm_home, registry, resolvable)


def test_run_removes_standalone_pnpm_and_warns_shadowing_install(host: _Host, caplog: pytest.LogCaptureFixture) -> None:
    outcome = prune_foreign_tools.run()

    assert outcome.changed
    assert not host.pnpm_home.exists()
    assert "PNPM_HOME" not in os.environ
    if sys.platform == "win32":
        assert host.registry == {"Path": host.registry["Path"]}
        assert "%PNPM_HOME%" not in host.registry["Path"]
        assert host.registry["Path"].endswith("keep")
    # PATHでmise shimより前にある導入元不明の版は削除せず、案内だけ出す。
    assert any("machine" in record.getMessage() and "先にPATH" in record.getMessage() for record in caplog.records)


def test_run_keeps_standalone_pnpm_when_mise_version_is_unavailable(host: _Host) -> None:
    host.resolvable["value"] = False

    assert not prune_foreign_tools.run().changed
    assert (host.pnpm_home / f"pnpm{_EXE}").is_file()
