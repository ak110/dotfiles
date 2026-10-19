"""pytools._internal.setup_dotfiles_autoupdateのテスト。"""

import logging
import pathlib
import subprocess
import typing

import pytest

from pytools._internal import common, host_roles, setup_dotfiles_autoupdate, systemd_user_unit


@pytest.fixture(autouse=True)
def _no_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    """実行ホストのsystemdを操作しないよう、既定ではsystemctlが無い環境にする。"""
    monkeypatch.setattr(setup_dotfiles_autoupdate.shutil, "which", lambda name: None)


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


def test_timer_preserves_configured_codex_home(prepared, monkeypatch: pytest.MonkeyPatch) -> None:
    """systemdで解釈される引用符・specifierを引用し、設定された保存先をtimerへ渡す。"""
    del prepared
    monkeypatch.setenv("CODEX_HOME", '/custom/codex home%u/"quoted"')
    received: dict[str, typing.Any] = {}

    def setup_timer(**kwargs: typing.Any) -> bool:
        received.update(kwargs)
        return True

    monkeypatch.setattr(systemd_user_unit, "setup_timer", setup_timer)
    setup_dotfiles_autoupdate.run()
    assert 'Environment="CODEX_HOME=/custom/codex home%%u/\\"quoted\\""\n' in received["service_unit_content"]


def test_run_returns_failure_with_recovery_when_user_manager_unavailable(
    prepared: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ユーザーマネージャー停止は例外ではなく、復旧手順を次の操作とする失敗として返す。"""
    del prepared

    def fail(**kwargs: typing.Any) -> bool:
        del kwargs
        raise systemd_user_unit.UserManagerUnavailableError(
            "ユーザーマネージャーが停止", "sudo systemctl start user@1000.service"
        )

    monkeypatch.setattr(systemd_user_unit, "setup_timer", fail)
    outcome = setup_dotfiles_autoupdate.run()
    assert outcome.failure is not None and "ユーザーマネージャーが停止" in outcome.failure
    assert outcome.next_action == "sudo systemctl start user@1000.service"


def _stub_systemctl(
    monkeypatch: pytest.MonkeyPatch, returncodes: dict[str, int], stderrs: dict[str, str] | None = None
) -> list[list[str]]:
    """systemctlのサブコマンドごとの終了コードと標準エラーを返す差し替えを追加し、呼び出しの記録を返す。"""
    calls: list[list[str]] = []

    def run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=returncodes.get(cmd[2], 0), stdout="", stderr=(stderrs or {}).get(cmd[2], "")
        )

    monkeypatch.setattr(setup_dotfiles_autoupdate.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(common, "run_subprocess", run_subprocess)
    return calls


@pytest.mark.parametrize("linux_server", [True, False])
def test_run_enables_tmpfiles_clean_timer_on_every_linux_host(
    monkeypatch: pytest.MonkeyPatch,
    linux_server: bool,
) -> None:
    """役割によらず、経過削除タイマーが未有効なら有効化する。"""
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: linux_server)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: None)
    calls = _stub_systemctl(monkeypatch, {"is-enabled": 1})

    assert setup_dotfiles_autoupdate.run().changed is True

    assert ["systemctl", "--user", "enable", "--now", "systemd-tmpfiles-clean.timer"] in calls


def test_tmpfiles_clean_timer_already_enabled_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """既に有効な経過削除タイマーは再有効化せず、変更なしとして扱う。"""
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: False)
    calls = _stub_systemctl(monkeypatch, {})

    assert setup_dotfiles_autoupdate.run().changed is False

    assert not any(call[2] == "enable" for call in calls)


def test_tmpfiles_clean_enable_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """有効化のコマンドが非0で終わった場合はSetupErrorで失敗させる。"""
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: False)
    _stub_systemctl(monkeypatch, {"is-enabled": 1, "enable": 1}, {"enable": "Failed to enable unit: Access denied"})

    with pytest.raises(systemd_user_unit.SetupError, match="systemd-tmpfiles-clean.timer") as excinfo:
        setup_dotfiles_autoupdate.run()
    assert "stderr: Failed to enable unit: Access denied" in str(excinfo.value)
    assert "systemctl --user status systemd-tmpfiles-clean.timer" in str(excinfo.value)
    assert "update-dotfiles" in str(excinfo.value)


@pytest.mark.parametrize("reason", ["no_systemctl", "no_user_manager"])
def test_tmpfiles_clean_skipped_without_user_systemd(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    reason: str,
) -> None:
    """systemctlまたはユーザーのsystemdマネージャーが無い環境では、理由と次の操作を示して見送る。"""
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: False)
    calls = _stub_systemctl(monkeypatch, {"show-environment": 1}, {"show-environment": "Failed to connect to bus"})
    if reason == "no_systemctl":
        monkeypatch.setattr(setup_dotfiles_autoupdate.shutil, "which", lambda name: None)

    with caplog.at_level(logging.INFO):
        assert setup_dotfiles_autoupdate.run().changed is False

    assert not any(call[2] == "enable" for call in calls)
    assert "見送る" in caplog.text
    assert "`update-dotfiles`を再実行する" in caplog.text
    if reason == "no_user_manager":
        assert "stderr: Failed to connect to bus" in caplog.text
        assert "systemctl start user@" in caplog.text
