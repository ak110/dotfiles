import codecs
import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pytools._internal import setup_cli_common
from pytools._internal._test_helpers import npm_cmd_shim_kinds, npm_windows_shims
from pytools._internal.setup_cli_common_npm_test_helpers import (
    _canonical,
    _install_global_npm,
    _migrate,
    _ps1_comment,
    _shown,
)


def _assert_left_without_package_warning(message: str, launchers: list[Path], canonical: Path) -> None:
    assert all(str(_shown(launcher)) in message for launcher in launchers), message
    assert "意図したラッパーなら保持する" in message
    assert "不要と確認できた場合だけ別の場所へ退避し" in message
    assert f"`{canonical}`" in message
    assert "package.json" not in message


def _assert_held_launcher_guidance(message: str, canonical: Path) -> None:
    assert "PATHで先に解決される間は、正規の起動先ではなく保持した起動先" in message, message
    assert f"不要と確認できた場合だけ別の場所へ退避し、正規の起動先`{canonical}`で起動する" in message, message


def test_warn_about_npm_launchers_keeps_unknown_launcher(monkeypatch, tmp_path: Path) -> None:
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

    setup_cli_common.warn_about_npm_launchers("codex", "@openai/codex", canonical, canonical_prefix)
    assert not any("uninstall" in call for call in calls)


@pytest.mark.parametrize("shim_location", ["data-dir", "default"])
def test_migrate_skips_mise_shim_directory(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, shim_location: str
) -> None:
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
        setup_cli_common.warn_about_npm_launchers(
            "codex", "@openai/codex", tmp_path / "canonical" / "bin" / "codex", tmp_path / "canonical"
        )

    assert not any(str(shims / "codex") in message for message in caplog.messages)
    assert any(str(other / "codex") in message for message in caplog.messages)


@pytest.mark.parametrize("scenario", ["package-missing", "not-symlink"])
def test_migrate_reports_why_package_ownership_is_unconfirmed(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, scenario: str
) -> None:
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
    canonical = tmp_path / "canonical" / "bin" / "codex"

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        setup_cli_common.warn_about_npm_launchers("codex", "@openai/codex", canonical, tmp_path / "canonical")

    assert launcher.is_file()
    messages = [message for message in caplog.messages if str(launcher) in message]
    assert len(messages) == 1
    expected = (
        f"packageのディレクトリが不在: {package_dir}"
        if scenario == "package-missing"
        else f"{package_dir}のbinの実体ではない: 通常ファイル"
    )
    assert expected in messages[0]
    _assert_held_launcher_guidance(messages[0], canonical)


def test_migrate_keeps_launcher_without_adjacent_npm(monkeypatch, tmp_path: Path) -> None:
    launcher = tmp_path / "old" / "codex"
    launcher.parent.mkdir()
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(launcher.parent))

    setup_cli_common.warn_about_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/bin/codex", tmp_path / "canonical"
    )


@pytest.mark.parametrize("left", [("", ".cmd", ".ps1"), (".ps1",)], ids=["all", "ps1-only"])
def test_windows_warns_once_with_ps1_about_launchers_without_adjacent_npm(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, left: tuple[str, ...]
) -> None:
    """npmだけが無くなった配置では、PowerShellが先に使う`.ps1`も含めた起動先を、毎回1つの警告と正規の起動先で示す。

    `codex`と`codex.cmd`を退避して`.ps1`だけが残った配置でも、旧版を起動する`.ps1`を示す。
    """
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True)
    npm.npm.unlink()
    for launcher in npm.launchers:
        if launcher.suffix not in left:
            launcher.unlink()
    launchers = {launcher: launcher.read_bytes() for launcher in npm.launchers if launcher.exists()}

    for _ in range(2):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
            _migrate(tmp_path, windows=True)
        assert {launcher: launcher.read_bytes() for launcher in launchers} == launchers
        assert len(caplog.messages) == 1, caplog.messages
        assert all(str(_shown(launcher)) in caplog.messages[0] for launcher in launchers), caplog.messages[0]
        _assert_held_launcher_guidance(caplog.messages[0], _canonical(tmp_path, windows=True))
    assert not npm.uninstalls
    assert npm.package.is_dir()


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_migrate_excludes_callers_own_wrappers_from_retention_warning(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, platform: str
) -> None:
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
        setup_cli_common.warn_about_npm_launchers(
            "codex",
            "@openai/codex",
            tmp_path / "canonical/bin/codex",
            tmp_path / "canonical",
            excluded_launchers={wrappers / "codex", wrappers / "codex.cmd"},
        )

    assert not any(str(wrappers) in message for message in caplog.messages)
    assert any(str(legacy) in message for message in caplog.messages)


@pytest.mark.parametrize("cmd_shim", npm_cmd_shim_kinds(), ids="-".join)
def test_windows_warns_about_unedited_shims_left_without_package(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, cmd_shim: tuple[str, str]
) -> None:
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True, cmd_shim=cmd_shim)
    shutil.rmtree(npm.package)
    launchers = {launcher: launcher.read_bytes() for launcher in npm.launchers if launcher.exists()}

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        _migrate(tmp_path, windows=True)

    assert {launcher: launcher.read_bytes() for launcher in launchers} == launchers
    assert not npm.uninstalls
    assert len(caplog.messages) == 1, caplog.messages
    _assert_left_without_package_warning(caplog.messages[0], list(launchers), _canonical(tmp_path, windows=True))


def _utf16(text: str, byteorder: str) -> bytes:
    """エディターが保存するBOM付きUTF-16（`byteorder`は`le`か`be`）の本文を返す。"""
    bom = codecs.BOM_UTF16_LE if byteorder == "le" else codecs.BOM_UTF16_BE
    return bom + text.encode(f"utf-16-{byteorder}")


@pytest.mark.parametrize(
    ("suffix", "edit"),
    [
        # 参照先をパッケージ内のユーザーのファイルに変えた`.cmd`。本体があれば保持し、本体を片付けた後も残す。
        pytest.param(".cmd", lambda text: text.replace("bin\\codex.js", "bin\\work.js").encode(), id="cmd-user-entry"),
        # PowerShellが`.cmd`より先に使う`.ps1`に、ユーザーが注記を足したもの。
        pytest.param(".ps1", lambda text: _ps1_comment(text).encode(), id="ps1-comment"),
        # エディターがBOM付きUTF-16で保存し直した`.ps1`。本文が生成形式のままでも、npmの生成物とはみなさない。
        pytest.param(".ps1", lambda text: _utf16(text, "le"), id="ps1-utf16le"),
        pytest.param(".ps1", lambda text: _utf16(_ps1_comment(text), "le"), id="ps1-comment-utf16le"),
        pytest.param(".ps1", lambda text: _utf16(_ps1_comment(text), "be"), id="ps1-comment-utf16be"),
    ],
)
def test_windows_keeps_edited_shims_left_without_package(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, suffix: str, edit
) -> None:
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True)
    edited = npm.bin_dir / f"codex{suffix}"
    edited.write_bytes(edit(edited.read_bytes().decode()))
    (npm.package / "bin" / "work.js").write_text("#!/usr/bin/env node\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        _migrate(tmp_path, windows=True)
    assert not npm.uninstalls
    assert len(caplog.messages) == 1, caplog.messages
    assert f"{_shown(edited)}（" in caplog.messages[0]
    shutil.rmtree(npm.package)
    launchers = {launcher: launcher.read_bytes() for launcher in npm.launchers}

    for _ in range(2):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
            _migrate(tmp_path, windows=True)
        assert {launcher: launcher.read_bytes() for launcher in launchers} == launchers
        assert len(caplog.messages) == 1, caplog.messages
        _assert_left_without_package_warning(caplog.messages[0], npm.launchers, _canonical(tmp_path, windows=True))
    assert not npm.uninstalls


def test_migrate_keeps_users_link_to_launcher_of_other_npm(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True)
    personal = tmp_path / "personal-bin"
    personal.mkdir()
    link = personal / "codex.cmd"
    link.symlink_to(npm.bin_dir / "codex.cmd")
    monkeypatch.setenv("PATH", str(personal))
    launchers = {launcher: launcher.read_bytes() for launcher in npm.launchers}

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        _migrate(tmp_path, windows=True)

    assert link.is_symlink()
    assert link.readlink() == npm.bin_dir / "codex.cmd"
    assert {launcher: launcher.read_bytes() for launcher in launchers} == launchers
    assert npm.package.is_dir()
    assert not npm.uninstalls
    assert len(caplog.messages) == 1, caplog.messages
    _assert_left_without_package_warning(caplog.messages[0], [link], _canonical(tmp_path, windows=True))


@pytest.mark.parametrize("windows", [True, False], ids=["windows", "posix"])
@pytest.mark.parametrize("manifest", ["missing", "broken"])
def test_migrate_reports_unreadable_package_json_once_per_package(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, windows: bool, manifest: str
) -> None:
    npm = _install_global_npm(monkeypatch, tmp_path, windows=windows)
    package_json = npm.package / "package.json"
    if manifest == "missing":
        package_json.unlink()
    else:
        package_json.write_text('{"name":"@openai/codex",', encoding="utf-8")

    for _ in range(2):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
            _migrate(tmp_path, windows=windows)
        assert len(caplog.messages) == 1, caplog.messages
        assert str(package_json.resolve()) in caplog.messages[0]
        assert "同じ版のpackage.jsonを復元するか読取権限を直す" in caplog.messages[0]
    assert not npm.uninstalls
    assert npm.package.is_dir()
    assert all(launcher.exists() or launcher.is_symlink() for launcher in npm.launchers)


@pytest.mark.parametrize("package", ["installed", "missing"])
def test_migrate_keeps_shims_of_unknown_npm_and_shows_removal_steps(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, package: str
) -> None:
    npm = _install_global_npm(monkeypatch, tmp_path, windows=True)
    unknown = {
        launcher: launcher.read_bytes().replace(b"\n", b"\n# generated by a future cmd-shim\n", 1) for launcher in npm.launchers
    }
    for launcher, data in unknown.items():
        launcher.write_bytes(data)
    if package == "missing":
        shutil.rmtree(npm.package)

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_cli_common"):
        _migrate(tmp_path, windows=True)

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


def test_migrate_warns_once_per_directory_about_launchers_left_without_package(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    shims = npm_windows_shims("node_modules/@openai/codex/bin/codex.js")
    files = {
        "orphan": {f"codex{suffix}": content.encode() for suffix, content in shims.items()},
        "unrelated": {"codex.ps1": b"& codex.exe @args\n"},
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
        setup_cli_common.warn_about_npm_launchers("codex", "@openai/codex", canonical, canonical.parent)

    assert {
        directory: {name: (tmp_path / directory / name).read_bytes() for name in contents}
        for directory, contents in files.items()
    } == files
    assert (installed / "codex.ps1").read_text(encoding="utf-8") == shims[".ps1"]
    warned = {directory: [m for m in caplog.messages if str(tmp_path / directory) in m] for directory in (*files, "installed")}
    assert {directory: len(messages) for directory, messages in warned.items()} == dict.fromkeys((*files, "installed"), 1)
    for directory, contents in files.items():
        if directory != "unrelated":
            launchers = [tmp_path / directory / name for name in contents]
            _assert_left_without_package_warning(warned[directory][0], launchers, canonical)
    for directory in ("unrelated", "installed"):
        assert "意図したラッパーなら保持する" not in warned[directory][0]
        assert str(tmp_path / directory / "codex.ps1") in warned[directory][0]
        _assert_held_launcher_guidance(warned[directory][0], canonical)
