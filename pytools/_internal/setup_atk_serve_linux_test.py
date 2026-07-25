"""`atk serve`自動起動セットアップのテスト。"""

# pylint: disable=protected-access

import os
import pathlib
import stat
import typing

import pytest

from pytools._internal import claude_common, setup_atk_serve_linux, systemd_user_unit


def test_unit_uses_unpinned_serve_command() -> None:
    """unitがhostとportをCLIで固定しないことを検証する。"""
    assert "ExecStart=%h/.local/bin/atk serve\n" in setup_atk_serve_linux._UNIT_CONTENT
    assert "--host" not in setup_atk_serve_linux._UNIT_CONTENT
    assert "--port" not in setup_atk_serve_linux._UNIT_CONTENT


@pytest.mark.parametrize(
    ("platform", "hostname"),
    [("win32", "euryale"), ("linux", "other-host")],
)
def test_run_skips_non_target_environment(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    hostname: str,
) -> None:
    """Linuxかつeuryale以外では設定処理を開始しない。"""
    monkeypatch.setattr(setup_atk_serve_linux.sys, "platform", platform)
    monkeypatch.setattr(setup_atk_serve_linux.socket, "gethostname", lambda: hostname)
    assert not setup_atk_serve_linux.run()


@pytest.mark.parametrize("initial", [None, "stale launcher\n", setup_atk_serve_linux._LAUNCHER])
def test_run_places_executable_launcher_before_unit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    initial: str | None,
) -> None:
    """ランチャーを新設又は更新し、実行可能化した後にunitを設定する。"""
    monkeypatch.setattr(setup_atk_serve_linux.sys, "platform", "linux")
    monkeypatch.setattr(setup_atk_serve_linux.socket, "gethostname", lambda: "euryale.example")
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    launcher = tmp_path / ".local/bin/atk"
    if initial is not None:
        launcher.parent.mkdir(parents=True)
        launcher.write_text(initial, encoding="utf-8")
        launcher.chmod(0o600)
    events: list[str] = []

    def write(path: pathlib.Path, content: str, *, mode: int, tag: str) -> None:
        del tag
        events.append("write")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)

    def setup(**kwargs: typing.Any) -> bool:
        events.append("unit")
        assert kwargs["executable_path"] == launcher
        assert os.access(launcher, os.X_OK)
        assert kwargs["unit_content"] == setup_atk_serve_linux._UNIT_CONTENT
        assert kwargs["service_name"] == "atk-serve.service"
        return True

    monkeypatch.setattr(claude_common, "atomic_write_text", write)
    monkeypatch.setattr(systemd_user_unit, "setup", setup)
    assert setup_atk_serve_linux.run()
    assert launcher.read_text(encoding="utf-8") == setup_atk_serve_linux._LAUNCHER
    assert launcher.stat().st_mode & stat.S_IXUSR
    assert events[-1] == "unit"
    assert ("write" in events) is (initial != setup_atk_serve_linux._LAUNCHER)


def test_run_returns_false_when_launcher_is_not_executable(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実行権限の復旧に失敗した場合はunitを変更しない。"""
    monkeypatch.setattr(setup_atk_serve_linux.sys, "platform", "linux")
    monkeypatch.setattr(setup_atk_serve_linux.socket, "gethostname", lambda: "euryale")
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(claude_common, "atomic_write_text", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_atk_serve_linux.os, "access", lambda *args: False)
    monkeypatch.setattr(systemd_user_unit, "setup", lambda **kwargs: pytest.fail(str(kwargs)))
    assert not setup_atk_serve_linux.run()


def test_run_converts_setup_exception_to_false(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """unit設定例外を利用者向け失敗結果へ変換する。"""
    monkeypatch.setattr(setup_atk_serve_linux.sys, "platform", "linux")
    monkeypatch.setattr(setup_atk_serve_linux.socket, "gethostname", lambda: "euryale")
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)

    def write(path: pathlib.Path, content: str, *, mode: int, tag: str) -> None:
        del tag
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)

    def fail(**kwargs: object) -> bool:
        del kwargs
        raise RuntimeError("unit failure")

    monkeypatch.setattr(claude_common, "atomic_write_text", write)
    monkeypatch.setattr(systemd_user_unit, "setup", fail)
    assert not setup_atk_serve_linux.run()
