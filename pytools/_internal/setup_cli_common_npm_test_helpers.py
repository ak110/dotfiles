"""pytools._internal.setup_cli_common_npm_test.pyとsetup_cli_common_npm_held_test.pyが共有する、npmの導入先のfixtureと補助関数。"""

import shutil
import subprocess
from pathlib import Path

import pytest

from pytools._internal import setup_cli_common
from pytools._internal._test_helpers import npm_windows_shims, patch_windows_processes


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
        cmd_shim: tuple[str, str] = ("8.0.0", "node"),
    ) -> None:
        self.windows = windows
        self.prefix = prefix
        self.root = prefix / "node_modules" if windows else prefix / "lib" / "node_modules"
        self.bin_dir = prefix if windows else prefix / "bin"
        self.package = self.root / "@openai" / "codex"
        self.npm = self.bin_dir / ("npm.cmd" if windows else "npm")
        self.launchers = [self.bin_dir / f"codex{suffix}" for suffix in (("", ".cmd", ".ps1") if windows else ("",))]
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
    cmd_shim: tuple[str, str] = ("8.0.0", "node"),
) -> _GlobalNpm:
    """npmでグローバルに導入した旧版のCodexを、PATHで見つかる状態にする。

    Windowsの起動スクリプトは`cmd_shim`（cmd-shimの版と、binの実体の先頭行の種類）が生成した形である。
    """
    if windows:
        patch_windows_processes(monkeypatch, [])
    else:
        monkeypatch.setattr(setup_cli_common.sys, "platform", "linux")
    npm = _GlobalNpm(tmp_path / "npm", windows=windows, cmd_shim=cmd_shim)
    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", npm.run)
    monkeypatch.setenv("PATH", str(npm.bin_dir))
    return npm


def _canonical(tmp_path: Path, *, windows: bool) -> Path:
    return tmp_path / "dotfiles" / "bin" / ("codex.cmd" if windows else "codex")


def _migrate(tmp_path: Path, *, windows: bool) -> None:
    canonical = _canonical(tmp_path, windows=windows)
    setup_cli_common.warn_about_npm_launchers("codex", "@openai/codex", canonical, tmp_path / "standalone")


def _shown(path: Path) -> Path:
    """警告が示すパス。ディレクトリだけを実体のパスにし、シンボリックリンクはたどらない。"""
    return path.parent.resolve() / path.name


def _ps1_comment(text: str) -> str:
    return text.replace("\n", "\n# work profile\n", 1)
