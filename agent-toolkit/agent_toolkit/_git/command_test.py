"""Gitコマンド共通ラッパーの出力契約を検証する。"""

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
