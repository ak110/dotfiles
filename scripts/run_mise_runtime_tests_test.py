"""runtimeの解決先、pytest引数と前提失敗の受け渡しを公開CLIから確かめる。"""

import json
import os
import pathlib
import subprocess
import sys
from unittest.mock import Mock

import pytest
import run_mise_runtime_tests


def _executable(directory: pathlib.Path) -> pathlib.Path:
    directory.mkdir()
    executable = directory / ("runtime-probe.exe" if sys.platform == "win32" else "runtime-probe")
    executable.write_bytes(b"runtime probe")
    executable.chmod(0o755)
    return executable


def test_cli_uses_shim_and_preserves_pytest_arguments_and_exit_code(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """uvが先に置いたruntimeよりshimを優先し、同じプロセスのpytestへ元の引数と終了状態を渡す。"""
    shim = _executable(tmp_path / "shims")
    other = _executable(tmp_path / "other")
    monkeypatch.setenv("PATH", str(other.parent))
    doctor = subprocess.CompletedProcess(["mise", "doctor", "--json"], 0, json.dumps({"dirs": {"shims": str(shim.parent)}}), "")
    run = Mock(return_value=doctor)
    monkeypatch.setattr(run_mise_runtime_tests.subprocess, "run", run)
    received: list[list[str]] = []

    def _pytest_main(args: list[str]) -> int:
        received.append(args)
        assert os.environ["PATH"].split(os.pathsep)[0] == str(shim.parent)
        return 5

    monkeypatch.setattr(run_mise_runtime_tests.pytest, "main", _pytest_main)

    assert run_mise_runtime_tests.main(["runtime-probe", "--", "path with spaces/test.py", "-k", "alpha or beta"]) == 5
    assert received == [["-v", "-n", "0", "-p", "no:cacheprovider", "path with spaces/test.py", "-k", "alpha or beta"]]
    assert run.call_args.args[0] == ["mise", "doctor", "--json"]
    assert os.environ["PATH"] == str(other.parent)
    assert str(shim) in capsys.readouterr().out


@pytest.mark.parametrize("fallback", [False, True], ids=["missing-runtime", "outside-shims"])
def test_cli_rejects_runtime_outside_shims_before_pytest(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fallback: bool
) -> None:
    """shimの欠落時は別のruntimeも試験へ渡さず、解決先と次の操作を示す。"""
    shims = tmp_path / "shims"
    shims.mkdir()
    old_path = str(_executable(tmp_path / "other").parent) if fallback else str(tmp_path / "missing")
    monkeypatch.setenv("PATH", old_path)
    doctor = subprocess.CompletedProcess(["mise"], 0, json.dumps({"dirs": {"shims": str(shims)}}), "")
    monkeypatch.setattr(run_mise_runtime_tests.subprocess, "run", Mock(return_value=doctor))
    pytest_main = Mock()
    monkeypatch.setattr(run_mise_runtime_tests.pytest, "main", pytest_main)

    assert run_mise_runtime_tests.main(["runtime-probe", "--", "target.py"]) == 2

    pytest_main.assert_not_called()
    assert os.environ["PATH"] == old_path
    diagnostic = capsys.readouterr().err
    assert "shimへ解決されない" in diagnostic
    assert "runtime設定とshimを確認" in diagnostic


@pytest.mark.parametrize(
    "doctor",
    [
        subprocess.CompletedProcess(["mise"], 1, "照会結果", "設定エラー"),
        subprocess.CompletedProcess(["mise"], 0, "invalid json", ""),
        subprocess.CompletedProcess(["mise"], 0, '{"dirs": {}}', ""),
        subprocess.CompletedProcess(["mise"], 0, '{"dirs": {"shims": "relative"}}', ""),
        FileNotFoundError("mise"),
    ],
    ids=["doctor-failed", "invalid-json", "missing-shims", "relative-shims", "mise-missing"],
)
def test_cli_reports_mise_failures_without_starting_pytest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], doctor: subprocess.CompletedProcess[str] | OSError
) -> None:
    """miseの起動・応答の失敗を試験結果と分け、診断を保持して終了する。"""
    run = Mock(side_effect=doctor) if isinstance(doctor, OSError) else Mock(return_value=doctor)
    monkeypatch.setattr(run_mise_runtime_tests.subprocess, "run", run)
    pytest_main = Mock()
    monkeypatch.setattr(run_mise_runtime_tests.pytest, "main", pytest_main)

    assert run_mise_runtime_tests.main(["runtime-probe", "--", "target.py"]) == 2

    pytest_main.assert_not_called()
    diagnostic = capsys.readouterr().err
    assert "mise doctor" in diagnostic
    if isinstance(doctor, subprocess.CompletedProcess) and doctor.returncode:
        assert "照会結果" in diagnostic and "設定エラー" in diagnostic
