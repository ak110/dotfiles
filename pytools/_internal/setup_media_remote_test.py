"""pytools._internal.setup_media_remote のテスト。"""

import pathlib
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from pytools._internal import setup_media_remote

_FakeRun = Callable[..., subprocess.CompletedProcess[str] | None]


def _expected_vbs(exe: pathlib.Path) -> str:
    """テスト用VBS本文（本体の生成ロジックと一致する形式）。"""
    return f'CreateObject("WScript.Shell").Run """{exe}"" serve", 0, False\n'


def _ok(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode=returncode, stdout=stdout, stderr="")


def _make_static_fake(
    calls: list[list[str]],
    response: subprocess.CompletedProcess[str] | None = None,
) -> _FakeRun:
    fixed = response if response is not None else _ok()

    def fake(
        cmd: list[str],
        *,
        timeout: float | None = None,
        cwd: pathlib.Path | None = None,
        tag: str | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str] | None:
        del timeout, cwd, tag, kwargs
        calls.append(list(cmd))
        return fixed

    return fake


def _make_branching_fake(
    calls: list[list[str]],
    create_result: subprocess.CompletedProcess[str],
    read_result: subprocess.CompletedProcess[str],
) -> _FakeRun:
    def fake(
        cmd: list[str],
        *,
        timeout: float | None = None,
        cwd: pathlib.Path | None = None,
        tag: str | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str] | None:
        del timeout, cwd, tag, kwargs
        calls.append(list(cmd))
        script = " ".join(cmd)
        return create_result if "Save()" in script else read_result

    return fake


@pytest.fixture(name="windows_stheno")
def _windows_stheno(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    monkeypatch.setattr(setup_media_remote.sys, "platform", "win32")
    monkeypatch.setattr(setup_media_remote.pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setattr(setup_media_remote.socket, "gethostname", lambda: "Stheno")
    exe = tmp_path / ".local" / "bin" / "dotfiles-media-remote.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.touch()
    return tmp_path


@pytest.fixture(name="startup_dir")
def _startup_dir(windows_stheno: pathlib.Path) -> pathlib.Path:
    startup = windows_stheno / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True)
    return startup


@pytest.fixture(name="vbs_path")
def _vbs_path(windows_stheno: pathlib.Path) -> pathlib.Path:
    return windows_stheno / "AppData" / "Local" / "dotfiles" / "media-remote" / "launch.vbs"


@pytest.fixture(name="exe_path")
def _exe_path(windows_stheno: pathlib.Path) -> pathlib.Path:
    return windows_stheno / ".local" / "bin" / "dotfiles-media-remote.exe"


def test_non_windows_returns_false(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(setup_media_remote.sys, "platform", "linux")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run() is False
    assert not calls


@pytest.mark.usefixtures("windows_stheno")
def test_startup_dir_missing_returns_false(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run() is False
    assert not calls


@pytest.mark.usefixtures("startup_dir")
def test_exe_missing_skips(exe_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    # uv tool install経由のexeを削除する。フォールバックは存在しないためスキップされる。
    exe_path.unlink()
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run() is False
    assert not calls


@pytest.mark.usefixtures("startup_dir")
def test_creates_shortcut_and_vbs_when_missing(
    exe_path: pathlib.Path,
    vbs_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls, _ok()),
    )

    assert setup_media_remote.run() is True

    # VBSが配置され、内容は期待形式と一致する。
    assert vbs_path.is_file()
    assert vbs_path.read_text(encoding="utf-8") == _expected_vbs(exe_path)

    # `.lnk`生成PowerShellスクリプト内にwscript.exeターゲットとVBSパスが渡されている。
    cmd_strings = [" ".join(c) for c in calls]
    save_scripts = [s for s in cmd_strings if "Save()" in s]
    assert save_scripts
    assert any(setup_media_remote.LNK_NAME in s for s in save_scripts)
    assert any(setup_media_remote.WSCRIPT_PATH in s for s in save_scripts)
    assert any(str(vbs_path) in s for s in save_scripts)


@pytest.mark.usefixtures("startup_dir")
def test_create_shortcut_failure_returns_false(
    exe_path: pathlib.Path,
    vbs_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """VBS配置済みの状態で`.lnk`生成PowerShellが失敗したとき`run()`は`False`を返す。"""
    vbs_path.parent.mkdir(parents=True, exist_ok=True)
    vbs_path.write_text(_expected_vbs(exe_path), encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls, _ok(returncode=1)),
    )
    assert setup_media_remote.run() is False
    cmd_strings = [" ".join(c) for c in calls]
    assert any("Save()" in s for s in cmd_strings)


def test_idempotent_when_vbs_and_lnk_match(
    startup_dir: pathlib.Path,
    exe_path: pathlib.Path,
    vbs_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
):
    lnk = startup_dir / setup_media_remote.LNK_NAME
    lnk.touch()
    vbs_path.parent.mkdir(parents=True, exist_ok=True)
    vbs_path.write_text(_expected_vbs(exe_path), encoding="utf-8")
    expected_args = f'"{vbs_path}"'
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_branching_fake(
            calls,
            _ok(),
            _ok(stdout=f"{setup_media_remote.WSCRIPT_PATH}\t{expected_args}"),
        ),
    )
    assert setup_media_remote.run() is False
    cmd_strings = [" ".join(c) for c in calls]
    assert not any("Save()" in s for s in cmd_strings)


def test_existing_pythonw_lnk_is_overwritten(
    startup_dir: pathlib.Path,
    exe_path: pathlib.Path,
    vbs_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """旧仕様（TargetPathが`pythonw.exe`）の`.lnk`は新仕様で上書きされる。"""
    del exe_path  # 既存exe検出のため間接参照
    lnk = startup_dir / setup_media_remote.LNK_NAME
    lnk.touch()
    calls: list[list[str]] = []
    old_target = str(pathlib.Path.home() / ".local" / "share" / "uv" / "tools" / "pytools" / "Scripts" / "pythonw.exe")
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_branching_fake(
            calls,
            _ok(),
            _ok(stdout=f"{old_target}\t-m pytools.media_remote serve"),
        ),
    )
    assert setup_media_remote.run() is True
    assert vbs_path.is_file()
    cmd_strings = [" ".join(c) for c in calls]
    save_scripts = [s for s in cmd_strings if "Save()" in s]
    assert save_scripts
    assert any(setup_media_remote.WSCRIPT_PATH in s for s in save_scripts)


def test_non_stheno_removes_existing_lnk_and_vbs(
    startup_dir: pathlib.Path,
    vbs_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(setup_media_remote.socket, "gethostname", lambda: "other-host")
    lnk = startup_dir / setup_media_remote.LNK_NAME
    lnk.touch()
    vbs_path.parent.mkdir(parents=True, exist_ok=True)
    vbs_path.write_text("dummy", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run() is True
    assert not lnk.is_file()
    assert not vbs_path.is_file()
    assert not calls


@pytest.mark.usefixtures("startup_dir")
def test_non_stheno_without_existing_assets_is_noop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(setup_media_remote.socket, "gethostname", lambda: "other-host")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run() is False
    assert not calls
