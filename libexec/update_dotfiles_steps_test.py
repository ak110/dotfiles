# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesのstepsの試験。"""

# pylint: disable=protected-access

import os
import pathlib
import subprocess
import sys
from typing import cast

import psutil
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_stages  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _INTERLEAVED_CHILD_CODE,
    _fake_run,
)
from update_dotfiles_test_helpers import _isolate_sync_report as _isolate_sync_report  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _logs_environment as _logs_environment  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _no_codex_processes as _no_codex_processes  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _own_git_process_scan as _own_git_process_scan  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _real_stash_runtime as _real_stash_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _separate_git_recovery as _separate_git_recovery  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _skip_agent_toolkit_prepare as _skip_agent_toolkit_prepare  # noqa: E402  # pylint: disable=wrong-import-position


def test_run_step_passes_declared_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """chezmoi工程へは宣言表の待機上限を渡し、上限の無い段にはNoneを渡す。"""
    timeouts: list[float | None] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeouts.append(cast(float | None, kwargs["timeout"]))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    for stage in update_dotfiles_stages.STAGES:
        if stage.where == "executor" and stage.name.startswith("chezmoi"):
            assert update_dotfiles._run_step(1, 1, stage.name, ["chezmoi", "x"], timeout=stage.timeout_sec)[0] == 0  # pylint: disable=protected-access
            assert timeouts[-1] == stage.timeout_sec
            assert stage.timeout_sec is not None
    assert update_dotfiles._run_step(1, 1, "test", ["chezmoi", "x"])[0] == 0  # pylint: disable=protected-access
    assert timeouts[-1] is None


def test_run_step_timeout_kills_descendants_and_fails(tmp_path: pathlib.Path) -> None:
    """待機上限を超えた段は、親を失った孫も回収して失敗（終了コード1）にし、再試行対象から外す。"""
    marker = tmp_path / "grandchild.pid"
    grandchild_code = f"import os,time,pathlib; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
    code = f"import subprocess, sys, time\np = subprocess.Popen([sys.executable, '-c', {grandchild_code!r}])\ntime.sleep(60)\n"
    update_dotfiles_runtime.last_step_timed_out = False
    returncode, _ = update_dotfiles._run_step(1, 1, "slow", [sys.executable, "-c", code], timeout=2)  # pylint: disable=protected-access
    assert returncode == 1
    assert update_dotfiles_runtime.last_step_timed_out is True
    grandchild = int(marker.read_text(encoding="utf-8"))
    assert not psutil.pid_exists(grandchild) or psutil.Process(grandchild).status() == psutil.STATUS_ZOMBIE


def test_run_step_disables_mise_auto_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """工程2以降のサブプロセス起動が`MISE_AUTO_INSTALL=0`を含む環境で実行される。"""
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls, environments=environments))

    returncode, _output = update_dotfiles._run_step(2, 4, "test", ["chezmoi", "init"])  # pylint: disable=protected-access

    assert returncode == 0
    assert environments[0]["MISE_AUTO_INSTALL"] == "0"


def test_run_step_decodes_captured_output_as_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """工程2以降の取得出力をUTF-8でデコードする。"""
    calls: list[list[str]] = []
    encodings: list[str | None] = []
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls, encodings=encodings))

    returncode, _output = update_dotfiles._run_step(  # pylint: disable=protected-access
        3,
        4,
        "test",
        ["chezmoi", "status"],
        capture=True,
    )

    assert returncode == 0
    assert encodings == ["utf-8"]


def test_run_step_decodes_utf8_bytes(capsys: pytest.CaptureFixture[str]) -> None:
    """CP932ではデコードできないUTF-8出力を文字列として転送する。"""
    code = "import sys; sys.stdout.buffer.write('日本語—'.encode('utf-8')); sys.stderr.buffer.write('警告—'.encode('utf-8'))"

    returncode, output = update_dotfiles._run_step(  # pylint: disable=protected-access
        3,
        4,
        "test",
        [sys.executable, "-c", code],
        capture=True,
    )

    assert returncode == 0
    assert output == "日本語—"
    assert capsys.readouterr().err == "警告—"


@pytest.mark.parametrize("exit_code", [0, 7])
def test_run_step_preserves_exit_and_non_utf8_diagnostic(capsys: pytest.CaptureFixture[str], exit_code: int) -> None:
    code = "import sys; sys.stderr.buffer.write(b'diagnostic: \\x93'); sys.exit(int(sys.argv[1]))"

    returncode, output = update_dotfiles._run_step(  # pylint: disable=protected-access
        3, 4, "test", [sys.executable, "-c", code, str(exit_code)], capture=True
    )

    assert returncode == exit_code
    assert output == ""
    assert "diagnostic: �" in capsys.readouterr().err
    assert update_dotfiles_runtime.last_stderr_tail == "diagnostic: �"  # pylint: disable=protected-access


@pytest.mark.parametrize("exit_code", [0, 4])
def test_run_step_streams_interleaved_child_output_in_write_order(capfd: pytest.CaptureFixture[str], exit_code: int) -> None:
    """子が交互に書いた出力のストリームを分離し、終了コードを保持する。"""

    returncode, output = update_dotfiles._run_step(  # pylint: disable=protected-access
        3, 5, "chezmoi apply", [sys.executable, "-c", _INTERLEAVED_CHILD_CODE, str(exit_code)]
    )

    captured = capfd.readouterr()
    assert returncode == exit_code
    assert output == ""
    assert captured.out.splitlines() == ["=== [3/5] chezmoi apply ===", "out1", "out2"]
    assert captured.err.splitlines() == ["err1", "err2"]
    assert update_dotfiles_runtime.last_stderr_tail is None


def test_child_env_preserves_existing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """既存の環境変数が保持される。"""
    monkeypatch.setenv("UPDATE_DOTFILES_TEST_SENTINEL", "preserved")

    environment = update_dotfiles_runtime.child_env()  # pylint: disable=protected-access

    assert environment["UPDATE_DOTFILES_TEST_SENTINEL"] == "preserved"
    assert environment["MISE_AUTO_INSTALL"] == "0"
    assert str(pathlib.Path.home() / ".local" / "bin") in environment["PATH"].split(os.pathsep)


def test_inherited_streams_preserve_combined_write_order() -> None:
    """端末と同じ出力先を使う場合、子の交互出力は見出しの後へ書込順で届く。"""
    code = (
        "import sys, update_dotfiles; "
        f"rc, _ = update_dotfiles._run_step(3, 5, 'apply', [sys.executable, '-c', {_INTERLEAVED_CHILD_CODE!r}, '4']); "
        "raise SystemExit(rc)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=pathlib.Path(__file__).resolve().parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 4
    assert result.stdout.splitlines() == ["=== [3/5] apply ===", "out1", "err1", "out2", "err2"]
