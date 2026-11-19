"""pytools._internal.setup_cli_commonのテスト。"""

import logging
import os
import shutil
import subprocess
import sys
import types
import typing
from pathlib import Path

import httpx
import psutil
import pytest

from pytools._internal import setup_cli_common
from pytools._internal._test_helpers import npm_windows_shims, patch_windows_processes, windows_process


class _GlobalNpm:
    """npmのグローバル導入先と、そこへnpmが導入した`@openai/codex`。

    `run`は`run_subprocess`の代わりにnpmの照会と`uninstall`へ答える。`uninstall`は実物のnpmと同じく、
    パッケージと、binの名前の起動スクリプト（Windowsはprefix直下の`codex`・`codex.cmd`・`codex.ps1`、
    POSIXは`bin/codex`）を、中身を確かめずに削除する。
    """

    def __init__(self, prefix: Path, *, windows: bool, uninstall_returncode: int = 0) -> None:
        self.windows = windows
        self.prefix = prefix
        self.root = prefix / "node_modules" if windows else prefix / "lib" / "node_modules"
        self.bin_dir = prefix if windows else prefix / "bin"
        self.package = self.root / "@openai" / "codex"
        self.npm = self.bin_dir / ("npm.cmd" if windows else "npm")
        self.launchers = [self.bin_dir / f"codex{suffix}" for suffix in (("", ".cmd", ".ps1") if windows else ("",))]
        self.uninstall_returncode = uninstall_returncode
        self.uninstalls: list[list[str]] = []
        entrypoint = self.package / "bin" / "codex.js"
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        (self.package / "package.json").write_text('{"name":"@openai/codex","bin":{"codex":"bin/codex.js"}}', encoding="utf-8")
        self.bin_dir.mkdir(exist_ok=True)
        self.npm.write_text("", encoding="utf-8")
        if windows:
            for suffix, content in npm_windows_shims("node_modules/@openai/codex/bin/codex.js").items():
                (self.bin_dir / f"codex{suffix}").write_text(content, encoding="utf-8", newline="")
        else:
            (self.bin_dir / "codex").symlink_to(Path("../lib/node_modules/@openai/codex/bin/codex.js"))

    def run(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1:] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{self.prefix}\n", "")
        if command[1:] == ["root", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{self.root}\n", "")
        assert command == [str(self.npm), "uninstall", "--global", "@openai/codex"], command
        self.uninstalls.append(command)
        if self.uninstall_returncode:
            return subprocess.CompletedProcess(command, self.uninstall_returncode, "", "npm ERR! EBUSY")
        shutil.rmtree(self.package)
        for launcher in self.launchers:
            launcher.unlink(missing_ok=True)
        return subprocess.CompletedProcess(command, 0, "", "")

    def removal(self) -> str:
        """警告が示す、旧npm版の削除コマンド。"""
        if self.windows:
            return f"PowerShellで`& '{self.npm}' uninstall --global @openai/codex`を実行"
        return f'`"{self.npm}" uninstall --global @openai/codex`を実行'


def _install_global_npm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    windows: bool,
    processes: list[object] | None = None,
    uninstall_returncode: int = 0,
) -> _GlobalNpm:
    """npmでグローバルに導入した旧版のCodexを、PATHで見つかる状態にする。Windowsでは`processes`が動いている。"""
    if windows:
        patch_windows_processes(monkeypatch, processes or [])
    else:
        monkeypatch.setattr(setup_cli_common.sys, "platform", "linux")
    npm = _GlobalNpm(tmp_path / "npm", windows=windows, uninstall_returncode=uninstall_returncode)
    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", npm.run)
    monkeypatch.setenv("PATH", str(npm.bin_dir))
    return npm


def _migrate(tmp_path: Path, *, windows: bool) -> bool:
    canonical = tmp_path / "dotfiles" / "bin" / ("codex.cmd" if windows else "codex")
    return setup_cli_common.migrate_npm_launchers("codex", "@openai/codex", canonical, tmp_path / "standalone")


@pytest.mark.parametrize("stderr", ["npm ERR! permission denied", ""])
def test_migrate_preserves_npm_failure_diagnostic(monkeypatch, tmp_path, caplog, stderr):
    """npmの導入先の照会に失敗した場合は移行を見送り、失敗した照会のコマンドとnpmのパスと診断を示す。

    空出力の失敗には終了コードを示す。
    """
    old = tmp_path / "old"
    old.mkdir()
    launcher = old / "codex"
    launcher.touch()
    (old / "npm").touch()
    (old / "npm.cmd").touch()
    monkeypatch.setenv("PATH", str(old))

    def fail(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", stderr)

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", fail)
    assert not setup_cli_common.migrate_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/bin/codex", tmp_path / "canonical"
    )
    assert launcher.is_file()
    output = "\n".join(caplog.messages)
    assert "旧npm版の移行を見送り" in output
    assert f'`"{old / "npm"}" root --global`: 終了コード1' in output
    assert (stderr or "exit 1") in output
    assert "npmを修復してから`update-dotfiles`を再実行する" in output


@pytest.mark.parametrize(
    ("platform", "explicit_ca", "uses_system_store"),
    [("win32", False, True), ("win32", True, False), ("linux", False, False)],
)
def test_official_installer_uses_platform_trust_and_explicit_ca(
    monkeypatch: pytest.MonkeyPatch, platform: str, explicit_ca: bool, uses_system_store: bool
) -> None:
    """CAの指定がないWindowsで使うOSの信頼済みCAと、環境変数で指定したCAを分け、取得後の実行経路を維持する。"""
    monkeypatch.setattr(setup_cli_common, "sys", types.SimpleNamespace(platform=platform))
    if explicit_ca:
        monkeypatch.setenv("SSL_CERT_FILE", "/example/custom-ca.pem")
    else:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    context = object()
    context_calls: list[bool] = []

    def default_context() -> object:
        context_calls.append(True)
        return context

    monkeypatch.setattr(setup_cli_common.ssl, "create_default_context", default_context)
    options: list[dict[str, object]] = []
    requested: list[str] = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_error: object) -> None:
            self.close()

        def __init__(self, **kwargs: object) -> None:
            options.append(kwargs)

        def get(self, url: str) -> httpx.Response:
            requested.append(url)
            return httpx.Response(200, content=b"installer", request=httpx.Request("GET", url))

        def close(self) -> None:
            pass

    monkeypatch.setattr(setup_cli_common.httpx, "Client", Client)
    monkeypatch.setattr(setup_cli_common.shutil, "which", lambda name: "/usr/bin/pwsh" if name == "pwsh" else None)
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", run)
    result, reason = setup_cli_common.run_official_installer(
        None,
        posix_url="https://example.test/install.sh",
        windows_url="https://example.test/install.ps1",
        tag="test",
        timeout=5.0,
    )

    assert result is not None and result.returncode == 0
    assert reason == ""
    assert options[0]["verify"] is (context if uses_system_store else True)
    assert len(context_calls) == int(uses_system_store)
    assert requested == ["https://example.test/install.ps1" if platform == "win32" else "https://example.test/install.sh"]
    assert commands[0][0] == ("/usr/bin/pwsh" if platform == "win32" else "bash")


def test_find_powershell_uses_windows_powershell_when_pwsh_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        setup_cli_common.shutil, "which", lambda name: "C:/Windows/powershell.exe" if name == "powershell" else None
    )

    assert setup_cli_common.find_powershell() == "C:/Windows/powershell.exe"


def test_official_installer_decodes_windows_powershell_with_local_code_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PowerShell 5.1の日本語診断だけロケールのコードページで受け取る。"""
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    monkeypatch.setattr(setup_cli_common, "find_powershell", lambda: "C:/Windows/powershell.exe")
    monkeypatch.setattr(setup_cli_common.locale, "getpreferredencoding", lambda _setlocale: "cp932")
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"Write-Host", request=request)

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[0] == "C:/Windows/powershell.exe"
        observed.append(str(kwargs["encoding"]))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", run)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, reason = setup_cli_common.run_official_installer(
            client,
            posix_url="https://example.test/install.sh",
            windows_url="https://example.test/install.ps1",
            tag="test",
            timeout=5.0,
        )
    assert result is not None and result.returncode == 0
    assert reason == ""
    assert observed == ["cp932"]


def test_find_powershell_reports_missing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_cli_common.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="PowerShellが見つからない"):
        setup_cli_common.find_powershell()


def test_prepend_path_moves_existing_entry_to_front(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "first"
    canonical = tmp_path / "canonical"
    monkeypatch.setenv("PATH", os.pathsep.join([str(first), str(canonical), str(first)]))

    setup_cli_common.prepend_path(canonical)

    assert os.environ["PATH"].split(os.pathsep) == [str(canonical), str(first), str(first)]


@pytest.mark.parametrize("uninstall_returncode", [0, 1])
def test_posix_migrate_removes_npm_version_linked_by_npm(monkeypatch, tmp_path: Path, uninstall_returncode: int) -> None:
    """POSIXのnpmが`bin/codex`に作るシンボリックリンクから旧npm版を削除し、削除に失敗した場合は旧版を残して失敗を返す。"""
    npm = _install_global_npm(monkeypatch, tmp_path, windows=False, uninstall_returncode=uninstall_returncode)
    canonical = tmp_path / "standalone" / "bin" / "codex"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", os.pathsep.join([str(canonical.parent), str(npm.bin_dir)]))

    if uninstall_returncode:
        with pytest.raises(RuntimeError):
            _migrate(tmp_path, windows=False)
    else:
        assert _migrate(tmp_path, windows=False)
    assert len(npm.uninstalls) == 1
    assert npm.package.is_dir() is bool(uninstall_returncode)
    assert (npm.bin_dir / "codex").is_symlink() is bool(uninstall_returncode)


def test_migrate_npm_launchers_searches_extra_directories_once(monkeypatch, tmp_path: Path) -> None:
    """PATH外の明示した配置先を走査し、重複指定しても同じnpm版を一度だけ除去する。"""
    canonical_prefix = tmp_path / "canonical"
    canonical = canonical_prefix / "bin" / "codex"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("", encoding="utf-8")
    nvm_bin = tmp_path / ".nvm" / "versions" / "node" / "v24" / "bin"
    package_root = nvm_bin.parent / "lib" / "node_modules"
    package = package_root / "@openai" / "codex"
    entrypoint = package / "bin" / "codex.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("", encoding="utf-8")
    (package / "package.json").write_text('{"bin":{"codex":"bin/codex.js"}}', encoding="utf-8")
    nvm_bin.mkdir(exist_ok=True)
    (nvm_bin / "codex").symlink_to(entrypoint)
    npm = nvm_bin / "npm"
    npm.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(canonical.parent))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[1:3] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{nvm_bin.parent}\n", "")
        if command[1:3] == ["root", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{package_root}\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", fake_run)

    assert setup_cli_common.migrate_npm_launchers(
        "codex",
        "@openai/codex",
        canonical,
        canonical_prefix,
        extra_search_directories=(nvm_bin, nvm_bin),
    )
    assert sum(command[1:3] == ["uninstall", "--global"] for command in calls) == 1


def test_migrate_npm_launchers_keeps_unknown_launcher(monkeypatch, tmp_path: Path) -> None:
    canonical_prefix = tmp_path / "canonical"
    canonical = canonical_prefix / "bin" / "codex"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("", encoding="utf-8")
    old_bin = tmp_path / "old"
    old_bin.mkdir()
    (old_bin / "codex").write_text("", encoding="utf-8")
    (old_bin / "npm").write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(old_bin))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[1] == "prefix":
            return subprocess.CompletedProcess(command, 0, f"{tmp_path / 'prefix'}\n", "")
        root = tmp_path / "prefix" / "lib" / "node_modules"
        (root / "@openai" / "codex").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(command, 0, f"{root}\n", "")

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", fake_run)

    assert not setup_cli_common.migrate_npm_launchers("codex", "@openai/codex", canonical, canonical_prefix)
    assert not any("uninstall" in call for call in calls)


def test_migrate_excludes_canonical_prefix_and_launcher(monkeypatch, tmp_path: Path) -> None:
    prefix = tmp_path / "node"
    launcher = prefix / "bin" / "codex"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    (launcher.parent / "alias" / "codex").parent.mkdir()
    (launcher.parent / "alias" / "codex").symlink_to(launcher)
    monkeypatch.setenv("PATH", os.pathsep.join([str(launcher.parent), str(launcher.parent / "alias")]))
    monkeypatch.setattr(
        setup_cli_common.common,
        "run_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError((args, kwargs))),
    )

    assert not setup_cli_common.migrate_npm_launchers("codex", "@openai/codex", launcher, prefix)


@pytest.mark.parametrize("shim_location", ["data-dir", "default"])
def test_migrate_skips_mise_shim_directory(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, shim_location: str
) -> None:
    """miseのshimは帰属判定へ掛けず、shim以外の非正規ランチャーは従来どおり対象にする。"""
    home = tmp_path / "home"
    if shim_location == "data-dir":
        data_dir = tmp_path / "mise-data"
        shims = data_dir / "shims"
        monkeypatch.setenv("MISE_DATA_DIR", str(data_dir))
    else:
        shims = home / ".local" / "share" / "mise" / "shims"
        monkeypatch.delenv("MISE_DATA_DIR", raising=False)
    shims.mkdir(parents=True)
    (shims / "codex").write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    other = tmp_path / "other"
    other.mkdir()
    (other / "codex").write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", os.pathsep.join([str(shims), str(other)]))

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        assert not setup_cli_common.migrate_npm_launchers(
            "codex", "@openai/codex", tmp_path / "canonical" / "bin" / "codex", tmp_path / "canonical"
        )

    assert not any(str(shims / "codex") in message for message in caplog.messages)
    assert any(str(other / "codex") in message for message in caplog.messages)


@pytest.mark.parametrize("scenario", ["package-missing", "not-symlink"])
def test_migrate_reports_why_package_ownership_is_unconfirmed(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, scenario: str
) -> None:
    """帰属を確認できず保持する場合は、確認が成立しなかった理由を条件ごとに示す。"""
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise-data"))
    old_bin = tmp_path / "old"
    old_bin.mkdir()
    launcher = old_bin / "codex"
    launcher.write_text("", encoding="utf-8")
    (old_bin / "npm").write_text("", encoding="utf-8")
    root = tmp_path / "prefix" / "lib" / "node_modules"
    root.mkdir(parents=True)
    package_dir = root.resolve() / "@openai" / "codex"
    if scenario == "not-symlink":
        package_dir.mkdir(parents=True)
        (package_dir / "package.json").write_text('{"bin":{"codex":"bin/codex.js"}}', encoding="utf-8")
    monkeypatch.setenv("PATH", str(old_bin))

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        output = tmp_path / "prefix" if command[1] == "prefix" else root
        return subprocess.CompletedProcess(command, 0, f"{output}\n", "")

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", fake_run)

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        assert not setup_cli_common.migrate_npm_launchers(
            "codex", "@openai/codex", tmp_path / "canonical" / "bin" / "codex", tmp_path / "canonical"
        )

    messages = [message for message in caplog.messages if str(launcher) in message]
    assert len(messages) == 1
    expected = (
        f"packageのディレクトリが不在: {package_dir}"
        if scenario == "package-missing"
        else f"{package_dir}配下の実体ではない: 通常ファイル"
    )
    assert expected in messages[0]


def test_migrate_keeps_launcher_without_adjacent_npm(monkeypatch, tmp_path: Path) -> None:
    launcher = tmp_path / "old" / "codex"
    launcher.parent.mkdir()
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(launcher.parent))

    assert not setup_cli_common.migrate_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/bin/codex", tmp_path / "canonical"
    )


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_migrate_excludes_callers_own_wrappers_from_retention_warning(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, platform: str
) -> None:
    """PATH上のdotfiles自身の起動管理は保持警告を出さず、帰属不明の旧版だけを警告する。"""
    monkeypatch.setattr(setup_cli_common.sys, "platform", platform)
    wrappers = tmp_path / "dotfiles" / "bin"
    wrappers.mkdir(parents=True)
    for name in ("codex", "codex.cmd"):
        (wrappers / name).write_text("wrapper\n", encoding="utf-8")
    legacy = tmp_path / "legacy" / "codex"
    legacy.parent.mkdir()
    legacy.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", os.pathsep.join([str(wrappers), str(legacy.parent)]))

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        assert not setup_cli_common.migrate_npm_launchers(
            "codex",
            "@openai/codex",
            tmp_path / "canonical/bin/codex",
            tmp_path / "canonical",
            excluded_launchers={wrappers / "codex", wrappers / "codex.cmd"},
        )

    assert not any(str(wrappers) in message for message in caplog.messages)
    assert any(str(legacy) in message for message in caplog.messages)


def test_migrate_windows_mise_shim_resolving_to_npm_package(monkeypatch, tmp_path: Path) -> None:
    """npmの起動スクリプトの外にある`codex.exe`は、miseの解決先がnpm版のパッケージ配下の場合に移行の根拠とする。"""
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    old_bin = tmp_path / "old"
    old_bin.mkdir()
    npm = old_bin / "npm.cmd"
    npm.write_text("", encoding="utf-8")
    root = tmp_path / "prefix" / "node_modules"
    package = root / "@openai" / "codex"
    package.mkdir(parents=True)
    (package / "package.json").write_text('{"bin":{"codex":"bin/codex.js"}}', encoding="utf-8")
    (old_bin / "codex.exe").write_bytes(b"shim")
    monkeypatch.setattr(setup_cli_common.shutil, "which", lambda name: "C:/mise.exe" if name == "mise" else None)
    monkeypatch.setenv("PATH", str(old_bin))

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1:3] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{tmp_path / 'prefix'}\n", "")
        if command[1:3] == ["root", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{root}\n", "")
        if command[1:3] == ["which", "codex"]:
            shim_target = package / "npm-openai-codex" / "bin" / "codex.exe"
            shim_target.parent.mkdir(parents=True)
            shim_target.write_bytes(b"codex")
            return subprocess.CompletedProcess(command, 0, f"{shim_target}\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", fake_run)

    assert setup_cli_common.migrate_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
    )


@pytest.mark.parametrize(
    ("running", "removed"),
    [
        pytest.param("npm-node", False, id="npm-node"),
        # 起動したスクリプトを読めないnodeは、削除するnpm版を実行している可能性を除けない。
        pytest.param("node-cmdline-denied", False, id="node-cmdline-denied"),
        pytest.param("npx-node", True, id="npx-node"),
        pytest.param("update-loop", True, id="update-loop"),
    ],
)
def test_windows_migrate_keeps_npm_package_while_it_may_be_running(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, running: str, removed: bool
) -> None:
    """Windowsでは、削除するnpm版から起動したプロセスか、その可能性を否定できないプロセスがある間は削除しない。"""
    entrypoint = tmp_path / "npm" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    process = {
        "npm-node": windows_process(_NODE, str(entrypoint)),
        "node-cmdline-denied": windows_process(_NODE, r"C:\Services\worker.js", denied=["cmdline"]),
        "npx-node": windows_process(_NODE, rf"{_NPX_CODEX}\bin\codex.js"),
        "update-loop": windows_process(_DAEMON_EXE, "app-server", "daemon", "pid-update-loop"),
    }[running]
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True, processes=[process])

    assert _migrate(tmp_path, windows=True) is removed
    assert len(npm.uninstalls) == int(removed)
    assert npm.package.is_dir() is not removed
    assert (f"旧npm版の移行を次回へ延期: {npm.package.resolve()}" in caplog.text) is not removed


@pytest.mark.parametrize(
    ("scenario", "uninstall_returncode", "warnings"),
    [
        # 無関係なnodeの起動引数を読めず、旧npm版の実行を否定できない。
        ("deferred", 0, ["旧npm版の移行を次回へ延期"]),
        ("removed", 0, []),
        ("removal-failed", 1, ["旧npm版の削除に失敗"]),
    ],
)
def test_windows_migrates_three_npm_shims_as_one_package(
    monkeypatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    scenario: str,
    uninstall_returncode: int,
    warnings: list[str],
) -> None:
    """Windowsのnpmが1つのbinに作る3本の起動スクリプトは、同じパッケージとして1回だけ判定して表示する。

    延期中に「保持したため対応不要」と「削除する」の矛盾する警告を並べない。
    削除コマンドは、推奨するPowerShell 7でそのまま実行できる形で示す。
    """
    unrelated_node = windows_process(_NODE, r"C:\Services\worker.js", denied=["cmdline"])
    npm = _install_global_npm(
        monkeypatch,
        tmp_path,
        windows=True,
        processes=[unrelated_node] if scenario == "deferred" else [],
        uninstall_returncode=uninstall_returncode,
    )

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        try:
            _migrate(tmp_path, windows=True)
        except RuntimeError as error:
            assert scenario == "removal-failed", error

    removed = scenario == "removed"
    assert len(npm.uninstalls) == int(scenario != "deferred")
    assert [launcher.exists() for launcher in npm.launchers] == [not removed] * 3
    assert npm.package.is_dir() is not removed
    assert [[warning for warning in warnings if warning in message] for message in caplog.messages] == [
        [warning] for warning in warnings
    ]
    assert all(npm.removal() in message for message in caplog.messages)


_NPM_SHIMS = npm_windows_shims("node_modules/@openai/codex/bin/codex.js")
_USER_FILES: dict[str, tuple[bool, str, str | bytes]] = {
    # ユーザーが正規のCodexを`--profile work`で起動するよう置いたラッパー。
    "windows-wrapper-cmd": (True, "codex.cmd", '@echo off\r\n"%USERPROFILE%\\.local\\bin\\codex.exe" --profile work %*\r\n'),
    # npmが生成した起動スクリプトの起動行に、ユーザーが起動オプションを足したもの。
    "windows-edited-cmd": (True, "codex.cmd", _NPM_SHIMS[".cmd"].replace('codex.js" %*', 'codex.js" --profile work %*')),
    "windows-edited-sh-one-line": (
        True,
        "codex",
        _NPM_SHIMS[""].replace(
            'exec node  "$basedir/node_modules/@openai/codex/bin/codex.js" "$@"',
            'exec node  "$basedir/node_modules/@openai/codex/bin/codex.js" --profile work "$@"',
        ),
    ),
    "windows-user-ps1": (True, "codex.ps1", "& codex.exe --profile work @args\n"),
    # 別のパッケージを起動するnpmの起動スクリプト。
    "windows-foreign-sh": (True, "codex", npm_windows_shims("node_modules/@other/cli/bin/cli.js")[""]),
    "windows-small-binary": (True, "codex", b"MZ\x90\x00" + bytes(60)),
    # npmのシンボリックリンクを、同じ実体を起動オプション付きで起動するスクリプトへ置き換えたもの。
    "posix-script": (False, "codex", '#!/bin/sh\nexec node "<package>/bin/codex.js" --profile work "$@"\n'),
    "posix-small-binary": (False, "codex", b"\x7fELF\x02\x01\x01\x00" + bytes(56)),
}


@pytest.mark.parametrize("scenario", list(_USER_FILES))
def test_migrate_keeps_user_files_that_npm_uninstall_would_remove(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, scenario: str
) -> None:
    """npmの`uninstall`がパッケージと一緒に消す同名のファイルにユーザーのファイルがあれば、旧npm版を自動では削除しない。

    そのファイルと、旧npm版を削除する手順を、パッケージごとに1つの警告で示す。
    """
    windows, name, content = _USER_FILES[scenario]
    npm = _install_global_npm(monkeypatch, tmp_path, windows=windows)
    user_file = npm.bin_dir / name
    user_file.unlink()
    data = content if isinstance(content, bytes) else content.replace("<package>", str(npm.package)).encode()
    user_file.write_bytes(data)

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        changed = _migrate(tmp_path, windows=windows)

    assert (user_file.read_bytes() if user_file.exists() else None) == data
    assert not npm.uninstalls
    assert not changed
    assert len(caplog.messages) == 1, caplog.messages
    assert f"{user_file.resolve()}（" in caplog.messages[0]
    assert npm.removal() in caplog.messages[0]


def test_migrate_windows_cmd_rejects_package_name_fragments(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    old_bin = tmp_path / "old"
    old_bin.mkdir()
    (old_bin / "npm.cmd").write_text("", encoding="utf-8")
    (old_bin / "codex.cmd").write_text("@echo @openai/codex\n", encoding="utf-8")
    root = tmp_path / "prefix" / "node_modules"
    (root / "@openai" / "codex").mkdir(parents=True)
    monkeypatch.setenv("PATH", str(old_bin))

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        value = tmp_path / "prefix" if command[1] == "prefix" else root
        return subprocess.CompletedProcess(command, 0, f"{value}\n", "")

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", fake_run)

    assert not setup_cli_common.migrate_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
    )


@pytest.mark.parametrize(
    ("bin_json", "referenced_name", "expected"),
    [
        ('"bin/codex.js"', "bin/codex.js", True),
        ('{"codex":"bin/codex.js"}', "bin/codex.js", True),
        ('{"other":"bin/codex.js"}', "bin/codex.js", False),
        ('{"codex":"bin/codex.js"}', "README.md", False),
        ('{"codex":"../outside.js"}', "../outside.js", False),
    ],
)
def test_migrate_windows_cmd_uses_package_json_bin(
    monkeypatch, tmp_path: Path, bin_json: str, referenced_name: str, expected: bool
) -> None:
    """npmの起動スクリプトは、package.jsonのbinの名前と実体に合う参照だけを生成物とする。"""
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    prefix = tmp_path / "prefix"
    (prefix / "npm.cmd").parent.mkdir()
    (prefix / "npm.cmd").write_text("", encoding="utf-8")
    root = prefix / "node_modules"
    package = root / "@openai" / "codex"
    package.mkdir(parents=True)
    entrypoint = package / "bin" / "codex.js"
    entrypoint.parent.mkdir()
    entrypoint.write_text("", encoding="utf-8")
    (package / "README.md").write_text("documentation", encoding="utf-8")
    outside = package.parent / "outside.js"
    outside.write_text("", encoding="utf-8")
    (package / "package.json").write_text(f'{{"name":"@openai/codex","bin":{bin_json}}}', encoding="utf-8")
    shim = npm_windows_shims(f"node_modules/@openai/codex/{referenced_name}")[".cmd"]
    (prefix / "codex.cmd").write_text(shim, encoding="utf-8", newline="")
    monkeypatch.setenv("PATH", str(prefix))

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        output = {"prefix": prefix, "root": root}.get(command[1], "")
        return subprocess.CompletedProcess(command, 0, f"{output}\n", "")

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", fake_run)

    assert (
        setup_cli_common.migrate_npm_launchers(
            "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
        )
        is expected
    )


@pytest.mark.parametrize("mise_target", ["other_package", "outside"])
def test_migrate_windows_mise_shim_rejects_wrong_target(monkeypatch, tmp_path: Path, mise_target: str) -> None:
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    old_bin = tmp_path / "old"
    old_bin.mkdir()
    (old_bin / "npm.cmd").write_text("", encoding="utf-8")
    (old_bin / "codex.exe").write_bytes(b"shim")
    root = tmp_path / "prefix" / "node_modules"
    package = root / "@openai" / "codex"
    package.mkdir(parents=True)
    target = (
        root / "@openai" / "other-package" / "npm-openai-codex.exe"
        if mise_target == "other_package"
        else tmp_path / "outside" / "npm-openai-codex" / "codex.exe"
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(b"target")
    monkeypatch.setenv("PATH", str(old_bin))
    monkeypatch.setattr(setup_cli_common.shutil, "which", lambda name: "C:/mise.exe")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1] == "prefix":
            output = tmp_path / "prefix"
        elif command[1] == "root":
            output = root
        else:
            output = target
        return subprocess.CompletedProcess(command, 0, f"{output}\n", "")

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", fake_run)

    assert not setup_cli_common.migrate_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
    )


_STANDALONE = r"C:\Users\Me\.codex\packages\standalone"
_STANDALONE_RELEASE = rf"{_STANDALONE}\releases\0.170.0-x86_64-pc-windows-msvc"
_DAEMON_EXE = r"C:\Users\Me\.codex\packages\app-server-daemon\releases\0.170.0-x86_64-pc-windows-msvc\bin\codex.exe"
_NPM_CODEX = r"C:\Users\Me\AppData\Roaming\npm\node_modules\@openai\codex"
_NPX_CODEX = r"C:\Users\Me\AppData\Local\npm-cache\_npx\9f2c\node_modules\@openai\codex"
_NATIVE_IN_PACKAGE = r"node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\codex\codex.exe"
_NODE = r"C:\Program Files\nodejs\node.exe"
_MISE = r"C:\Users\Me\AppData\Local\Microsoft\WinGet\Links\mise.exe"
_CODEX_FILES: dict[str, tuple[Path, ...]] = {"native": (Path(_STANDALONE),), "packages": (Path(_NPM_CODEX),)}


@pytest.mark.parametrize(
    ("process", "expected"),
    [
        # 導入が置き換えるスタンドアローン版から起動した会話と、会話が起動した同梱の実行体。
        pytest.param(windows_process(rf"{_STANDALONE_RELEASE}\bin\codex.exe"), True, id="standalone-session"),
        pytest.param(windows_process(rf"{_STANDALONE_RELEASE}\codex-path\rg.exe"), True, id="standalone-helper"),
        # 実行ファイルだけを読めない場合は、command lineの先頭の実行パスで所在を確かめる。
        pytest.param(
            windows_process(rf"{_STANDALONE_RELEASE}\codex-path\rg.exe", denied=["exe"]),
            True,
            id="standalone-helper-exe-denied",
        ),
        pytest.param(
            windows_process(rf"\\?\{_STANDALONE_RELEASE}\codex-path\rg.exe", denied=["exe"]),
            True,
            id="standalone-helper-extended-path",
        ),
        # 2026-11-19のsthenoで動いていた管理daemonと更新ループ。
        pytest.param(windows_process(_DAEMON_EXE, "app-server", "daemon", "pid-update-loop"), False, id="update-loop"),
        pytest.param(
            windows_process(_DAEMON_EXE, "app-server", "--managed-daemon", denied=["cmdline"]),
            False,
            id="daemon-cmdline-denied",
        ),
        pytest.param(
            windows_process(
                r"C:\Program Files\WindowsApps\OpenAI.Codex_26.1106.6628.0_x64__2p2nqsd0c76g0\app\resources\codex.exe"
            ),
            False,
            id="desktop-app",
        ),
        pytest.param(windows_process("C:/node/codex.exe"), False, id="other-codex"),
        pytest.param(windows_process(r"C:\tools\codex.exe", denied=["exe"]), True, id="codex-exe-denied"),
        # 移行が削除するnpm版と、移行が削除しないnpxのキャッシュ。
        pytest.param(windows_process(rf"{_NPM_CODEX}\{_NATIVE_IN_PACKAGE}"), True, id="npm-native"),
        pytest.param(windows_process(_NODE, rf"{_NPM_CODEX}\bin\codex.js"), True, id="npm-node"),
        pytest.param(windows_process(_NODE, rf"{_NPM_CODEX}\bin\codex.js", denied=["exe"]), True, id="npm-node-exe-denied"),
        pytest.param(
            windows_process(_MISE, "exec", "--", "node", rf"{_NPM_CODEX}\bin\codex.js", denied=["exe"]),
            True,
            id="npm-mise-exe-denied",
        ),
        pytest.param(windows_process(_NODE, denied=["cmdline"]), True, id="node-cmdline-denied"),
        pytest.param(windows_process(_MISE, denied=["exe", "cmdline"]), True, id="mise-unreadable"),
        pytest.param(windows_process(rf"{_NPX_CODEX}\{_NATIVE_IN_PACKAGE}"), False, id="npx-native"),
        pytest.param(windows_process(_NODE, rf"{_NPX_CODEX}\bin\codex.js"), False, id="npx-node"),
        # node・miseの引数のうち、起動するスクリプトの位置だけを見る。
        pytest.param(windows_process(_MISE, "exec", "codex"), False, id="mise-without-separator"),
        pytest.param(windows_process(_MISE, "exec", "--", "codex"), True, id="mise-launcher-name"),
        pytest.param(windows_process(_MISE, "exec", "--", "server", "codex"), False, id="mise-other-command"),
        pytest.param(windows_process(_NODE, "--no-warnings", f"{_NPM_CODEX}/bin/codex.js"), True, id="node-option"),
        pytest.param(windows_process(_NODE, "--require", rf"{_NPM_CODEX}\bin\codex.js", "server.js"), False, id="require"),
        pytest.param(windows_process(_NODE, "--title", "codex", "server.js"), False, id="title"),
        pytest.param(
            windows_process(_MISE, "x", "--", "node", "--no-warnings", rf"{_NPM_CODEX}\bin\codex.js"),
            True,
            id="mise-node-option",
        ),
        pytest.param(windows_process(_NODE, "--eval", "codex", rf"{_NPM_CODEX}\bin\codex.js"), False, id="eval"),
        pytest.param(windows_process(_NODE, "--", rf"{_NPM_CODEX}\bin\codex.js", "prompt"), True, id="separator"),
        pytest.param(windows_process(_NODE, "C:/bin/codex.cmd"), False, id="launcher-outside-package"),
        pytest.param(windows_process(_NODE, "bin/codex.js"), True, id="relative-launcher"),
        pytest.param(windows_process(r"C:\python\python.exe", "codex"), False, id="python"),
        pytest.param(windows_process(_NODE, "C:/work/codex-project/server.js"), False, id="project-server"),
        pytest.param(windows_process(_NODE, "server.js", "--prompt=codex"), False, id="prompt-option"),
        pytest.param(windows_process(_NODE, "server.js", rf"{_NPM_CODEX}\bin\codex.js"), False, id="later-argument"),
    ],
)
def test_windows_counts_processes_started_from_files_to_replace_or_remove(
    monkeypatch, process: types.SimpleNamespace, expected: bool
) -> None:
    """導入が置き換えるか移行が削除する配置から起動したプロセスと、その可能性を否定できないプロセスだけを数える。"""
    patch_windows_processes(monkeypatch, [process])

    assert bool(setup_cli_common.find_windows_cli_processes("codex", **_CODEX_FILES)) is expected


@pytest.mark.parametrize(
    "process",
    [
        windows_process(_NODE, denied=["cmdline"]),
        windows_process(_MISE, "exec", "--", "codex"),
        windows_process(_NODE, rf"{_NPX_CODEX}\bin\codex.js"),
    ],
)
def test_windows_node_is_not_counted_without_npm_or_mise_version_to_remove(monkeypatch, process: types.SimpleNamespace) -> None:
    """導入が置き換える配置だけを調べる場合、起動したスクリプトを読めないものも含め、node・miseのプロセスは数えない。"""
    patch_windows_processes(monkeypatch, [process])

    assert not setup_cli_common.find_windows_cli_processes("codex", native=(Path(_STANDALONE),))


@pytest.mark.parametrize(
    ("exe", "expected"),
    [
        # 更新が同じパスで置き換える導入先。Windowsのパスは大文字小文字と区切りの表記が揺れる。
        (r"c:\users\me\.local\bin\CLAUDE.EXE", True),
        (r"C:\Program Files\WindowsApps\Claude_5.11492.5.0_x64__pzs8sxrjxfjjc\app\claude.exe", False),
        (r"C:\Users\Me\AppData\Roaming\Claude\claude-code\2.1.325\806e0a45e6f5\claude.exe", False),
    ],
)
def test_windows_claude_counts_only_replaced_launcher(monkeypatch, exe: str, expected: bool) -> None:
    """Claude Codeは、更新が置き換える導入先から起動した場合だけ数え、デスクトップアプリと同梱版は数えない。"""
    patch_windows_processes(monkeypatch, [windows_process(exe)])

    native = (Path(r"C:\Users\Me\.local\bin\claude.exe"),)
    assert bool(setup_cli_common.find_windows_cli_processes("claude", native=native)) is expected


def test_windows_check_is_skipped_on_other_platforms(monkeypatch) -> None:
    """Windows以外では実行中のファイルを置き換えても衝突しないため、書き換える配置から起動したプロセスも数えない。"""
    patch_windows_processes(monkeypatch, [windows_process(rf"{_STANDALONE_RELEASE}\bin\codex.exe")])
    monkeypatch.setattr(setup_cli_common.sys, "platform", "linux")

    assert not setup_cli_common.find_windows_cli_processes("codex", **_CODEX_FILES)


@pytest.mark.parametrize("error", [psutil.NoSuchProcess(1), psutil.ZombieProcess(2)])
def test_windows_process_races_are_ignored(monkeypatch, error: Exception) -> None:
    class VanishedProcess:
        def name(self) -> str:
            raise error

    patch_windows_processes(monkeypatch, [VanishedProcess()])

    assert not setup_cli_common.find_windows_cli_processes("codex", **_CODEX_FILES)


def _inject_previous_generation(monkeypatch: pytest.MonkeyPatch, pid: int, *, name: str, exe: str) -> None:
    """列挙が再利用するProcessだけを以前の世代の実行名・実行ファイルにし、走査を対象のPIDへ限る。

    PIDの自然な再利用や実行ファイルの置き換えを待たずに、列挙キャッシュへ以前の値が残った状態を作る。
    """
    stale = psutil.Process(pid)
    monkeypatch.setattr(stale, "_name", name)
    monkeypatch.setattr(stale, "_exe", exe)
    monkeypatch.setitem(vars(psutil)["_pmap"], pid, stale)
    real_process_iter = psutil.process_iter

    def own_process(*args: typing.Any, **kwargs: typing.Any) -> list[psutil.Process]:
        return [item for item in real_process_iter(*args, **kwargs) if item.pid == pid]

    monkeypatch.setattr(psutil, "process_iter", own_process)


@pytest.mark.skipif(
    os.name == "nt", reason="仮想環境のpython.exeは本体を子として起動するため、起動したPIDだけに走査を限定できない"
)
def test_windows_cli_running_reads_current_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """列挙キャッシュに以前の世代のCLIの実行名と実行ファイルが残っていても、現在の属性で実行中かを判定する。"""
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) as child:
        try:
            _inject_previous_generation(monkeypatch, child.pid, name="codex.exe", exe=rf"{_STANDALONE_RELEASE}\bin\codex.exe")
            monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
            running = setup_cli_common.find_windows_cli_processes("codex", **_CODEX_FILES)
        finally:
            child.kill()
    assert not running


def test_migrate_removes_npm_launchers_left_without_package(monkeypatch, tmp_path):
    """本体の無いnpm起動スクリプトだけを削除し、本体のあるものと、ユーザーが置いたものは残す。"""
    shims = npm_windows_shims("node_modules/@openai/codex/bin/codex.js")
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    for suffix, content in shims.items():
        (orphan / f"codex{suffix}").write_text(content, encoding="utf-8", newline="")
    installed = tmp_path / "installed"
    (installed / "node_modules" / "@openai" / "codex").mkdir(parents=True)
    (installed / "codex.ps1").write_text(shims[".ps1"], encoding="utf-8", newline="")
    user_files = {
        "unrelated": ("codex.ps1", b"& codex.exe @args\n"),
        # 本体の無いnpm版を起動行にユーザーが起動オプションを足して使っていたもの。
        "edited": ("codex.cmd", shims[".cmd"].replace('codex.js" %*', 'codex.js" --profile work %*').encode()),
        # npmの起動スクリプトの参照を含むが、テキストとして読めない実行ファイル。
        "binary": ("codex", shims[""].encode() + b"\0"),
    }
    for directory, (name, data) in user_files.items():
        (tmp_path / directory).mkdir()
        (tmp_path / directory / name).write_bytes(data)
    monkeypatch.setenv("PATH", os.pathsep.join(str(tmp_path / name) for name in ("orphan", "installed", *user_files)))
    canonical = tmp_path / "canonical"

    assert setup_cli_common.migrate_npm_launchers("codex", "@openai/codex", canonical / "codex.cmd", canonical)

    assert not any(orphan.iterdir())
    assert (installed / "codex.ps1").is_file()
    remaining = {directory: tmp_path / directory / name for directory, (name, _) in user_files.items()}
    assert {directory: path.read_bytes() if path.exists() else None for directory, path in remaining.items()} == {
        directory: data for directory, (_, data) in user_files.items()
    }


def test_migrate_keeps_large_file_named_like_launcher(monkeypatch, tmp_path):
    """起動スクリプトより大きいファイル（実行ファイル本体など）は、参照の文字列を含んでも読まず削除しない。"""
    directory = tmp_path / "bin"
    directory.mkdir()
    binary = directory / "codex"
    binary.write_bytes(b'"$basedir/node_modules/@openai/codex/bin/codex.js"\n' + b"\x00" * (64 * 1024))
    monkeypatch.setenv("PATH", str(directory))
    canonical = tmp_path / "canonical"

    setup_cli_common.migrate_npm_launchers("codex", "@openai/codex", canonical / "codex.cmd", canonical)

    assert binary.is_file()
