"""pytools._internal.setup_cli_commonのnpm版の移行（`warn_about_npm_launchers`）の検出と警告のテスト。"""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pytools._internal import common, setup_cli_common
from pytools._internal._test_helpers import npm_cmd_shim_kinds, npm_windows_shims
from pytools._internal.setup_cli_common_npm_test_helpers import (
    _install_global_npm,
    _migrate,
    _ps1_comment,
    _shown,
)


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
    setup_cli_common.warn_about_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/bin/codex", tmp_path / "canonical"
    )
    assert launcher.is_file()
    output = "\n".join(caplog.messages)
    assert "旧npm版を確認できない" in output
    npm = old / ("npm.cmd" if sys.platform == "win32" else "npm")
    assert f"{setup_cli_common.format_command([str(npm), 'root', '--global'])}: 終了コード1" in output
    assert (stderr or "exit 1") in output
    assert "`update-dotfiles`を再実行すると旧npm版かを再確認" in output


@pytest.mark.parametrize("scenario", ["posix", "windows", "windows-user-cmd"])
def test_npm_warning_does_not_remove_package_or_launchers(monkeypatch, tmp_path: Path, caplog, scenario: str) -> None:
    """検出だけでは、標準のnpm版も同じ配置のユーザーラッパーも削除しない。"""
    windows = scenario != "posix"
    npm = _install_global_npm(monkeypatch, tmp_path, windows=windows)
    if scenario == "windows-user-cmd":
        (npm.bin_dir / "codex.cmd").write_bytes(b'@echo off\r\n"%USERPROFILE%\\.local\\bin\\codex.exe" --profile work %*\r\n')
    files = {path: path.read_bytes() for path in npm.launchers}
    manifest = npm.package / "package.json"
    files[manifest] = manifest.read_bytes()

    _migrate(tmp_path, windows=windows)

    assert not npm.uninstalls
    assert {path: path.read_bytes() for path in files} == files
    assert len(caplog.messages) == 1
    assert "PATHで先に解決される間" in caplog.text
    assert str(tmp_path / "dotfiles" / "bin" / ("codex.cmd" if windows else "codex")) in caplog.text
    assert npm.removal() in caplog.text


def test_warn_about_npm_launchers_searches_extra_directories_once(monkeypatch, tmp_path: Path) -> None:
    """PATH外の明示した配置先を走査し、重複指定しても同じnpm版を一度だけ照会する。"""
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

    setup_cli_common.warn_about_npm_launchers(
        "codex",
        "@openai/codex",
        canonical,
        canonical_prefix,
        extra_search_directories=(nvm_bin, nvm_bin),
    )
    assert [command[1:] for command in calls] == [["prefix", "--global"], ["root", "--global"]]


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

    setup_cli_common.warn_about_npm_launchers("codex", "@openai/codex", launcher, prefix)


@pytest.mark.parametrize("ps1", ["generated", "edited"])
def test_windows_judges_npm_package_whose_only_launcher_left_is_ps1(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, ps1: str
) -> None:
    """PowerShell用の起動先しかなくても、対処を案内する。"""
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True)
    for launcher in npm.launchers[:2]:
        launcher.unlink()
    ps1_file = npm.bin_dir / "codex.ps1"
    if ps1 == "edited":
        ps1_file.write_text(_ps1_comment(ps1_file.read_text(encoding="utf-8")), encoding="utf-8", newline="")
    data = ps1_file.read_bytes()

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        _migrate(tmp_path, windows=True)
    assert not npm.uninstalls
    assert ps1_file.read_bytes() == data
    assert len(caplog.messages) == 1
    assert (f"{_shown(ps1_file)}（" in caplog.text) is (ps1 == "edited")
    assert npm.removal() in caplog.text


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

    setup_cli_common.warn_about_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
    )


def test_known_npm_shim_forms_cover_npm_6_to_12() -> None:
    """既知の生成形式は、npm 6.0.0からnpm 12までが同梱したcmd-shimの形を含む。

    npm 6.0.0は2.0.2、npm 6.14は3.0.3、npm 11.20.0は8.0.0、npm 12.2.3は9.0.2を同梱する。
    データの全ての形で、移行は`test_windows_reports_unedited_shims_of_every_known_npm`、
    本体の無い起動スクリプトの警告は`setup_cli_common_npm_held_test.py`の
    `test_windows_warns_about_unedited_shims_left_without_package`が確かめる。
    """
    versions = {version for version, _ in npm_cmd_shim_kinds()}
    assert {"2.0.2", "3.0.3", "8.0.0", "9.0.2"} <= versions


@pytest.mark.parametrize("cmd_shim", npm_cmd_shim_kinds(), ids="-".join)
def test_windows_reports_unedited_shims_of_every_known_npm(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, cmd_shim: tuple[str, str]
) -> None:
    """npm 6以降が同梱したcmd-shimの各版が生成した未編集の起動スクリプトは、npmの生成物として認識し、旧npm版を案内する。"""
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True, cmd_shim=cmd_shim)
    launchers = [launcher for launcher in npm.launchers if launcher.exists()]

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        _migrate(tmp_path, windows=True)

    assert not npm.uninstalls
    assert npm.package.is_dir()
    assert all(launcher.exists() for launcher in launchers)
    assert len(caplog.messages) == 1
    assert npm.removal() in caplog.text


@pytest.mark.parametrize("suffix", ["", ".cmd", ".ps1"])
def test_windows_keeps_link_placed_where_npm_writes_launcher(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, suffix: str
) -> None:
    """Windowsのnpmは起動先にリンクを作らないため、package.jsonのbinを指すリンクでもユーザーのものとして残す。"""
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True)
    link = npm.bin_dir / f"codex{suffix}"
    link.unlink()
    link.symlink_to(npm.package / "bin" / "codex.js")

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        _migrate(tmp_path, windows=True)

    assert not npm.uninstalls
    assert link.readlink() == npm.package / "bin" / "codex.js"
    assert npm.package.is_dir()
    assert len(caplog.messages) == 1, caplog.messages
    assert f"{_shown(link)}（" in caplog.messages[0]
    assert npm.removal() in caplog.messages[0]


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
        _migrate(tmp_path, windows=False)

    assert not npm.uninstalls
    assert script.read_text(encoding="utf-8") == "#!/usr/bin/env node\n// work profile\n"
    assert link.resolve() == script.resolve()
    assert len(caplog.messages) == 1, caplog.messages
    assert f"{_shown(link)}（" in caplog.messages[0]
    assert npm.removal() in caplog.messages[0]
    assert (f"{script.resolve()}もパッケージと一緒に削除される" in caplog.messages[0]) is (target == "inside-package")


def test_posix_keeps_npm_link_routed_through_users_selection_link(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """POSIXの`bin/codex`を、版の切替用のユーザーのリンクを経由してbinへ着くよう変えたものは、npmの生成物とみなさない。"""
    npm = _install_global_npm(monkeypatch, tmp_path, windows=False)
    selection = tmp_path / "versions" / "current"
    selection.parent.mkdir()
    selection.symlink_to(npm.package / "bin" / "codex.js")
    link = npm.bin_dir / "codex"
    link.unlink()
    link.symlink_to(selection)

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        _migrate(tmp_path, windows=False)

    assert not npm.uninstalls
    assert link.readlink() == selection
    assert selection.resolve() == (npm.package / "bin" / "codex.js").resolve()
    assert len(caplog.messages) == 1, caplog.messages
    assert f"{_shown(link)}（" in caplog.messages[0]
    assert npm.removal() in caplog.messages[0]


def test_posix_reports_npm_link_through_linked_prefix(monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """npmのprefixがディレクトリのリンクの先にあっても、npmが作った`bin/codex`は生成物として認識し、旧npm版を案内する。"""
    npm = _install_global_npm(monkeypatch, tmp_path, windows=False)
    (tmp_path / "npm-link").symlink_to(npm.prefix)
    npm.npm = tmp_path / "npm-link" / "bin" / "npm"
    monkeypatch.setenv("PATH", str(npm.npm.parent))

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        _migrate(tmp_path, windows=False)

    assert not npm.uninstalls
    assert (npm.bin_dir / "codex").is_symlink()
    assert npm.removal() in caplog.text


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
        _migrate(tmp_path, windows=windows)

    assert (user_file.read_bytes() if user_file.exists() else None) == data
    assert not npm.uninstalls
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

    setup_cli_common.warn_about_npm_launchers(
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
    monkeypatch, tmp_path: Path, caplog, bin_json: str, referenced_name: str, expected: bool
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

    setup_cli_common.warn_about_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
    )
    assert ("npmの生成物と確認できない" not in caplog.text and "旧npm版を保持" in caplog.text) is expected


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

    setup_cli_common.warn_about_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="拡張子なしの起動ファイルでPOSIXの照会経路を確かめる")
def test_query_failure_asks_to_repair_the_failed_command(tmp_path, monkeypatch, caplog):
    """導入先の照会のうち失敗したコマンドの修復を案内し、成功したコマンドの修復は案内しない。"""
    old = tmp_path / "old"
    old.mkdir()
    for name in ("codex", "npm"):
        (old / name).write_text("", encoding="utf-8")
        (old / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(old))

    def npm(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 3, "", "fixture prefix failed")
        return subprocess.CompletedProcess(command, 0, str(old / "node_modules"), "")

    monkeypatch.setattr(common, "run_subprocess", npm)
    setup_cli_common.warn_about_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/bin/codex", tmp_path / "canonical"
    )

    assert "prefix --global" in caplog.text
    assert "root --global`が導入先" not in caplog.text
    assert "失敗した照会が成功するよう" in caplog.text


def test_query_failure_shows_runnable_powershell_command(monkeypatch):
    """Windowsで照会の失敗を示すコマンドは、PowerShellでそのまま実行できる表記にする。"""
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")

    def npm(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 3, "", "fixture prefix failed")
        return subprocess.CompletedProcess(command, 0, "C:/Old Dir/node_modules", "")

    monkeypatch.setattr(common, "run_subprocess", npm)
    npm_path = Path("C:/Old Dir/npm.cmd")
    with pytest.raises(setup_cli_common._NpmQueryError) as raised:  # pylint: disable=protected-access
        setup_cli_common._npm_global_root(npm_path)  # pylint: disable=protected-access

    # Windowsでは入力のPathがバックスラッシュへ正規化されるため、期待値も同じPathから組み立てる。
    expected = setup_cli_common.format_command([str(npm_path), "prefix", "--global"])
    assert expected.startswith("PowerShellで`& '")
    assert expected in str(raised.value)
    assert f'"{npm_path}" prefix' not in str(raised.value)
