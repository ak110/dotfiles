"""Gitコマンド共通ラッパーの出力契約を検証する。"""

import os
import pathlib
import subprocess

import pytest

from agent_toolkit._git import command as subject


def test_run_quiet_suppresses_success_output(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """成功したGitの出力を標準出力にも標準エラーにも書かない。"""
    subject.run_quiet(["init", "--initial-branch=main"], tmp_path)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_quiet_reports_failure_and_preserves_exception(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """失敗したGitの診断と終了コードを保持する。"""
    subject.run_quiet(["init", "--initial-branch=main"], tmp_path)
    capsys.readouterr()

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        subject.run_quiet(["rev-parse", "--verify", "missing"], tmp_path)

    captured = capsys.readouterr()
    error = exc_info.value
    assert captured.out == ""
    assert captured.err == error.stderr
    assert error.returncode == 128
    assert error.cmd == ["git", "rev-parse", "--verify", "missing"]
    assert error.output == ""
    assert error.stderr


def test_run_quiet_redirects_failure_stdout_to_stderr(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """失敗したGitの標準出力も呼び出し側の標準エラーへ書く。"""
    (tmp_path / "first.txt").write_text("first\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("second\n", encoding="utf-8")

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        subject.run_quiet(["diff", "--no-index", "--", "first.txt", "second.txt"], tmp_path)

    captured = capsys.readouterr()
    error = exc_info.value
    assert captured.out == ""
    assert captured.err == error.output
    assert error.returncode == 1
    assert error.output
    assert error.stderr == ""


def test_run_quiet_can_hold_failure_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """出力転送を無効にした失敗は診断を例外だけへ保持する。"""
    subject.run_quiet(["init", "--initial-branch=main"], tmp_path)
    capsys.readouterr()

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        subject.run_quiet(
            ["rev-parse", "--verify", "missing"],
            tmp_path,
            forward_error_output=False,
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert exc_info.value.output == ""
    assert exc_info.value.stderr


def test_run_passes_input_and_environment(tmp_path: pathlib.Path) -> None:
    """標準入力と環境変数を`git`へ渡し、テキストの結果を返すこと。"""
    subject.run_quiet(["init", "--initial-branch=main"], tmp_path)
    blob = subject.run(["hash-object", "-w", "--stdin"], tmp_path, capture_output=True, text=True, input="本文\n", check=True)
    shown = subject.run(["cat-file", "-p", blob.stdout.strip()], tmp_path, capture_output=True, check=True)
    configured = subject.run(
        ["config", "--get", "user.name"],
        tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "環境の名前"},
    )

    assert shown.stdout == "本文\n".encode()
    assert configured.stdout == "環境の名前\n"


def test_optional_helpers_return_none_on_failure(tmp_path: pathlib.Path) -> None:
    """非0終了では`None`を返し、`optional_stdout`は起動の失敗でも`None`を返すこと。"""
    assert subject.optional_stdout(["rev-parse", "--verify", "missing"], tmp_path, timeout=30) is None
    assert subject.optional_output(["rev-parse", "--verify", "missing"], tmp_path, timeout=30) is None
    assert subject.optional_stdout(["status"], tmp_path / "missing-directory", timeout=30) is None
    with pytest.raises(OSError):
        subject.optional_output(["status"], tmp_path / "missing-directory", timeout=30)


def test_command_line_prefixes_git() -> None:
    """例外と記録へ載せるコマンドは`git`で始まる引数列であること。"""
    assert subject.command_line(["status", "--porcelain"]) == ["git", "status", "--porcelain"]
