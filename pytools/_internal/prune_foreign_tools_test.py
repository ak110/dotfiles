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


def _shim(path: Path, key: str, entry: str) -> Path:
    _executable(path).write_text(f'"%dp0%\\node_modules\\{key}\\{entry}" %*\n', encoding="utf-8")
    return path


def test_classify_selects_removal_by_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    packages = tmp_path / "WinGet" / "Packages"
    winget_rg = _executable(packages / "BurntSushi.ripgrep.MSVC_Microsoft.Winget.Source_8wekyb3d8bbwe" / "rg-14" / "rg.exe")
    standalone = _executable(layout.pnpm_home / "pnpm.exe")
    windows_prefix = tmp_path / "npm"
    _package(windows_prefix / "node_modules", "pnpm", {"pnpm": "bin/pnpm.cjs", "pnpx": "bin/pnpx.cjs"})
    windows_launcher = _shim(windows_prefix / "pnpm.cmd", "pnpm", "bin\\pnpm.cjs")
    posix_prefix = tmp_path / "usr-local"
    _package(posix_prefix / "lib" / "node_modules", "typescript", {"tsc": "bin/tsc"})
    posix_launcher = _shim(posix_prefix / "bin" / "tsc", "typescript", "bin\\tsc")
    machine = _executable(tmp_path / "Program Files" / "GitHub CLI" / "gh.exe")

    node_windows = tmp_path / "Program Files" / "nodejs"
    _executable(node_windows / "node.exe")
    _package(node_windows / "node_modules", "npm", {"npm": "bin/npm-cli.js"})
    node_windows_npm = _executable(node_windows / "npm.cmd")
    node_posix = tmp_path / "usr"
    _executable(node_posix / "bin" / "node")
    _package(node_posix / "lib" / "node_modules", "npm", {"npm": "bin/npm-cli.js"})
    node_posix_npm = _executable(node_posix / "bin" / "npm")

    assert prune_foreign_tools.classify(winget_rg, "rg", layout) == prune_foreign_tools.Removal(
        "winget", "BurntSushi.ripgrep.MSVC", common.safe_resolve(winget_rg)
    )
    assert prune_foreign_tools.classify(standalone, "pnpm", layout) == prune_foreign_tools.Removal(
        "pnpm-standalone", str(layout.pnpm_home), layout.pnpm_home
    )

    monkeypatch.setattr(prune_foreign_tools.sys, "platform", "win32")
    assert prune_foreign_tools.classify(windows_launcher, "pnpm", layout) == prune_foreign_tools.Removal(
        "npm", "pnpm", windows_prefix
    )
    assert prune_foreign_tools.classify(machine, "gh", layout) is None
    assert prune_foreign_tools.classify(node_windows_npm, "npm", layout) is None
    monkeypatch.setattr(prune_foreign_tools.sys, "platform", "linux")
    assert prune_foreign_tools.classify(posix_launcher, "tsc", layout) == prune_foreign_tools.Removal(
        "npm", "typescript", posix_prefix
    )
    assert prune_foreign_tools.classify(node_posix_npm, "npm", layout) is None


@pytest.mark.parametrize(
    ("package", "bins"),
    [
        ("@openai/codex", {"legacy": "bin/codex.js"}),
        ("@anthropic-ai/claude-code", {"legacy": "cli.js"}),
        ("other", {"legacy": "bin/legacy.js", "codex": "bin/codex.js"}),
        ("other", {"legacy": "bin/legacy.js", "nested/claude.ps1": "bin/claude.js"}),
    ],
)
def test_classify_excludes_legacy_cli_packages_and_launchers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, package: str, bins: dict[str, str]
) -> None:
    monkeypatch.setattr(prune_foreign_tools.sys, "platform", "win32")
    _package(tmp_path / "node_modules", package, bins)
    launcher = _executable(tmp_path / "legacy.cmd")

    assert prune_foreign_tools.classify(launcher, "legacy", _layout(tmp_path)) == prune_foreign_tools.Removal(
        "npm", package, tmp_path, manual_only=True
    )


def test_windows_duplicate_prune_leaves_legacy_npm_guidance_to_setup(tmp_path, monkeypatch, caplog):
    """旧npm版にWindowsの「設定」からの削除を案内しない。"""
    _package(tmp_path / "node_modules", "@openai/codex", {"codex": "bin/codex.js"})
    launcher = _executable(tmp_path / "codex.cmd")
    monkeypatch.setattr(prune_foreign_tools.sys, "platform", "win32")
    monkeypatch.setattr(setup_mise_binary, "find_mise_binary", lambda: tmp_path / "mise.exe")
    monkeypatch.setattr(prune_foreign_tools, "_managed_names", lambda _mise: {"codex"})
    monkeypatch.setattr(prune_foreign_tools, "_foreign_executables", lambda *_args: [("codex", launcher, True)])
    monkeypatch.setattr(common, "run_subprocess", lambda *args, **kwargs: pytest.fail("削除経路を起動した"))

    assert not prune_foreign_tools.run().changed
    assert launcher.is_file()
    assert "「設定」の「アプリ」" not in caplog.text


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
    # Windowsでは、PATHでmise shimより前にある導入元不明の版を削除せず案内だけ出す。Linuxでは案内しない。
    shadow_notices = [
        record for record in caplog.records if "machine" in record.getMessage() and "先にPATH" in record.getMessage()
    ]
    assert bool(shadow_notices) == (sys.platform == "win32")


def test_run_keeps_standalone_pnpm_when_mise_version_is_unavailable(host: _Host) -> None:
    host.resolvable["value"] = False

    assert not prune_foreign_tools.run().changed
    assert (host.pnpm_home / f"pnpm{_EXE}").is_file()


def test_duplicate_prune_uninstalls_alias_install_by_its_key(tmp_path, monkeypatch):
    """別名導入でインストールキーとnameが違っても、照合した導入物だけをnpm uninstallへ渡す。

    nameが同じ別の導入物（旧npm版のcodexを含む）は削除対象にしない。
    """
    windows = sys.platform == "win32"
    prefix = tmp_path / "npm"
    modules = prefix / "node_modules" if windows else prefix / "lib" / "node_modules"
    for key, bins in (("some-tool-v1", {"tsc": "tsc.js"}), ("some-tool", {"codex": "codex.js"})):
        (modules / key).mkdir(parents=True)
        (modules / key / "package.json").write_text(json.dumps({"name": "some-tool", "bin": bins}), encoding="utf-8")
    bin_dir = prefix if windows else prefix / "bin"
    suffix = ".cmd" if windows else ""
    launchers = [
        _shim(bin_dir / f"{name}{suffix}", key, entry)
        for name, key, entry in (("tsc", "some-tool-v1", "tsc.js"), ("codex", "some-tool", "codex.js"))
    ]
    install = tmp_path / "mise" / "installs" / "typescript" / "5"
    _executable(install / f"tsc{_EXE}")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise"))
    monkeypatch.setenv("PNPM_HOME", str(tmp_path / "pnpm"))
    monkeypatch.setattr(setup_mise_binary, "find_mise_binary", lambda: tmp_path / "mise.exe")
    calls: list[list[str]] = []

    def run_subprocess(cmd: list[str], **_kwargs: typing.Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, f"{install}\n" if cmd[1:] == ["bin-paths"] else "", "")

    monkeypatch.setattr(common, "run_subprocess", run_subprocess)

    assert prune_foreign_tools.run().changed
    uninstalls = [cmd[cmd.index("uninstall") :] for cmd in calls if "uninstall" in cmd]
    assert uninstalls == [["uninstall", "--global", "--prefix", str(prefix), "some-tool-v1"]]
    assert all(launcher.is_file() for launcher in launchers)


@pytest.mark.parametrize("launcher", ["codex", "opencode"])
def test_run_keeps_standalone_pnpm_home_holding_legacy_npm_launcher(
    host: _Host, caplog: pytest.LogCaptureFixture, launcher: str
) -> None:
    """PNPM_HOMEに旧npm版の起動ファイルが同居する場合は、ディレクトリごとの削除をしない。

    旧版と同名の起動ファイルに加え、旧npm版のパッケージが持つ別名のbin（`opencode`）も対象にする。
    """
    _package(host.pnpm_home / "node_modules", "@openai/codex", {"codex": "bin/codex.js", "opencode": "bin/codex.js"})
    held = _executable(host.pnpm_home / f"{launcher}{'.cmd' if sys.platform == 'win32' else ''}")

    assert not prune_foreign_tools.run().changed
    assert held.is_file()
    assert (host.pnpm_home / f"pnpm{_EXE}").is_file()
    assert "削除せず保持" in caplog.text
    assert "`update-dotfiles`を再実行" in caplog.text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIXのnpmのglobal root（<prefix>/lib/node_modules）の規則を確かめる")
def test_duplicate_prune_checks_root_that_npm_uninstall_uses(tmp_path, monkeypatch):
    """`npm uninstall --prefix`が操作するrootと、削除の可否を確かめるrootを一致させる。

    POSIXの`<prefix>/bin/node_modules`を確かめて`--prefix <prefix>/bin`を渡すと、npmは
    `<prefix>/bin/lib/node_modules`の同じキー（旧npm版のbinを持つ）を削除する。
    """
    prefix = tmp_path / "prefix"
    bin_dir = prefix / "bin"
    compared = bin_dir / "node_modules" / "alias-tool"
    targeted = bin_dir / "lib" / "node_modules" / "alias-tool"
    for package, bins in ((compared, {"tsc": "tsc.js"}), (targeted, {"codex": "cli.js"})):
        package.mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"name": "different-name", "bin": bins}), encoding="utf-8")
    _shim(bin_dir / "tsc", "alias-tool", "tsc.js")
    install = tmp_path / "mise" / "installs" / "typescript" / "5"
    _executable(install / "tsc")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise"))
    monkeypatch.setenv("PNPM_HOME", str(tmp_path / "pnpm"))
    monkeypatch.setattr(setup_mise_binary, "find_mise_binary", lambda: tmp_path / "mise.exe")
    calls: list[list[str]] = []

    def run_subprocess(cmd: list[str], **_kwargs: typing.Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, f"{install}\n" if cmd[1:] == ["bin-paths"] else "", "")

    monkeypatch.setattr(common, "run_subprocess", run_subprocess)

    assert not prune_foreign_tools.run().changed
    assert not [cmd for cmd in calls if "uninstall" in cmd]


def test_run_keeps_standalone_pnpm_home_with_unconfirmed_entries(host: _Host, caplog: pytest.LogCaptureFixture) -> None:
    """PNPM_HOMEは、pnpm本体と付属物と確認できた項目だけを含む場合に限って削除する。

    配列形式のbinを持つ旧npm版のパッケージの起動ファイルのように、旧版の判定から漏れる項目があっても残す。
    """
    package = host.pnpm_home / "node_modules" / "@openai" / "codex"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": "@openai/codex", "bin": ["bin/opencode"]}), encoding="utf-8")
    _shim(host.pnpm_home / f"opencode{'.cmd' if sys.platform == 'win32' else ''}", "@openai/codex", "bin/opencode")

    assert not prune_foreign_tools.run().changed
    assert (host.pnpm_home / f"pnpm{_EXE}").is_file()
    assert "pnpm本体と付属物と確認できない項目" in caplog.text
    assert "`update-dotfiles`を再実行" in caplog.text


def test_run_keeps_unreadable_pnpm_home_with_retry_guidance(
    host: _Host, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """PNPM_HOMEの一覧を読めない場合は削除せず、存在しない項目の移動ではなく確認と再試行を案内する。"""
    original = Path.iterdir
    listed: list[Path] = []

    def list_dir(path: Path) -> typing.Iterator[Path]:
        # PATHの走査（1回目）では読め、PNPM_HOMEの削除前の確認（2回目以降）で読めない状態を作る。
        if path == host.pnpm_home:
            listed.append(path)
            if len(listed) > 1:
                raise PermissionError("fixture listing denied")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", list_dir)

    assert not prune_foreign_tools.run().changed
    assert (host.pnpm_home / f"pnpm{_EXE}").is_file()
    assert "内容を読めない" in caplog.text
    assert "列挙した項目" not in caplog.text
    assert "`update-dotfiles`を再実行" in caplog.text
