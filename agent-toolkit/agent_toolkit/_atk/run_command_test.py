"""`atk run-command`の公開契約を検証する。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable

import pytest

from agent_toolkit._atk import run_command
from agent_toolkit._testing import git_repository


def _public_command(argv: list[str], cwd: pathlib.Path, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], dict]:
    """実CLIから子を起動し、保存された結果を読む。"""
    env = {**env, "PYTHONPATH": str(pathlib.Path(run_command.__file__).resolve().parents[2])}
    result = subprocess.run(
        [sys.executable, "-m", "agent_toolkit.atk", "run-command", "--cwd", str(cwd), *argv],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    metadata = json.loads(result.stdout)
    assert json.loads(pathlib.Path(metadata["record_path"]).read_text(encoding="utf-8")) == metadata
    return result, metadata


def test_public_command_strips_inherited_venv(tmp_path: pathlib.Path) -> None:
    """実子へ渡る環境から仮想環境だけを除き、無関係の値とPATHの順序を保つ。"""
    venv = tmp_path / "inherited"
    path = os.pathsep.join([str(tmp_path / "before"), str(venv / "bin"), "", str(venv / "Scripts"), os.defpath])
    env = {**os.environ, "VIRTUAL_ENV": str(venv), "PATH": path, "PRESERVE_VALUE": "保持する"}
    result, metadata = _public_command(
        ["--", sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
        tmp_path,
        env,
    )
    assert result.returncode == 0, result.stderr
    child = json.loads(pathlib.Path(metadata["stdout_path"]).read_text(encoding="utf-8"))
    assert "VIRTUAL_ENV" not in child
    assert child["PATH"] == os.pathsep.join([str(tmp_path / "before"), "", os.defpath])
    assert child["PRESERVE_VALUE"] == "保持する"
    assert env["VIRTUAL_ENV"] == str(venv)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_public_command_records_exit_modes(tmp_path: pathlib.Path, exit_code: int) -> None:
    """公開コマンドでも終了値と両streamを保存する。"""
    argv = [sys.executable, "-c", f"import sys; print('out'); print('err', file=sys.stderr); sys.exit({exit_code})"]
    result, metadata = _public_command(["--", *argv], tmp_path, dict(os.environ))
    assert result.returncode == exit_code
    assert metadata["argv"] == argv
    assert metadata["cwd"] == str(tmp_path)
    assert metadata["child_exit_code"] == exit_code
    assert metadata["wrapper_exit_code"] == exit_code and metadata["failure"] is None
    assert pathlib.Path(metadata["stdout_path"]).read_text(encoding="utf-8") == "out\n"
    assert pathlib.Path(metadata["stderr_path"]).read_text(encoding="utf-8") == "err\n"


def test_public_command_runs_another_uv_environment(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    """実uvの環境で起動したatkから別プロジェクトのPythonへ到達する。"""
    env = host_environ()
    uv = shutil.which("uv", path=env["PATH"])
    assert uv is not None
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="child"\nversion="0.0.0"\n', encoding="utf-8")
    subprocess.run(
        [uv, "venv", "--offline", "--python", sys.executable, str(project / ".venv")],
        env=env,
        check=True,
        capture_output=True,
        timeout=60,
    )
    plugin = pathlib.Path(run_command.__file__).resolve().parents[2]
    result = subprocess.run(
        [
            uv,
            "run",
            "--project",
            str(plugin),
            "--no-sync",
            "python",
            "-m",
            "agent_toolkit.atk",
            "run-command",
            "--cwd",
            str(project),
            "--",
            uv,
            "run",
            "--project",
            str(project),
            "--no-sync",
            "python",
            "-c",
            "import sys; print(sys.prefix)",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    metadata = json.loads(result.stdout)
    assert pathlib.Path(pathlib.Path(metadata["stdout_path"]).read_text(encoding="utf-8").strip()) == project / ".venv"
    assert "VIRTUAL_ENV" not in pathlib.Path(metadata["stderr_path"]).read_text(encoding="utf-8")


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    *,
    timeout: float | None = None,
    cwd: pathlib.Path | None = None,
) -> tuple[int, dict[str, object], str]:
    directory = tmp_path / "managed"
    directory.mkdir(exist_ok=True)
    for previous in directory.iterdir():
        previous.unlink()
    monkeypatch.setattr(run_command.managed_temp, "create_managed_temp", lambda _prefix: directory)
    args = argparse.Namespace(command_argv=["--", *argv], cwd=(cwd or tmp_path).resolve(), timeout=timeout)
    result = run_command.dispatch(args)
    captured = capsys.readouterr()
    metadata = json.loads(captured.out)
    record = pathlib.Path(metadata["record_path"])
    assert record.is_absolute()
    assert record.parent == directory
    assert json.loads(record.read_text(encoding="utf-8")) == metadata
    return result, metadata, captured.err


def test_success_preserves_streams_and_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    command = [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"]

    result, metadata, stderr = _run(monkeypatch, tmp_path, capsys, command)

    assert result == 0
    assert metadata["argv"] == command
    assert metadata["cwd"] == str(tmp_path.resolve())
    assert metadata["git_head"] is None
    assert metadata["git_status"] is None
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
        [sys.executable, "-c", "import sys; print('before'); print('problem', file=sys.stderr); sys.exit(37)"],
    )

    assert result == 37
    assert metadata["child_exit_code"] == 37
    assert metadata["timed_out"] is False
    assert pathlib.Path(str(metadata["stdout_path"])).read_bytes() == b"before\n"
    assert pathlib.Path(str(metadata["stderr_path"])).read_bytes() == b"problem\n"
    assert stderr.startswith("失敗: 外部コマンドが終了コード37で終了した\n")


def test_short_atk_result_is_available_from_saved_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """短い公開atk照会も保存JSONだけから実行条件と出力へ到達できる。"""
    monkeypatch.setenv("PYTHONPATH", str(pathlib.Path(run_command.__file__).resolve().parents[2]))
    command = [sys.executable, "-m", "agent_toolkit.atk", "config", "get", "state_dir"]
    result, metadata, _stderr = _run(monkeypatch, tmp_path, capsys, command, timeout=60)
    assert result == 0
    saved = json.loads(pathlib.Path(str(metadata["record_path"])).read_text(encoding="utf-8"))
    assert saved["argv"] == command
    assert saved["cwd"] == str(tmp_path.resolve())
    assert saved["child_exit_code"] == 0
    output = pathlib.Path(saved["stdout_path"]).read_text(encoding="utf-8").strip()
    assert output and pathlib.Path(output).is_absolute()
    assert pathlib.Path(saved["stderr_path"]).is_file()


def test_timeout_returns_124_and_keeps_partial_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stdout_path = tmp_path / "managed" / "stdout.bin"

    class _PopenAfterFirstOutput(subprocess.Popen):  # type: ignore[type-arg]
        """子が最初の出力を書いてから上限付きの待機を始め、上限をPython起動時間から切り離す。"""

        def wait(self, timeout: float | None = None) -> int:
            if timeout is not None:
                deadline = time.monotonic() + 60
                while not stdout_path.read_bytes() and time.monotonic() < deadline:
                    time.sleep(0.01)
            return super().wait(timeout=timeout)

    monkeypatch.setattr(run_command.subprocess, "Popen", _PopenAfterFirstOutput)
    result, metadata, _stderr = _run(
        monkeypatch,
        tmp_path,
        capsys,
        [sys.executable, "-c", "import time; print('before', flush=True); time.sleep(30)"],
        timeout=0.05,
    )

    assert result == 124
    assert metadata["wrapper_exit_code"] == 124
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
    assert metadata["wrapper_exit_code"] == 128 + signal.SIGTERM
    assert metadata["child_exit_code"] == -signal.SIGTERM
    assert metadata["signal"] == signal.SIGTERM


def test_start_failure_returns_125_with_empty_saved_streams(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result, metadata, _stderr = _run(monkeypatch, tmp_path, capsys, [str(tmp_path / "missing-command")])

    assert result == 125
    assert metadata["child_exit_code"] is None
    assert metadata["wrapper_exit_code"] == 125 and metadata["failure"]
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

    result = run_command.dispatch(args)
    captured = capsys.readouterr()

    assert result == 125
    assert captured.out == ""
    assert "実行するCOMMANDがありません" in captured.err


def test_record_write_failure_preserves_child_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """記録の保存だけが失敗しても子の終了状態と両出力へ到達できる。"""
    monkeypatch.setattr(run_command.managed_temp, "create_managed_temp", lambda _prefix: tmp_path)
    (tmp_path / "record.json").mkdir()
    args = argparse.Namespace(
        command_argv=["--", sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"],
        cwd=tmp_path.resolve(),
        timeout=30,
    )

    assert run_command.dispatch(args) == 125
    captured = capsys.readouterr()
    metadata = json.loads(captured.out)
    assert metadata["record_path"] is None
    assert metadata["child_exit_code"] == 7
    assert metadata["wrapper_exit_code"] == 125 and "保存できない" in metadata["failure"]
    assert pathlib.Path(metadata["stdout_path"]).read_bytes() == b"out\n"
    assert pathlib.Path(metadata["stderr_path"]).read_bytes() == b"err\n"
    assert "実行結果JSONを保存できない" in captured.err


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_stream_preparation_failure_returns_saved_wrapper_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], stream: str
) -> None:
    """出力先を開けない失敗も例外で終わらず、起動前の失敗と未取得の属性を保存する。"""
    monkeypatch.setattr(run_command.managed_temp, "create_managed_temp", lambda _prefix: tmp_path)
    (tmp_path / f"{stream}.bin").mkdir()
    args = argparse.Namespace(command_argv=["--", sys.executable, "-c", "print('child')"], cwd=tmp_path, timeout=30)
    assert run_command.dispatch(args) == 125
    metadata = json.loads(capsys.readouterr().out)
    assert metadata["child_exit_code"] is None and metadata["wrapper_exit_code"] == 125
    assert metadata["failure"] and metadata[f"{stream}_lines"] is None and metadata[f"{stream}_bytes"] is None
    assert json.loads(pathlib.Path(metadata["record_path"]).read_text(encoding="utf-8")) == metadata


def test_command_without_separator_is_wrapper_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """子argvの先頭がオプションでも曖昧にならないよう、区切り`--`を必須にする。"""
    monkeypatch.setattr(run_command.managed_temp, "create_managed_temp", lambda _prefix: tmp_path / "unused")
    args = argparse.Namespace(command_argv=[sys.executable, "--version"], cwd=tmp_path.resolve(), timeout=None)

    result = run_command.dispatch(args)
    captured = capsys.readouterr()

    assert result == 125
    assert captured.out == ""
    assert "`--`以後" in captured.err


def test_records_worktree_head_and_uncommitted_state_before_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Git作業ツリーで実行すると、起動直前のHEADと未commit・未追跡の状態を保存JSONへ残す。

    観測を後から別の版へ適用できるかは、実行した版と未commitの入力で決まる。HEADだけを残すと、
    未commitの変更を実行した記録を、HEADの内容を実行した結果と取り違える。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo)
    git_repository.git_output(repo, "config", "user.email", "test@example.com")
    git_repository.git_output(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    git_repository.git_output(repo, "add", "tracked.txt")
    git_repository.git_output(repo, "commit", "-qm", "base")
    head = git_repository.git_output(repo, "rev-parse", "HEAD")
    command = [sys.executable, "-c", "print('ok')"]

    result, metadata, stderr = _run(monkeypatch, tmp_path, capsys, command, cwd=repo)
    assert result == 0
    assert metadata["git_head"] == head
    assert metadata["git_status"] == []
    assert stderr == "成功: 外部コマンドが終了した\n"

    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("input\n", encoding="utf-8")
    result, metadata, _stderr = _run(monkeypatch, tmp_path, capsys, command, cwd=repo)
    assert result == 0
    assert metadata["git_head"] == head
    assert metadata["git_status"] == [" M tracked.txt", "?? untracked.txt"]


def test_public_read_many_records_keeps_child_wrapper_outputs_and_never_reruns(
    tmp_path: pathlib.Path,
) -> None:
    """保存済みの成功・失敗・旧記録をそのまま読み、終了状態の役割と両出力を分ける。"""
    counter = tmp_path / "count.txt"
    script = (
        "import pathlib,sys;p=pathlib.Path(sys.argv[1]);"
        "p.write_text(p.read_text()+'x' if p.exists() else 'x');"
        "print('child-out');print('child-err',file=sys.stderr);sys.exit(int(sys.argv[2]))"
    )
    records = []
    for code in (0, 7):
        _result, metadata = _public_command(
            ["--", sys.executable, "-c", script, str(counter), str(code)], tmp_path, dict(os.environ)
        )
        records.append(metadata["record_path"])
    legacy = tmp_path / "legacy.json"
    previous = json.loads(pathlib.Path(records[0]).read_text(encoding="utf-8"))
    previous.pop("wrapper_exit_code")
    previous.pop("failure")
    legacy.write_text(json.dumps(previous), encoding="utf-8")
    bundle = tmp_path / "results.json"
    bundle.write_text(json.dumps({"records": records}), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(pathlib.Path(run_command.__file__).resolve().parents[2])}
    result = subprocess.run(
        [sys.executable, "-m", "agent_toolkit.atk", "run-command", "--records-file", str(bundle), "--record", str(legacy)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    read = json.loads(result.stdout)
    assert not read["errors"] and counter.read_text(encoding="utf-8") == "xx"
    assert [row["child_exit_code"] for row in read["records"]] == [0, 7, 0]
    assert [row["wrapper_exit_code"] for row in read["records"]] == [0, 7, None]
    assert set(read["records"][2]["unrecorded"]) == {"wrapper_exit_code", "failure"}
    assert all(row["stdout"] == "child-out\n" and row["stderr"] == "child-err\n" for row in read["records"])


def test_saved_record_read_reports_corruption_without_starting_child(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """破損した保存記録を終了成功や子の失敗へ変換せず、読取の失敗として返す。"""
    path = tmp_path / "corrupt.json"
    path.write_text("{", encoding="utf-8")
    args = argparse.Namespace(record=[path], records_file=None, command_argv=[], cwd=None, timeout=None)
    assert run_command.dispatch(args) == 125
    saved = json.loads(capsys.readouterr().out)
    assert not saved["records"] and saved["errors"][0]["record_path"] == str(path)


@pytest.mark.parametrize("results", [{}, [{}], [{"record_path": "/absolute/missing.json"}]])
def test_saved_result_bundle_rejects_broken_shape_or_mismatched_order(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], results: object
) -> None:
    """結果まとめの欠損や保存先の不一致を、空の成功結果と区別して拒否する。"""
    path = tmp_path / "corrupt-bundle.json"
    path.write_text(json.dumps({"records": [], "results": results}), encoding="utf-8")
    args = argparse.Namespace(record=[], records_file=path, command_argv=[], cwd=None, timeout=None)
    assert run_command.dispatch(args) == 125
    captured = capsys.readouterr()
    assert not captured.out and "results" in captured.err
