"""更新の期限掃引からだけ、権限不足を1回の昇格へ送る。"""

from __future__ import annotations

import base64
import logging
import os
import pathlib
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from agent_toolkit._atk.managed_temp import elevation as subject
from agent_toolkit._atk.managed_temp.sweep import SweepResult

pytestmark = pytest.mark.windows

# OSごとの引数の境界を試験し、sudoとUACの実行は分ける。
# pylint: disable=protected-access


@pytest.mark.parametrize(
    "condition", ["interactive", "noninteractive", "process-loop", "no-sudo", "no-candidate", "recovered-parent"]
)
def test_sweep_requests_at_most_one_elevation(monkeypatch, tmp_path, caplog, condition):
    """複数候補を一度に渡す。禁止条件では通常回収だけか無操作とする。"""
    targets = [tmp_path / name for name in ("first", "second")]
    for target in targets:
        target.mkdir()
    candidates = tuple(targets)
    nested = targets[0] / "nested"
    failed = tuple((target, "permission denied") for target in targets)
    if condition == "recovered-parent":
        nested.mkdir()
        failed += ((targets[1], "permission denied"), (nested, "permission denied"))
    sweeps: list[bool] = []
    commands: list[tuple[list[str], pathlib.Path]] = []

    def sweep(*, now, force):
        del now
        sweeps.append(force)
        return SweepResult([], failed, permission_candidates=() if condition == "no-candidate" else candidates)

    def run(argv, *, cwd, check):
        assert check is False
        commands.append((argv, cwd))
        shutil.rmtree(targets[0])
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(subject.sweep, "sweep_managed_temp", sweep)
    monkeypatch.setattr(subject.paths, "temp_root_path", lambda: tmp_path)
    monkeypatch.setattr(subject.sys, "stdin", SimpleNamespace(isatty=lambda: condition != "noninteractive"))
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1" if condition == "process-loop" else "0")
    monkeypatch.setattr(subject.subprocess, "run", run)
    monkeypatch.setattr(subject, "_command", lambda names: ["elevate", *names])
    subject.sweep_for_update(no_sudo=condition == "no-sudo", logger=logging.getLogger(__name__))
    assert sweeps == ([] if condition in {"noninteractive", "process-loop"} else [True])
    elevated = condition in {"interactive", "recovered-parent"}
    expected = [(["elevate", "first", "second"], tmp_path)] if elevated else []
    assert commands == expected
    assert targets[0].exists() is (not elevated)
    assert targets[1].exists()
    retry = [record.message for record in caplog.records if "通常のupdate-dotfilesを再実行" in record.message]
    assert len(retry) == sum(bool(sweeps) and target.exists() for target in (*targets, nested))
    for target in (*targets, nested):
        assert any(f"{target}:" in message for message in retry) is (bool(sweeps) and target.exists())


def test_linux_command_separates_candidate_names() -> None:
    if os.name == "nt":
        pytest.skip("Linuxのコマンド境界")
    assert subject._command(["--looks-like-option", "a name"]) == [
        "/usr/bin/sudo",
        "--",
        "/bin/rm",
        "-rf",
        "--one-file-system",
        "--",
        "--looks-like-option",
        "a name",
    ]


def test_windows_command_transports_names_as_data() -> None:
    names = ["a & b", "single'quote"]
    argv = subject._windows_command(names)
    assert argv[-2] == "-EncodedCommand"
    content = base64.b64decode(argv[-1]).decode("utf-16-le")
    assert "-Verb RunAs -Wait" in content and "-I -S" in content
    assert all(name not in content for name in names)
