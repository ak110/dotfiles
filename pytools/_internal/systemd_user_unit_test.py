"""systemd user unit共通処理のテスト。"""

import pathlib
import subprocess
import typing

import pytest

from pytools._internal import claude_common, systemd_user_unit

_ACTIVE_SHOW = "ActiveState=active\nNRestarts=0\n"


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """常駐確認の待機時間を無効化する。"""
    monkeypatch.setattr(systemd_user_unit, "_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(systemd_user_unit, "_POLL_SECONDS", 0.0)
    monkeypatch.setattr(systemd_user_unit, "_CONFIRM_SECONDS", 0.0)


def _show_aware(stdout: str = "Linger=yes", returncode: int = 0):
    """showコマンドへ常駐状態を返すrun_subprocess stubを生成する。"""

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "show" in command and "--property=ActiveState" in command:
            return subprocess.CompletedProcess(command, 0, _ACTIVE_SHOW, "")
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    return run


def _recording(commands: list[list[str]]):
    """呼び出し履歴を記録しつつ常駐状態を返すstubを生成する。"""
    inner = _show_aware()

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return inner(command, **kwargs)

    return run


def test_setup_writes_and_applies_unit(tmp_path: pathlib.Path, monkeypatch: typing.Any) -> None:
    """unit配置後にsystemd状態を更新する。"""
    executable = tmp_path / "bin/tool"
    executable.parent.mkdir()
    executable.write_text("", encoding="utf-8")
    unit = tmp_path / "tool.service"
    commands: list[list[str]] = []

    monkeypatch.setattr(claude_common, "run_subprocess", _recording(commands))
    assert systemd_user_unit.setup(
        unit_path=unit,
        executable_path=executable,
        unit_content="unit\n",
        log_tag="test",
        service_name="tool.service",
    )
    assert unit.read_text(encoding="utf-8") == "unit\n"
    assert commands[:3] == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "tool.service"],
        ["systemctl", "--user", "restart", "tool.service"],
    ]


def test_setup_skips_missing_executable(tmp_path: pathlib.Path) -> None:
    """実行ファイル不在時は変更しない。"""
    assert not systemd_user_unit.setup(
        unit_path=tmp_path / "tool.service",
        executable_path=tmp_path / "missing",
        unit_content="unit\n",
        log_tag="test",
        service_name="tool.service",
    )


def test_setup_does_not_rewrite_matching_unit(tmp_path: pathlib.Path, monkeypatch: typing.Any) -> None:
    """一致するunitを再書込みせずdaemon-reloadも省く。"""
    executable = tmp_path / "tool"
    executable.write_text("", encoding="utf-8")
    unit = tmp_path / "tool.service"
    unit.write_text("unit\n", encoding="utf-8")
    commands: list[list[str]] = []

    def unexpected_write(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("一致するunitを書き直した")

    monkeypatch.setattr(claude_common, "run_subprocess", _recording(commands))
    monkeypatch.setattr(claude_common, "atomic_write_text", unexpected_write)
    assert systemd_user_unit.setup(
        unit_path=unit,
        executable_path=executable,
        unit_content="unit\n",
        log_tag="test",
        service_name="tool.service",
    )
    assert commands[:2] == [
        ["systemctl", "--user", "enable", "tool.service"],
        ["systemctl", "--user", "restart", "tool.service"],
    ]


def test_setup_uses_atomic_write(tmp_path: pathlib.Path, monkeypatch: typing.Any) -> None:
    """変更時のunit配置にatomic writeと所定modeを使う。"""
    executable = tmp_path / "tool"
    executable.write_text("", encoding="utf-8")
    writes: list[tuple[pathlib.Path, str, int, str]] = []

    def write(path: pathlib.Path, content: str, *, mode: int, tag: str) -> None:
        writes.append((path, content, mode, tag))

    monkeypatch.setattr(claude_common, "atomic_write_text", write)
    monkeypatch.setattr(claude_common, "run_subprocess", _show_aware())
    unit = tmp_path / "tool.service"
    assert systemd_user_unit.setup(
        unit_path=unit,
        executable_path=executable,
        unit_content="unit\n",
        log_tag="test",
        service_name="tool.service",
    )
    assert writes == [(unit, "unit\n", 0o644, "test")]


@pytest.mark.parametrize(
    ("failed_label", "return_value"),
    [
        ("daemon-reload", 1),
        ("enable", 2),
        ("restart", 3),
    ],
)
def test_setup_raises_for_systemctl_failures(
    tmp_path: pathlib.Path,
    monkeypatch: typing.Any,
    failed_label: str,
    return_value: int,
) -> None:
    """systemctl失敗を例外へ変換して後続処理を打ち切る。"""
    executable = tmp_path / "tool"
    executable.write_text("", encoding="utf-8")
    show_calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "show" in command and "--property=ActiveState" in command:
            show_calls.append(command)
            return subprocess.CompletedProcess(command, 0, _ACTIVE_SHOW, "")
        label = command[2] if command[:2] == ["systemctl", "--user"] else "loginctl"
        code = return_value if label == failed_label else 0
        return subprocess.CompletedProcess(command, code, "Linger=yes", "")

    monkeypatch.setattr(claude_common, "run_subprocess", run)
    with pytest.raises(systemd_user_unit.SetupError, match=f"{failed_label}に失敗"):
        systemd_user_unit.setup(
            unit_path=tmp_path / "tool.service",
            executable_path=executable,
            unit_content="unit\n",
            log_tag="test",
            service_name="tool.service",
        )
    # systemctl失敗時は常駐確認へ到達しない（旧プロセス残存による誤判定を避けるため）。
    assert not show_calls


@pytest.mark.parametrize(
    ("loginctl_result", "expected"),
    [
        (None, "linger状態を確認できません"),
        (subprocess.CompletedProcess(["loginctl"], 1, "", ""), "linger確認: 失敗"),
        (subprocess.CompletedProcess(["loginctl"], 0, "Linger=no", ""), "linger 無効"),
    ],
)
def test_setup_reports_linger_states(
    tmp_path: pathlib.Path,
    monkeypatch: typing.Any,
    caplog: pytest.LogCaptureFixture,
    loginctl_result: subprocess.CompletedProcess[str] | None,
    expected: str,
) -> None:
    """loginctl不在・失敗・linger無効を識別して通知する。"""
    executable = tmp_path / "tool"
    executable.write_text("", encoding="utf-8")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str] | None:
        del kwargs
        if command[0] == "loginctl":
            return loginctl_result
        if "show" in command and "--property=ActiveState" in command:
            return subprocess.CompletedProcess(command, 0, _ACTIVE_SHOW, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(claude_common, "run_subprocess", run)
    assert systemd_user_unit.setup(
        unit_path=tmp_path / "tool.service",
        executable_path=executable,
        unit_content="unit\n",
        log_tag="test",
        service_name="tool.service",
    )
    assert expected in caplog.text


def test_setup_raises_when_service_never_activates(
    tmp_path: pathlib.Path,
    monkeypatch: typing.Any,
) -> None:
    """制限時間内にactiveへ至らない場合はSetupErrorを送出する。"""
    executable = tmp_path / "tool"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(systemd_user_unit, "_ACTIVE_TIMEOUT_SECONDS", 0.0)

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "show" in command:
            return subprocess.CompletedProcess(command, 0, "ActiveState=activating\nNRestarts=3\n", "")
        return subprocess.CompletedProcess(command, 0, "Linger=yes", "")

    monkeypatch.setattr(claude_common, "run_subprocess", run)
    with pytest.raises(systemd_user_unit.SetupError, match="起動しません"):
        systemd_user_unit.setup(
            unit_path=tmp_path / "tool.service",
            executable_path=executable,
            unit_content="unit\n",
            log_tag="test",
            service_name="tool.service",
        )


def test_setup_raises_when_service_restarts_repeatedly(
    tmp_path: pathlib.Path,
    monkeypatch: typing.Any,
) -> None:
    """2回の観測でNRestartsが増える場合はSetupErrorを送出する。"""
    executable = tmp_path / "tool"
    executable.write_text("", encoding="utf-8")
    restarts = iter(["1", "2"])

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "show" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                f"ActiveState=active\nNRestarts={next(restarts)}\n",
                "",
            )
        return subprocess.CompletedProcess(command, 0, "Linger=yes", "")

    monkeypatch.setattr(claude_common, "run_subprocess", run)
    with pytest.raises(systemd_user_unit.SetupError, match="常駐しません"):
        systemd_user_unit.setup(
            unit_path=tmp_path / "tool.service",
            executable_path=executable,
            unit_content="unit\n",
            log_tag="test",
            service_name="tool.service",
        )


def test_setup_raises_when_state_query_fails(
    tmp_path: pathlib.Path,
    monkeypatch: typing.Any,
) -> None:
    """状態照会が失敗した場合はSetupErrorを送出する。"""
    executable = tmp_path / "tool"
    executable.write_text("", encoding="utf-8")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "show" in command:
            return subprocess.CompletedProcess(command, 1, "", "")
        return subprocess.CompletedProcess(command, 0, "Linger=yes", "")

    monkeypatch.setattr(claude_common, "run_subprocess", run)
    with pytest.raises(systemd_user_unit.SetupError, match="状態を取得できません"):
        systemd_user_unit.setup(
            unit_path=tmp_path / "tool.service",
            executable_path=executable,
            unit_content="unit\n",
            log_tag="test",
            service_name="tool.service",
        )


def test_setup_timer_writes_units_and_enables_only_timer(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2 unitを配置し、oneshot serviceではなくtimerだけを有効化する。"""
    executable = tmp_path / "uv"
    executable.write_text("", encoding="utf-8")
    service_unit = tmp_path / "tool.service"
    timer_unit = tmp_path / "tool.timer"
    commands: list[list[str]] = []
    monkeypatch.setattr(claude_common, "run_subprocess", _recording(commands))

    assert systemd_user_unit.setup_timer(
        service_unit_path=service_unit,
        timer_unit_path=timer_unit,
        executable_path=executable,
        service_unit_content="service\n",
        timer_unit_content="timer\n",
        log_tag="test",
        timer_name="tool.timer",
    )

    assert service_unit.read_text(encoding="utf-8") == "service\n"
    assert timer_unit.read_text(encoding="utf-8") == "timer\n"
    assert commands[:4] == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "tool.timer"],
        ["systemctl", "--user", "restart", "tool.timer"],
        ["systemctl", "--user", "show", "tool.timer", "--property=ActiveState"],
    ]
    assert ["systemctl", "--user", "enable", "tool.service"] not in commands


def test_setup_timer_skips_missing_executable(tmp_path: pathlib.Path) -> None:
    """実行ファイル不在時はunitを配置しない。"""
    assert not systemd_user_unit.setup_timer(
        service_unit_path=tmp_path / "tool.service",
        timer_unit_path=tmp_path / "tool.timer",
        executable_path=tmp_path / "missing",
        service_unit_content="service\n",
        timer_unit_content="timer\n",
        log_tag="test",
        timer_name="tool.timer",
    )


def test_setup_timer_matching_units_skip_daemon_reload(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2 unitが同一内容の場合は再配置せずdaemon-reloadを省く。"""
    executable = tmp_path / "uv"
    executable.write_text("", encoding="utf-8")
    service_unit = tmp_path / "tool.service"
    timer_unit = tmp_path / "tool.timer"
    service_unit.write_text("service\n", encoding="utf-8")
    timer_unit.write_text("timer\n", encoding="utf-8")
    commands: list[list[str]] = []

    def unexpected_write(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("一致するunitを書き直した")

    monkeypatch.setattr(claude_common, "atomic_write_text", unexpected_write)
    monkeypatch.setattr(claude_common, "run_subprocess", _recording(commands))
    assert systemd_user_unit.setup_timer(
        service_unit_path=service_unit,
        timer_unit_path=timer_unit,
        executable_path=executable,
        service_unit_content="service\n",
        timer_unit_content="timer\n",
        log_tag="test",
        timer_name="tool.timer",
    )
    assert commands[:3] == [
        ["systemctl", "--user", "enable", "tool.timer"],
        ["systemctl", "--user", "restart", "tool.timer"],
        ["systemctl", "--user", "show", "tool.timer", "--property=ActiveState"],
    ]


@pytest.mark.parametrize(
    ("failed_label", "return_value"),
    [("daemon-reload", 1), ("enable", 2), ("restart", 3)],
)
def test_setup_timer_raises_for_systemctl_failures(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_label: str,
    return_value: int,
) -> None:
    """timer設定のsystemctl失敗を例外へ変換して打ち切る。"""
    executable = tmp_path / "uv"
    executable.write_text("", encoding="utf-8")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        label = command[2] if command[:2] == ["systemctl", "--user"] else "loginctl"
        code = return_value if label == failed_label else 0
        return subprocess.CompletedProcess(command, code, "", "")

    monkeypatch.setattr(claude_common, "run_subprocess", run)
    with pytest.raises(systemd_user_unit.SetupError, match=f"{failed_label}に失敗"):
        systemd_user_unit.setup_timer(
            service_unit_path=tmp_path / "tool.service",
            timer_unit_path=tmp_path / "tool.timer",
            executable_path=executable,
            service_unit_content="service\n",
            timer_unit_content="timer\n",
            log_tag="test",
            timer_name="tool.timer",
        )


def test_setup_timer_raises_when_timer_is_not_active(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """timerがactiveでない場合はSetupErrorを送出する。"""
    executable = tmp_path / "uv"
    executable.write_text("", encoding="utf-8")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "show" in command:
            return subprocess.CompletedProcess(command, 0, "ActiveState=inactive\n", "")
        return subprocess.CompletedProcess(command, 0, "Linger=yes\n", "")

    monkeypatch.setattr(claude_common, "run_subprocess", run)
    with pytest.raises(systemd_user_unit.SetupError, match="ActiveState=inactive"):
        systemd_user_unit.setup_timer(
            service_unit_path=tmp_path / "tool.service",
            timer_unit_path=tmp_path / "tool.timer",
            executable_path=executable,
            service_unit_content="service\n",
            timer_unit_content="timer\n",
            log_tag="test",
            timer_name="tool.timer",
        )
