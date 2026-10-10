"""計画からの公開検証操作が順番・失敗後の続行・保存と起動前拒否を満たすことを確かめる。"""

from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import subprocess
import sys
import time

import pytest

from agent_toolkit._atk import run_command, run_script
from agent_toolkit._testing import git_repository


def _run(plan: pathlib.Path, repo: pathlib.Path, *extra: str) -> int:
    return run_script.dispatch(
        argparse.Namespace(
            script_name="plan-verify", script_args=["--plan", str(plan), "--worktree", str(repo), "--timeout", "30", *extra]
        )
    )


def _plan(path: pathlib.Path, cell: str) -> pathlib.Path:
    path.write_text("## 検証\n\n| 区分 | 検証コマンド |\n| --- | --- |\n| 変更範囲の検証 | " + cell + " |\n", encoding="utf-8")
    return path


def test_public_plan_executes_all_commands_and_unwraps_recording(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """列挙は子未起動、非0とtimeoutの後にも子を実行し、保存版とargvを保持する。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo)
    git_repository.git_output(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "base"
    )
    head = git_repository.git_output(repo, "rev-parse", "HEAD")
    script = tmp_path / "child.py"
    script.write_text(
        "import sys, time\nfrom pathlib import Path\np=Path(sys.argv[1])\n"
        "p.write_text(p.read_text() + sys.argv[2] if p.exists() else sys.argv[2])\n"
        "print(sys.argv[2], flush=True)\nif sys.argv[2] == 'wait': time.sleep(30)\n"
        "sys.exit(7 if sys.argv[2] == 'fail' else 0)\n",
        encoding="utf-8",
    )
    log = tmp_path / "ordered.txt"

    class _PopenAfterReady(subprocess.Popen):  # type: ignore[type-arg]
        """待機する子の準備完了を確かめ、起動時間とは別にtimeoutを観測する。"""

        def wait(self, timeout: float | None = None) -> int:
            if timeout == 0.05:
                deadline = time.monotonic() + 30
                while "wait" not in log.read_text(encoding="utf-8") and time.monotonic() < deadline:
                    time.sleep(0.01)
            return super().wait(timeout=timeout)

    monkeypatch.setattr(run_command.subprocess, "Popen", _PopenAfterReady)
    commands = [shlex.join([sys.executable, str(script), str(log), value]) for value in ("with space", "fail", "wait", "last")]
    commands[2] = f"atk run-command --cwd {shlex.quote(str(repo))} --timeout 0.05 -- {commands[2]}"
    plan = _plan(tmp_path / "plan.md", "<br>".join(f"`{command}`" for command in commands))
    assert _run(plan, repo, "--list") == 0
    assert len(json.loads(capsys.readouterr().out)) == 4
    assert not log.exists()
    assert _run(plan, repo) == 1
    saved = json.loads(capsys.readouterr().out)
    results = saved["results"]
    assert [row["state"] for row in results] == ["success", "failure", "timeout", "success"]
    assert [row["exit_code"] for row in results] == [0, 7, 124, 0]
    assert log.read_text(encoding="utf-8") == "with spacefailwaitlast"
    for result in results:
        record = json.loads(pathlib.Path(result["record_path"]).read_text(encoding="utf-8"))
        assert record["git_head"] == head and record["git_status"] == []
        assert record["argv"][0] == sys.executable and record["argv"] == result["argv"]
        assert pathlib.Path(record["stdout_path"]).is_file() and pathlib.Path(record["stderr_path"]).is_file()
    assert pathlib.Path(saved["results_path"]).is_file()
    assert saved["records"] == [row["record_path"] for row in results] and not saved["unrecorded"]
    assert (
        run_command.dispatch(
            argparse.Namespace(
                records_file=pathlib.Path(saved["results_path"]), record=None, command_argv=[], cwd=None, timeout=None
            )
        )
        == 0
    )
    read = json.loads(capsys.readouterr().out)
    assert [row["wrapper_exit_code"] for row in read["records"]] == [0, 7, 124, 0]
    assert read["records"][2]["timed_out"]
    assert log.read_text(encoding="utf-8") == "with spacefailwaitlast"


@pytest.mark.parametrize(
    "invalid", ["説明 `true`", "`true`<br>`echo x \\| cat`", "`true`<br>`X=1 true`", "`true`<br>`'broken`", ""]
)
def test_public_plan_rejects_all_input_before_any_child(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """後続行の不正も先行コマンドの起動前に拒否する。"""
    marker = tmp_path / "marker"
    first = shlex.join([sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)])
    plan = _plan(tmp_path / "plan.md", f"`{first}`<br>{invalid}")
    # 空だけの入力は先頭を足さず、空の検証行として拒否する。
    if not invalid:
        plan = _plan(plan, "")
    assert _run(plan, tmp_path) == 2
    assert not marker.exists()
    assert "次の操作:" in capsys.readouterr().err
