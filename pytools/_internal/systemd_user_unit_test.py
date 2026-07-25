"""systemd user unit共通処理のテスト。"""

import pathlib
import subprocess
import typing

import pytest

from pytools._internal import claude_common, systemd_user_unit


def test_setup_writes_and_applies_unit(tmp_path: pathlib.Path, monkeypatch: typing.Any) -> None:
    """unit配置後にsystemd状態を更新する。"""
    executable = tmp_path / "bin/tool"
    executable.parent.mkdir()
    executable.write_text("", encoding="utf-8")
    unit = tmp_path / "tool.service"
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "Linger=yes", "")

    monkeypatch.setattr(claude_common, "run_subprocess", run)
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

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "Linger=yes", "")

    def unexpected_write(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("一致するunitを書き直した")

    monkeypatch.setattr(claude_common, "run_subprocess", run)
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

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(command, 0, "Linger=yes", "")

    monkeypatch.setattr(claude_common, "atomic_write_text", write)
    monkeypatch.setattr(claude_common, "run_subprocess", run)
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
    ("failed_label", "return_value", "expected"),
    [
        ("daemon-reload", 1, "daemon-reload: 失敗"),
        ("enable", 2, "enable: 失敗"),
        ("restart", 3, "restart: 失敗"),
    ],
)
def test_setup_warns_for_systemctl_failures(
    tmp_path: pathlib.Path,
    monkeypatch: typing.Any,
    caplog: pytest.LogCaptureFixture,
    failed_label: str,
    return_value: int,
    expected: str,
) -> None:
    """各systemctl失敗を警告して後続処理を継続する。"""
    executable = tmp_path / "tool"
    executable.write_text("", encoding="utf-8")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        label = command[2] if command[:2] == ["systemctl", "--user"] else "loginctl"
        code = return_value if label == failed_label else 0
        return subprocess.CompletedProcess(command, code, "Linger=yes", "")

    monkeypatch.setattr(claude_common, "run_subprocess", run)
    assert systemd_user_unit.setup(
        unit_path=tmp_path / "tool.service",
        executable_path=executable,
        unit_content="unit\n",
        log_tag="test",
        service_name="tool.service",
    )
    assert expected in caplog.text


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
