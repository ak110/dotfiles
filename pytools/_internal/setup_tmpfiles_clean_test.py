"""post-apply経由で旧自動更新の撤去と一時ファイル経過削除を検証する。"""

import logging
import pathlib
import subprocess
import types
from collections.abc import Callable

import pytest

from pytools import post_apply
from pytools._internal import common, host_roles, setup_tmpfiles_clean, systemd_user_unit

# pylint: disable=protected-access  # post-applyの宣言済み工程と隔離用ログ保存先を使う。


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    # osはpost_applyとも共有されるため、UIDの模擬は対象モジュールの参照だけに限る。
    monkeypatch.setattr(setup_tmpfiles_clean, "os", types.SimpleNamespace(getuid=lambda: 1234))
    monkeypatch.setattr(setup_tmpfiles_clean.platformdirs, "user_state_dir", lambda *_args, **_kwargs: str(tmp_path / "state"))
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", tmp_path / "update.log")
    monkeypatch.setattr(post_apply.sync_report, "REPORT_PATH", tmp_path / "sync-report.json")
    monkeypatch.setattr(setup_tmpfiles_clean.shutil, "which", lambda _name: None)
    monkeypatch.setattr(common, "run_subprocess", lambda *_args, **_kwargs: pytest.fail("実機のsystemdへ接続しない"))


def _stub_systemctl(
    monkeypatch: pytest.MonkeyPatch,
    returncodes: dict[str, int],
    observe: Callable[[list[str]], None] | None = None,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if observe is not None:
            observe(cmd)
        return subprocess.CompletedProcess(cmd, returncodes.get(cmd[2], 0), "", "systemctl diagnostic")

    monkeypatch.setattr(setup_tmpfiles_clean.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(common, "run_subprocess", run)
    return calls


@pytest.mark.parametrize("linux_server", [True, False])
def test_post_apply_removes_old_units_and_state_before_enabling_tmpfiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, linux_server: bool
) -> None:
    """配置済み旧unitを停止して撤去し、役割によらずtmpfilesを有効化する。再実行は変更しない。"""
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: linux_server)
    unit_dir = tmp_path / ".config/systemd/user"
    unit_dir.mkdir(parents=True)
    units = [unit_dir / name for name in ("dotfiles-autoupdate.timer", "dotfiles-autoupdate.service")]
    for unit in units:
        unit.write_text("old unit", encoding="utf-8")
    state = tmp_path / "state/auto-update"
    state.mkdir(parents=True)
    (state / "old.deferred").touch()
    (state / "old.lock").touch()

    def observe(cmd: list[str]) -> None:
        if cmd[2] == "disable":
            assert all(unit.exists() for unit in units)
            assert state.exists()
        if cmd[2] == "daemon-reload":
            assert not any(unit.exists() for unit in units)
            assert state.exists()
        if cmd[2] == "enable":
            assert not state.exists()

    codes = {"is-enabled": 1}
    calls = _stub_systemctl(monkeypatch, codes, observe)
    step = next(step for step in post_apply.DEFAULT_STEPS if step.run is setup_tmpfiles_clean.run)
    with pytest.raises(SystemExit) as result:
        post_apply.main(runner=lambda: post_apply.run([(step.name, step.run)]))
    assert result.value.code == 0
    assert calls == [
        ["systemctl", "--user", "disable", "--now", "dotfiles-autoupdate.timer"],
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "show-environment"],
        ["systemctl", "--user", "is-enabled", "systemd-tmpfiles-clean.timer"],
        ["systemctl", "--user", "enable", "--now", "systemd-tmpfiles-clean.timer"],
    ]
    assert not any(unit.exists() for unit in units)
    assert not state.exists()
    codes.clear()
    calls.clear()
    assert not setup_tmpfiles_clean.run().changed
    assert not any(cmd[2] in {"disable", "daemon-reload", "enable"} for cmd in calls)


@pytest.mark.parametrize("failure", ["no-systemctl", "disable", "daemon-reload", "unlink", "state"])
def test_post_apply_reports_removal_failure_and_next_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    """撤去失敗を終了1と診断で返し、停止できない場合はユニットと状態を保持する。"""
    unit = tmp_path / ".config/systemd/user/dotfiles-autoupdate.timer"
    unit.parent.mkdir(parents=True)
    unit.touch()
    state = tmp_path / "state/auto-update"
    state.mkdir(parents=True)
    calls = _stub_systemctl(monkeypatch, {failure: 1})
    if failure == "no-systemctl":
        monkeypatch.setattr(setup_tmpfiles_clean.shutil, "which", lambda _name: None)
    if failure == "unlink":
        real_unlink = pathlib.Path.unlink

        def unlink(path: pathlib.Path, missing_ok: bool = False, **_kwargs: object) -> None:
            if path == unit:
                raise PermissionError("unit denied")
            real_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    if failure == "state":

        def rmtree(_path: pathlib.Path, **_kwargs: object) -> None:
            raise PermissionError("state denied")

        monkeypatch.setattr(setup_tmpfiles_clean.shutil, "rmtree", rmtree)
    with pytest.raises(SystemExit) as result:
        post_apply.main(runner=lambda: post_apply.run([("経過削除", setup_tmpfiles_clean.run)]))
    assert result.value.code == 1
    error = capsys.readouterr().err
    assert "残存対象:" in error
    assert str(state) in error
    assert "次の操作:" in error and "update-dotfiles" in error
    assert state.exists()
    if failure in {"no-systemctl", "disable", "unlink"}:
        assert unit.exists()
        assert not any(cmd[2] == "daemon-reload" for cmd in calls)
    if failure == "disable":
        assert "systemctl diagnostic" in error
    assert not any(cmd[2] == "enable" for cmd in calls)


@pytest.mark.parametrize("enabled", [True, False])
def test_tmpfiles_enabled_without_old_units(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    calls = _stub_systemctl(monkeypatch, {"is-enabled": 0 if enabled else 1})
    assert setup_tmpfiles_clean.run().changed is not enabled
    assert (any(cmd[2] == "enable" for cmd in calls)) is not enabled
    assert not any(cmd[2] in {"disable", "daemon-reload"} for cmd in calls)


def test_removal_retries_after_only_timer_was_removed(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """timer削除後にservice削除が拒否されても、権限回復後の再実行で撤去を完了する。"""
    directory = tmp_path / ".config/systemd/user"
    directory.mkdir(parents=True)
    timer = directory / "dotfiles-autoupdate.timer"
    service = directory / "dotfiles-autoupdate.service"
    timer.touch()
    service.touch()
    calls = _stub_systemctl(monkeypatch, {})
    unlink = pathlib.Path.unlink

    def fail_service(path: pathlib.Path, missing_ok: bool = False, **_kwargs: object) -> None:
        if path == service:
            raise PermissionError("service denied")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(pathlib.Path, "unlink", fail_service)
    assert setup_tmpfiles_clean.run().failure is not None
    assert not timer.exists() and service.exists()
    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    calls.clear()
    assert setup_tmpfiles_clean.run().failure is None
    assert not service.exists()
    assert not any(command[2] == "disable" for command in calls)
    assert any(command[2] == "daemon-reload" for command in calls)


def test_tmpfiles_enable_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_systemctl(monkeypatch, {"is-enabled": 1, "enable": 1})
    with pytest.raises(systemd_user_unit.SetupError, match="systemctl diagnostic"):
        setup_tmpfiles_clean.run()


@pytest.mark.parametrize("reason", ["no-systemctl", "no-user-manager"])
def test_tmpfiles_skipped_without_user_systemd(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, reason: str
) -> None:
    calls = _stub_systemctl(monkeypatch, {"show-environment": 1})
    if reason == "no-systemctl":
        monkeypatch.setattr(setup_tmpfiles_clean.shutil, "which", lambda _name: None)
    with caplog.at_level(logging.INFO):
        assert not setup_tmpfiles_clean.run().changed
    assert not any(cmd[2] == "enable" for cmd in calls)
    assert "見送る" in caplog.text and "update-dotfiles" in caplog.text
