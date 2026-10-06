"""`atk run-command`の公開契約を検証する。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import sys

import pytest

from agent_toolkit._atk import run_command


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    *,
    timeout: float | None = None,
) -> tuple[int, dict[str, object], str]:
    directory = tmp_path / "managed"
    directory.mkdir()
    monkeypatch.setattr(run_command.managed_temp, "create_managed_temp", lambda _prefix: directory)
    args = argparse.Namespace(command_argv=["--", *argv], cwd=tmp_path.resolve(), timeout=timeout)
    result = run_command.run(args)
    captured = capsys.readouterr()
    return result, json.loads(captured.out), captured.err


def test_success_preserves_streams_and_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    command = [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"]

    result, metadata, stderr = _run(monkeypatch, tmp_path, capsys, command)

    assert result == 0
    assert metadata["argv"] == command
    assert metadata["cwd"] == str(tmp_path.resolve())
    assert metadata["child_exit_code"] == 0
    assert metadata["timed_out"] is False
    assert metadata["signal"] is None
    assert pathlib.Path(str(metadata["stdout_path"])).read_bytes() == b"out\n"
    assert pathlib.Path(str(metadata["stderr_path"])).read_bytes() == b"err\n"
    assert (metadata["stdout_lines"], metadata["stdout_bytes"]) == (1, 4)
    assert (metadata["stderr_lines"], metadata["stderr_bytes"]) == (1, 4)
    assert stderr == "成功: 外部コマンドが終了した\n"


def test_nonzero_child_exit_code_is_propagated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result, metadata, stderr = _run(
        monkeypatch,
        tmp_path,
        capsys,
        [sys.executable, "-c", "import sys; print('before'); sys.exit(37)"],
    )

    assert result == 37
    assert metadata["child_exit_code"] == 37
    assert metadata["timed_out"] is False
    assert pathlib.Path(str(metadata["stdout_path"])).read_bytes() == b"before\n"
    assert stderr.startswith("失敗: 外部コマンドが終了コード37で終了した\n")


def test_timeout_returns_124_and_keeps_partial_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result, metadata, _stderr = _run(
        monkeypatch,
        tmp_path,
        capsys,
        [sys.executable, "-c", "import time; print('before', flush=True); time.sleep(30)"],
        timeout=0.05,
    )

    assert result == 124
    assert metadata["timed_out"] is True
    assert pathlib.Path(str(metadata["stdout_path"])).read_bytes() == b"before\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal終了の契約")
def test_signal_exit_returns_128_plus_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result, metadata, _stderr = _run(
        monkeypatch,
        tmp_path,
        capsys,
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"],
    )

    assert result == 128 + signal.SIGTERM
    assert metadata["child_exit_code"] == -signal.SIGTERM
    assert metadata["signal"] == signal.SIGTERM


def test_start_failure_returns_125_with_empty_saved_streams(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result, metadata, _stderr = _run(monkeypatch, tmp_path, capsys, [str(tmp_path / "missing-command")])

    assert result == 125
    assert metadata["child_exit_code"] is None
    assert pathlib.Path(str(metadata["stdout_path"])).read_bytes() == b""
    assert pathlib.Path(str(metadata["stderr_path"])).read_bytes() == b""


def test_empty_non_utf8_and_large_outputs_are_kept_as_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    command = [
        sys.executable,
        "-c",
        "import os; os.write(2, b'\\xff\\x00'); os.write(1, b'x' * 2000000)",
    ]

    result, metadata, _stderr = _run(monkeypatch, tmp_path, capsys, command)

    assert result == 0
    assert metadata["stdout_lines"] == 1
    assert metadata["stdout_bytes"] == 2_000_000
    assert pathlib.Path(str(metadata["stderr_path"])).read_bytes() == b"\xff\x00"


def test_shell_metacharacters_and_leading_hyphen_keep_argv_boundaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    values = ["with space", "-leading", "$HOME", "a|b", "*.txt", ">target"]
    command = [sys.executable, "-c", "import json, sys; print(json.dumps(sys.argv[1:]))", *values]

    result, metadata, _stderr = _run(monkeypatch, tmp_path, capsys, command)

    assert result == 0
    saved = pathlib.Path(str(metadata["stdout_path"])).read_text(encoding="utf-8")
    assert json.loads(saved) == values


def test_empty_command_is_wrapper_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(run_command.managed_temp, "create_managed_temp", lambda _prefix: tmp_path / "unused")
    args = argparse.Namespace(command_argv=[], cwd=tmp_path.resolve(), timeout=None)

    result = run_command.run(args)
    captured = capsys.readouterr()

    assert result == 125
    assert captured.out == ""
    assert "実行するCOMMANDがありません" in captured.err


def test_command_without_separator_is_wrapper_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """子argvの先頭がオプションでも曖昧にならないよう、区切り`--`を必須にする。"""
    monkeypatch.setattr(run_command.managed_temp, "create_managed_temp", lambda _prefix: tmp_path / "unused")
    args = argparse.Namespace(command_argv=[sys.executable, "--version"], cwd=tmp_path.resolve(), timeout=None)

    result = run_command.run(args)
    captured = capsys.readouterr()

    assert result == 125
    assert captured.out == ""
    assert "`--`以後" in captured.err
