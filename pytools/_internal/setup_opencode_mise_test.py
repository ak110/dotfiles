"""miseのnpm shimと、移行対象OpenCodeの実体解決を分けて検証する。"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from pytools._internal import common, setup_opencode_cli


@pytest.mark.parametrize("install_fails", [False, True])
def test_mise_only_npm_is_used_but_opencode_shim_is_excluded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, install_fails: bool
) -> None:
    """唯一のnpm shimで導入し、OpenCodeはshim以外の導入先で確認する。"""
    shims = tmp_path / "mise" / "shims"
    binaries = tmp_path / "bin"
    shims.mkdir(parents=True)
    binaries.mkdir()
    suffix = ".cmd" if os.name == "nt" else ""
    npm = shims / ("npm" + suffix)
    old_opencode = shims / ("opencode" + suffix)
    installed = binaries / ("opencode" + suffix)
    for path in (npm, old_opencode):
        path.write_text("shim", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", os.pathsep.join((str(shims), str(binaries))))
    monkeypatch.setenv("MISE_DATA_DIR", str(shims.parent))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_DOTFILES_MODE", "full")
    calls: list[list[str]] = []

    def execute(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        failed = install_fails and command[1] == "install"
        if not failed and command[1] == "install":
            installed.write_text("installed", encoding="utf-8")
            installed.chmod(installed.stat().st_mode | stat.S_IXUSR)
        return subprocess.CompletedProcess(command, int(failed), "1.19.3", "install failed" if failed else "")

    monkeypatch.setattr(common, "run_subprocess", execute)
    result = setup_opencode_cli.run()
    expected = [[str(npm), "install", "--global", "opencode-ai"]]
    if not install_fails:
        expected.append([str(installed), "--version"])
    assert calls == expected
    assert result.changed is (not install_fails)
    assert result.failure is None


def test_minimal_does_not_resolve_or_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """minimalではPATH解決と導入を始めず、変更なしを返す。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_DOTFILES_MODE", "minimal")

    def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("minimalで導入経路へ到達した")

    monkeypatch.setattr(common, "resolve_executable", unexpected)
    monkeypatch.setattr(common, "run_subprocess", unexpected)
    result = setup_opencode_cli.run()
    assert not result.changed
    assert result.failure is None
