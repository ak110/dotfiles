"""pytools._internal.setup_cli_commonのnpm版の移行（`migrate_npm_launchers`）のテスト。"""

import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pytools._internal import setup_cli_common
from pytools._internal._test_helpers import npm_cmd_shim_kinds, npm_windows_shims, patch_windows_processes, windows_process

_DAEMON_EXE = r"C:\Users\Me\.codex\packages\app-server-daemon\releases\0.170.0-x86_64-pc-windows-msvc\bin\codex.exe"
_NPX_CODEX = r"C:\Users\Me\AppData\Local\npm-cache\_npx\9f2c\node_modules\@openai\codex"
_NODE = r"C:\Program Files\nodejs\node.exe"


class _GlobalNpm:
    """npmのグローバル導入先と、そこへnpmが導入した`@openai/codex`。

    `run`は`run_subprocess`の代わりにnpmの照会と`uninstall`へ答える。`uninstall`は実物のnpmと同じく、
    パッケージと、binの名前の起動スクリプト（Windowsはprefix直下の`codex`・`codex.cmd`・`codex.ps1`、
    POSIXは`bin/codex`）を、中身を確かめずに削除する。
    """

    def __init__(
        self,
        prefix: Path,
        *,
        windows: bool,
        uninstall_returncode: int = 0,
        cmd_shim: tuple[str, str] = ("8.0.0", "node"),
    ) -> None:
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
            version, program = cmd_shim
            shims = npm_windows_shims("node_modules/@openai/codex/bin/codex.js", version=version, program=program)
            for suffix, content in shims.items():
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
    cmd_shim: tuple[str, str] = ("8.0.0", "node"),
) -> _GlobalNpm:
    """npmでグローバルに導入した旧版のCodexを、PATHで見つかる状態にする。

    Windowsでは`processes`が動いていて、起動スクリプトは`cmd_shim`（cmd-shimの版と、binの実体の先頭行の種類）が生成した形である。
    """
    if windows:
        patch_windows_processes(monkeypatch, processes or [])
    else:
        monkeypatch.setattr(setup_cli_common.sys, "platform", "linux")
    npm = _GlobalNpm(tmp_path / "npm", windows=windows, uninstall_returncode=uninstall_returncode, cmd_shim=cmd_shim)
    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", npm.run)
    monkeypatch.setenv("PATH", str(npm.bin_dir))
    return npm


def _canonical(tmp_path: Path, *, windows: bool) -> Path:
    return tmp_path / "dotfiles" / "bin" / ("codex.cmd" if windows else "codex")


def _migrate(tmp_path: Path, *, windows: bool) -> bool:
    canonical = _canonical(tmp_path, windows=windows)
    return setup_cli_common.migrate_npm_launchers("codex", "@openai/codex", canonical, tmp_path / "standalone")


def _shown(path: Path) -> Path:
    """警告が示すパス。ディレクトリだけを実体のパスにし、シンボリックリンクはたどらない。"""
    return path.parent.resolve() / path.name


def _assert_left_without_package_warning(message: str, launchers: list[Path], canonical: Path) -> None:
    """本体の無い起動スクリプトの警告が、残したファイルの一覧と、保持か退避かの案内と、正規の起動先を示す。"""
    assert all(str(_shown(launcher)) in message for launcher in launchers), message
    assert "意図したラッパーなら保持する" in message
    assert "起動に失敗するなら別の場所へ退避するか削除し" in message
    assert f"`{canonical}`" in message
    assert "package.json" not in message


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
        else f"{package_dir}のbinの実体ではない: 通常ファイル"
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


def test_known_npm_shim_forms_cover_npm_6_to_12() -> None:
    """既知の生成形式は、npm 6.0.0からnpm 12までが同梱したcmd-shimの形を含む。

    npm 6.0.0は2.0.2、npm 6.14は3.0.3、npm 11.20.0は8.0.0、npm 12.2.3は9.0.2を同梱する。
    下の2つの試験は、データの全ての形で移行と本体の無い起動スクリプトの警告を確かめる。
    """
    versions = {version for version, _ in npm_cmd_shim_kinds()}
    assert {"2.0.2", "3.0.3", "8.0.0", "9.0.2"} <= versions


@pytest.mark.parametrize("cmd_shim", npm_cmd_shim_kinds(), ids="-".join)
def test_windows_migrates_unedited_shims_of_every_known_npm(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, cmd_shim: tuple[str, str]
) -> None:
    """npm 6以降が同梱したcmd-shimの各版が生成した未編集の起動スクリプトは、npmの生成物として旧npm版ごと削除する。"""
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True, cmd_shim=cmd_shim)
    launchers = [launcher for launcher in npm.launchers if launcher.exists()]

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        assert _migrate(tmp_path, windows=True)

    assert len(npm.uninstalls) == 1
    assert not npm.package.exists()
    assert not any(launcher.exists() for launcher in launchers)
    assert not caplog.messages


@pytest.mark.parametrize("cmd_shim", npm_cmd_shim_kinds(), ids="-".join)
def test_windows_warns_about_unedited_shims_left_without_package(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, cmd_shim: tuple[str, str]
) -> None:
    """パッケージ本体だけが無くなった配置では、cmd-shimの各版が生成した起動スクリプトを削除せず、1つの警告で示す。"""
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True, cmd_shim=cmd_shim)
    shutil.rmtree(npm.package)
    launchers = {launcher: launcher.read_bytes() for launcher in npm.launchers if launcher.exists()}

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        assert not _migrate(tmp_path, windows=True)

    assert {launcher: launcher.read_bytes() for launcher in launchers} == launchers
    assert not npm.uninstalls
    assert len(caplog.messages) == 1, caplog.messages
    _assert_left_without_package_warning(caplog.messages[0], list(launchers), _canonical(tmp_path, windows=True))


@pytest.mark.parametrize(
    ("suffix", "edit"),
    [
        # 参照先をパッケージ内のユーザーのファイルに変えた`.cmd`。本体があれば保持し、本体を片付けた後も残す。
        pytest.param(".cmd", lambda text: text.replace("bin\\codex.js", "bin\\work.js"), id="cmd-user-entry"),
        # PowerShellが`.cmd`より先に使う`.ps1`に、ユーザーが注記を足したもの。
        pytest.param(".ps1", lambda text: text.replace("\n", "\n# work profile\n", 1), id="ps1-comment"),
    ],
)
def test_windows_keeps_edited_shims_left_without_package(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, suffix: str, edit
) -> None:
    """本体の無い配置では、編集した起動スクリプトも未編集の起動スクリプトも削除せず、毎回1つの警告に全て示す。"""
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True)
    edited = npm.bin_dir / f"codex{suffix}"
    edited.write_text(edit(edited.read_text(encoding="utf-8")), encoding="utf-8", newline="")
    (npm.package / "bin" / "work.js").write_text("#!/usr/bin/env node\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        assert not _migrate(tmp_path, windows=True)
    assert not npm.uninstalls
    assert len(caplog.messages) == 1, caplog.messages
    assert f"{_shown(edited)}（" in caplog.messages[0]
    shutil.rmtree(npm.package)
    launchers = {launcher: launcher.read_bytes() for launcher in npm.launchers}

    for _ in range(2):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
            assert not _migrate(tmp_path, windows=True)
        assert {launcher: launcher.read_bytes() for launcher in launchers} == launchers
        assert len(caplog.messages) == 1, caplog.messages
        _assert_left_without_package_warning(caplog.messages[0], npm.launchers, _canonical(tmp_path, windows=True))
    assert not npm.uninstalls


def test_migrate_keeps_users_link_to_launcher_of_other_npm(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """PATH外のnpmの標準の起動スクリプトを指す、ユーザーのシンボリックリンクは削除せず、リンク先も導入先も残す。"""
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True)
    personal = tmp_path / "personal-bin"
    personal.mkdir()
    link = personal / "codex.cmd"
    link.symlink_to(npm.bin_dir / "codex.cmd")
    monkeypatch.setenv("PATH", str(personal))
    launchers = {launcher: launcher.read_bytes() for launcher in npm.launchers}

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        assert not _migrate(tmp_path, windows=True)

    assert link.is_symlink()
    assert link.readlink() == npm.bin_dir / "codex.cmd"
    assert {launcher: launcher.read_bytes() for launcher in launchers} == launchers
    assert npm.package.is_dir()
    assert not npm.uninstalls
    assert len(caplog.messages) == 1, caplog.messages
    _assert_left_without_package_warning(caplog.messages[0], [link], _canonical(tmp_path, windows=True))


@pytest.mark.parametrize("target", ["inside-package", "outside-package"])
def test_posix_keeps_npm_link_repointed_to_other_script(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, target: str
) -> None:
    """POSIXのnpmの`bin/codex`を、参照先をpackage.jsonのbinとは別のスクリプトに変えたものは、npmの生成物とみなさない。

    旧npm版を自動では削除せず、パッケージと一緒に消えるリンク先のスクリプトも、退避するファイルとして警告に示す。
    """
    npm = _install_global_npm(monkeypatch, tmp_path, windows=False)
    script = npm.package / "bin" / "work.js" if target == "inside-package" else tmp_path / "work.js"
    script.write_text("#!/usr/bin/env node\n// work profile\n", encoding="utf-8")
    link = npm.bin_dir / "codex"
    link.unlink()
    link.symlink_to(script)

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        assert not _migrate(tmp_path, windows=False)

    assert not npm.uninstalls
    assert script.read_text(encoding="utf-8") == "#!/usr/bin/env node\n// work profile\n"
    assert link.resolve() == script.resolve()
    assert len(caplog.messages) == 1, caplog.messages
    assert f"{_shown(link)}（" in caplog.messages[0]
    assert npm.removal() in caplog.messages[0]
    assert (f"{script.resolve()}もパッケージと一緒に削除される" in caplog.messages[0]) is (target == "inside-package")


@pytest.mark.parametrize("windows", [True, False], ids=["windows", "posix"])
@pytest.mark.parametrize("manifest", ["missing", "broken"])
def test_migrate_reports_unreadable_package_json_once_per_package(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, windows: bool, manifest: str
) -> None:
    """package.jsonを読めないパッケージは自動では削除せず、毎回の実行で1つの警告に対象のパスと復旧の手順を示す。"""
    npm = _install_global_npm(monkeypatch, tmp_path, windows=windows)
    package_json = npm.package / "package.json"
    if manifest == "missing":
        package_json.unlink()
    else:
        package_json.write_text('{"name":"@openai/codex",', encoding="utf-8")

    for _ in range(2):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
            assert not _migrate(tmp_path, windows=windows)
        assert len(caplog.messages) == 1, caplog.messages
        assert str(package_json.resolve()) in caplog.messages[0]
        assert "同じ版のpackage.jsonを復元するか、読取権限を直してから`update-dotfiles`を再実行する" in caplog.messages[0]
    assert not npm.uninstalls
    assert npm.package.is_dir()
    assert all(launcher.exists() or launcher.is_symlink() for launcher in npm.launchers)


@pytest.mark.parametrize("package", ["installed", "missing"])
def test_migrate_keeps_shims_of_unknown_npm_and_shows_removal_steps(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, package: str
) -> None:
    """既知の形に無い将来のnpmの起動スクリプトは、npmの生成物と確かめられないものとして残す。

    パッケージがあれば、1つの警告にそのファイルと、旧npm版を削除するコマンドを示す。
    パッケージが無ければ、1つの警告に`.ps1`を含むそのファイルと、正規の起動先を示す。
    """
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True)
    unknown = {
        launcher: launcher.read_bytes().replace(b"\n", b"\n# generated by a future cmd-shim\n", 1) for launcher in npm.launchers
    }
    for launcher, data in unknown.items():
        launcher.write_bytes(data)
    if package == "missing":
        shutil.rmtree(npm.package)

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        assert not _migrate(tmp_path, windows=True)

    assert {launcher: launcher.read_bytes() for launcher in unknown} == unknown
    assert not npm.uninstalls
    assert len(caplog.messages) == 1, caplog.messages
    if package == "installed":
        assert all(
            f"{launcher.resolve()}（npmの既知の生成形式と一致しない通常ファイル）" in caplog.messages[0] for launcher in unknown
        )
        assert npm.removal() in caplog.messages[0]
    else:
        _assert_left_without_package_warning(caplog.messages[0], list(unknown), _canonical(tmp_path, windows=True))


_NPM_SHIMS = npm_windows_shims("node_modules/@openai/codex/bin/codex.js")
_USER_FILES: dict[str, tuple[bool, str, str | bytes]] = {
    # npmの形のまま、参照先をpackage.jsonのbinとは別の、パッケージ内のファイルに変えたもの。
    **{
        f"windows-user-entry{suffix or '-sh'}": (True, f"codex{suffix}", content)
        for suffix, content in npm_windows_shims("node_modules/@openai/codex/bin/work.js").items()
    },
    # ユーザーが正規のCodexを`--profile work`で起動するよう置いたラッパー。
    "windows-wrapper-cmd": (True, "codex.cmd", '@echo off\r\n"%USERPROFILE%\\.local\\bin\\codex.exe" --profile work %*\r\n'),
    # npmが生成した起動スクリプトの起動行に、ユーザーが起動オプションを足したもの。
    "windows-edited-cmd": (True, "codex.cmd", _NPM_SHIMS[".cmd"].replace('codex.js" %*', 'codex.js" --profile work %*')),
    "windows-option-after-arguments": (True, "codex.cmd", _NPM_SHIMS[".cmd"].replace("%*", "%* --profile work")),
    # npmが生成した起動スクリプトに、ユーザーが環境変数の設定を1行足したもの。
    "windows-added-line-cmd": (
        True,
        "codex.cmd",
        _NPM_SHIMS[".cmd"].replace("@ECHO off\r\n", '@ECHO off\r\nset "CODEX_HOME=%USERPROFILE%\\work-codex"\r\n'),
    ),
    "windows-edited-sh-one-line": (
        True,
        "codex",
        _NPM_SHIMS[""].replace(
            'exec node  "$basedir/node_modules/@openai/codex/bin/codex.js" "$@"',
            'exec node  "$basedir/node_modules/@openai/codex/bin/codex.js" --profile work "$@"',
        ),
    ),
    "windows-user-ps1": (True, "codex.ps1", "& codex.exe --profile work @args\n"),
    # npmが生成したsh用の起動スクリプトの、片方の分岐だけを別のパッケージ向けにしたもの。
    "windows-sh-one-branch-other-package": (
        True,
        "codex",
        _NPM_SHIMS[""].replace(
            'exec node  "$basedir/node_modules/@openai/codex/bin/codex.js"',
            'exec node  "$basedir/node_modules/@other/cli/bin/cli.js"',
        ),
    ),
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


def test_migrate_warns_once_per_directory_about_launchers_left_without_package(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """本体の無いnpm起動スクリプトは、配置先ごとに1つの警告で示し、本体のあるものとnpmを参照しないものは示さない。

    どのファイルも削除しない。
    """
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    shims = npm_windows_shims("node_modules/@openai/codex/bin/codex.js")
    files = {
        "orphan": {f"codex{suffix}": content.encode() for suffix, content in shims.items()},
        "unrelated": {"codex.ps1": b"& codex.exe @args\n"},
        # 本体の無いnpm版を、起動行にユーザーが起動オプションを足して使っていたもの。
        "edited": {"codex.cmd": shims[".cmd"].replace('codex.js" %*', 'codex.js" --profile work %*').encode()},
        # 本体の無いnpm版に、ユーザーが環境変数の設定を1行足して使っていたもの。
        "added-line": {
            "codex.cmd": shims[".cmd"]
            .replace("@ECHO off\r\n", '@ECHO off\r\nset "CODEX_HOME=%USERPROFILE%\\work-codex"\r\n')
            .encode()
        },
        # npmの形のまま、パッケージの外のファイルを指すよう参照先を書き換えたもの。
        "outside-package": {"codex.cmd": npm_windows_shims("node_modules/@openai/codex/../../work/codex.js")[".cmd"].encode()},
    }
    for directory, contents in files.items():
        (tmp_path / directory).mkdir()
        for name, data in contents.items():
            (tmp_path / directory / name).write_bytes(data)
    installed = tmp_path / "installed"
    (installed / "node_modules" / "@openai" / "codex").mkdir(parents=True)
    (installed / "codex.ps1").write_text(shims[".ps1"], encoding="utf-8", newline="")
    monkeypatch.setenv("PATH", os.pathsep.join(str(tmp_path / name) for name in (*files, "installed")))
    monkeypatch.setattr(
        setup_cli_common.common,
        "run_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError((args, kwargs))),
    )
    canonical = tmp_path / "canonical" / "codex.cmd"

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        assert not setup_cli_common.migrate_npm_launchers("codex", "@openai/codex", canonical, canonical.parent)

    assert {
        directory: {name: (tmp_path / directory / name).read_bytes() for name in contents}
        for directory, contents in files.items()
    } == files
    assert (installed / "codex.ps1").read_text(encoding="utf-8") == shims[".ps1"]
    warned = {directory: [m for m in caplog.messages if str(tmp_path / directory) in m] for directory in (*files, "installed")}
    assert {directory: len(messages) for directory, messages in warned.items()} == {
        **dict.fromkeys(files, 1),
        "unrelated": 0,
        "installed": 0,
    }
    for directory, contents in files.items():
        if directory != "unrelated":
            launchers = [tmp_path / directory / name for name in contents]
            _assert_left_without_package_warning(warned[directory][0], launchers, canonical)
