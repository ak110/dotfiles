"""bin/autotmuxのサブコマンド挙動を検証する。"""

import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).with_name("autotmux")


def _run(args: list[str], home: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_on_creates_flag(tmp_path: pathlib.Path) -> None:
    result = _run(["on"], tmp_path)
    flag = tmp_path / ".config" / "dotfiles" / "tmux-auto-attach"
    assert result.returncode == 0
    assert flag.exists()
    assert "on" in result.stdout


def test_off_removes_flag(tmp_path: pathlib.Path) -> None:
    flag = tmp_path / ".config" / "dotfiles" / "tmux-auto-attach"
    flag.parent.mkdir(parents=True)
    flag.touch()
    result = _run(["off"], tmp_path)
    assert result.returncode == 0
    assert not flag.exists()
    assert "off" in result.stdout


def test_off_succeeds_when_flag_absent(tmp_path: pathlib.Path) -> None:
    result = _run(["off"], tmp_path)
    assert result.returncode == 0


def test_status_on(tmp_path: pathlib.Path) -> None:
    flag = tmp_path / ".config" / "dotfiles" / "tmux-auto-attach"
    flag.parent.mkdir(parents=True)
    flag.touch()
    result = _run(["status"], tmp_path)
    assert result.returncode == 0
    assert "on" in result.stdout


def test_status_off(tmp_path: pathlib.Path) -> None:
    result = _run(["status"], tmp_path)
    assert result.returncode == 1
    assert "off" in result.stdout


def test_usage_on_unknown_arg(tmp_path: pathlib.Path) -> None:
    result = _run(["bogus"], tmp_path)
    assert result.returncode == 2
    assert "使い方" in result.stderr


def test_usage_on_no_arg(tmp_path: pathlib.Path) -> None:
    result = _run([], tmp_path)
    assert result.returncode == 2
    assert "使い方" in result.stderr
