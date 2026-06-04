"""pytools._internal.setup_media_remote のテスト。"""

import pathlib
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from pytools._internal import setup_media_remote

_FakeRun = Callable[..., subprocess.CompletedProcess[str] | None]


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
    monkeypatch.setattr(setup_media_remote.socket, "gethostname", lambda: "Stheno")
    pythonw = tmp_path / ".local" / "share" / "uv" / "tools" / "pytools" / "Scripts" / "pythonw.exe"
    pythonw.parent.mkdir(parents=True, exist_ok=True)
    pythonw.touch()
    return tmp_path


@pytest.fixture(name="startup_dir")
def _startup_dir(windows_stheno: pathlib.Path) -> pathlib.Path:
    startup = windows_stheno / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True)
    return startup


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
def test_pythonw_missing_skips(windows_stheno: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    # uv tool venvのpythonw.exeを削除しwhichもNoneを返すようにする。
    (windows_stheno / ".local" / "share" / "uv" / "tools" / "pytools" / "Scripts" / "pythonw.exe").unlink()
    monkeypatch.setattr(setup_media_remote.shutil, "which", lambda _: None)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run() is False
    assert not calls


@pytest.mark.usefixtures("startup_dir")
def test_creates_shortcut_when_missing(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls, _ok()),
    )
    assert setup_media_remote.run() is True
    cmd_strings = [" ".join(c) for c in calls]
    assert any("Save()" in s for s in cmd_strings)
    assert any(setup_media_remote.LNK_NAME in s for s in cmd_strings)
    assert any("pythonw.exe" in s for s in cmd_strings)
    assert any(setup_media_remote.PYTHON_ARGS in s for s in cmd_strings)


@pytest.mark.usefixtures("startup_dir")
def test_create_shortcut_failure_returns_false(monkeypatch: pytest.MonkeyPatch):
    """PowerShellが`returncode != 0`で失敗したとき`run()`は`False`を返す。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls, _ok(returncode=1)),
    )
    assert setup_media_remote.run() is False
    cmd_strings = [" ".join(c) for c in calls]
    # 生成自体は試みられる（Save()呼び出しが渡される）が、戻り値非ゼロのためFalse。
    assert any("Save()" in s for s in cmd_strings)


def test_idempotent_when_target_matches(
    startup_dir: pathlib.Path, windows_stheno: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    lnk = startup_dir / setup_media_remote.LNK_NAME
    lnk.touch()
    target = windows_stheno / ".local" / "share" / "uv" / "tools" / "pytools" / "Scripts" / "pythonw.exe"
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_branching_fake(
            calls,
            _ok(),
            _ok(stdout=f"{target}\t{setup_media_remote.PYTHON_ARGS}"),
        ),
    )
    assert setup_media_remote.run() is False
    cmd_strings = [" ".join(c) for c in calls]
    assert not any("Save()" in s for s in cmd_strings)


def test_non_stheno_with_existing_shortcut_removes(startup_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(setup_media_remote.socket, "gethostname", lambda: "other-host")
    lnk = startup_dir / setup_media_remote.LNK_NAME
    lnk.touch()
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run() is True
    assert not lnk.is_file()
    assert not calls


@pytest.mark.usefixtures("startup_dir")
def test_non_stheno_without_existing_shortcut_is_noop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(setup_media_remote.socket, "gethostname", lambda: "other-host")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_media_remote.claude_common,
        "run_subprocess",
        _make_static_fake(calls),
    )
    assert setup_media_remote.run() is False
    assert not calls
