"""pytools._internal.setup_dotfiles_autoupdateのテスト。"""

import pathlib
import typing

import pytest

from pytools._internal import common, host_roles, setup_dotfiles_autoupdate, systemd_user_unit


@pytest.fixture(name="prepared")
def _prepared(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """euryale、dotfilesルート、スクリプト、uvを準備する。"""
    root = tmp_path / "dotfiles"
    script = root / "libexec" / "update_dotfiles_if_upstream_changed.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    uv = tmp_path / "bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("", encoding="utf-8")
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: True)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(common, "resolve_uv_path", lambda: uv)
    monkeypatch.setattr(setup_dotfiles_autoupdate.pathlib.Path, "home", lambda: tmp_path / "home")
    return root, script, uv


def test_non_euryale_skips_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """対象ホストでなければtimer設定を呼ばない。"""
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: False)
    monkeypatch.setattr(systemd_user_unit, "setup_timer", lambda **kwargs: pytest.fail(str(kwargs)))

    assert setup_dotfiles_autoupdate.run().changed is False


@pytest.mark.parametrize("missing", ["root", "script", "uv"])
def test_missing_dependency_skips_timer(
    prepared: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    """root、スクリプトまたはuvを解決できなければtimerを配置しない。"""
    _root, script, _uv = prepared
    if missing == "root":
        monkeypatch.setattr(common, "find_dotfiles_root", lambda: None)
    elif missing == "script":
        script.unlink()
    else:
        monkeypatch.setattr(common, "resolve_uv_path", lambda: None)
    monkeypatch.setattr(systemd_user_unit, "setup_timer", lambda **kwargs: pytest.fail(str(kwargs)))

    assert setup_dotfiles_autoupdate.run().changed is False


def test_run_passes_absolute_paths_and_unit_contents(
    prepared: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """解決した絶対パスと確定unit本文を共通timer設定へ渡す。"""
    _root, script, uv = prepared
    received: dict[str, typing.Any] = {}

    def setup_timer(**kwargs: typing.Any) -> bool:
        received.update(kwargs)
        return True

    monkeypatch.setattr(systemd_user_unit, "setup_timer", setup_timer)
    assert setup_dotfiles_autoupdate.run().changed is True

    assert received["executable_path"] == uv
    assert received["timer_name"] == "dotfiles-autoupdate.timer"
    assert received["service_unit_path"] == pathlib.Path.home() / ".config/systemd/user/dotfiles-autoupdate.service"
    assert received["timer_unit_path"] == pathlib.Path.home() / ".config/systemd/user/dotfiles-autoupdate.timer"
    service_content = str(received["service_unit_content"])
    assert systemd_user_unit.USER_UNIT_PATH_ENVIRONMENT in service_content
    assert f"ExecStart={uv} run --no-project --script {script}" in service_content
    assert (
        received["timer_unit_content"]
        == """[Unit]
Description=Check origin/develop for dotfiles updates

[Timer]
OnStartupSec=1min
OnUnitInactiveSec=10min
Unit=dotfiles-autoupdate.service

[Install]
WantedBy=timers.target
"""
    )


def test_run_propagates_setup_error(
    prepared: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """timerのactive確認失敗をpost_applyへ伝播させる。"""
    del prepared

    def fail(**kwargs: object) -> bool:
        del kwargs
        raise systemd_user_unit.SetupError("inactive")

    monkeypatch.setattr(systemd_user_unit, "setup_timer", fail)
    with pytest.raises(systemd_user_unit.SetupError, match="inactive"):
        setup_dotfiles_autoupdate.run()
