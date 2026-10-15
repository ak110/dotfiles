"""`plan-verify`が計画の検証を順番に実行し、失敗後も続行して結果を保存し、不正な入力を起動前に拒否することを確かめる。"""

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
    """`--list`では子を起動せず、実行では非0終了とtimeoutの後も残りの子を実行し、保存した版とargvを保持する。"""
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


def test_public_plan_uses_corrected_verification_for_list_and_execution(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """訂正表を明示すると列挙と実行のargvが一致し、凍結原文は保持する。"""
    frozen = _plan(tmp_path / "frozen.md", "`obsolete-command`")
    original = frozen.read_bytes()
    current = _plan(tmp_path / "current.md", f"`{shlex.join([sys.executable, '-c', 'print(123)'])}`")
    options = ["--verification-plan", str(current)]
    assert _run(frozen, tmp_path, *options, "--list") == 0
    listed = json.loads(capsys.readouterr().out)
    assert _run(frozen, tmp_path, *options) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["results"][0]["argv"] == listed[0]["argv"]
    assert result["verification_plan_path"] == str(current)
    assert frozen.read_bytes() == original


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ({"completion": "completed", "missing_targets": []}, 0),
        ({"completion": "incomplete", "missing_targets": ["absent.py"]}, 1),
        ({"completion": "not_reached"}, 1),
        ({"completion": "completed", "incomplete_commands": ["ruff-check"]}, 1),
        ({"completion": "completed", "fully_excluded_files": ["excluded.py"]}, 1),
        ({}, 1),
    ],
)
def test_public_plan_checks_pyfltr_completion_separately_from_exit_code(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    summary: dict,
    expected: int,
) -> None:
    """終了0でも未完了・未到達を失敗へ接続し、汎用コマンドはJSON解析の対象にしない。"""
    output = tmp_path / "stdout.jsonl"
    output.write_text(
        json.dumps({"kind": "summary", "files_reached": 1, "completed_commands": ["ruff-check"], **summary}) + "\n",
        encoding="utf-8",
    )

    def execute(*_args: object, **_kwargs: object) -> tuple[dict, int, None]:
        return {"stdout_path": str(output), "record_path": None, "timed_out": False}, 0, None

    monkeypatch.setattr(run_command, "execute", execute)
    plan = _plan(tmp_path / "plan.md", "`uv run --frozen pyfltr run-for-agent --output-format=jsonl target.py`<br>`true`")
    assert _run(plan, tmp_path) == expected
    result = json.loads(capsys.readouterr().out)
    assert result["results"][0]["state"] == ("success" if expected == 0 else "failure")
    assert result["results"][1]["state"] == "success"


# uv 0.13.1の`uvx --help`・`uv help tool run`・`uv run --help`とPython --helpの起動構文。
# 依存取得は起動せず、外部コマンドの保存結果をplan-verifyが検収する境界を確かめる。
@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize(
    "launcher",
    [
        ["pyfltr"],
        ["uvx", "pyfltr"],
        ["uvx", "--from", "pyfltr", "pyfltr"],
        ["uvx", "--from=pyfltr", "pyfltr"],
        ["uvx", "--with", "pyfltr", "pyfltr"],
        ["uvx", "-w", "pyfltr", "pyfltr"],
        ["uvx", "pyfltr@1.0"],
        ["uv", "tool", "run", "pyfltr"],
        ["uv", "run", "--frozen", "pyfltr"],
        ["uv", "run", "--with", "pyfltr", "pyfltr"],
        ["uv", "run", "--with=pyfltr", "pyfltr"],
        ["uv", "-q", "run", "pyfltr"],
        ["uv", "run", "--module", "pyfltr"],
        ["python", "-m", "pyfltr"],
        ["python", "-I", "-m", "pyfltr"],
        ["python", "-X", "pyfltr", "-m", "pyfltr"],
        ["python", "-mpyfltr"],
        ["uv", "run", "python", "-B", "-m", "pyfltr"],
        ["uvx", "--from", "pyfltr", "python", "-m", "pyfltr"],
    ],
)
def test_public_plan_checks_completion_across_runtime_boundaries(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    launcher: list[str],
    completed: bool,
) -> None:
    """起動表記を変えても、同じ完了結果は同じ合否となる。"""
    output = tmp_path / "stdout.jsonl"
    output.write_text(
        json.dumps(
            {
                "kind": "summary",
                "completion": "completed" if completed else "incomplete",
                "files_reached": 1 if completed else 0,
                "completed_commands": ["pytest"] if completed else [],
                "incomplete_commands": [] if completed else ["pytest"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def execute(argv: list[str], *_args: object, **_kwargs: object) -> tuple[dict, int, None]:
        assert argv == [*launcher, "run", "--output-format=jsonl", "target.py"]
        return {"stdout_path": str(output), "record_path": None, "timed_out": False}, 0, None

    monkeypatch.setattr(run_command, "execute", execute)
    plan = _plan(tmp_path / "plan.md", f"`{shlex.join([*launcher, 'run', '--output-format=jsonl', 'target.py'])}`")
    assert _run(plan, tmp_path) == (0 if completed else 1)
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["state"] == ("success" if completed else "failure")


@pytest.mark.parametrize(
    "argv",
    [
        ["uv", "run", "printf", "%s", "pyfltr", "run"],
        ["uvx", "python", "-c", "pass", "pyfltr", "run"],
        ["python", "-c", "pass", "pyfltr", "run"],
        ["uv", "run", "--help", "pyfltr", "run"],
        ["uvx", "python", "-I"],
        ["uv", "run", "-", "pyfltr", "run"],
        ["pyfltr.txt", "run"],
        ["env", "-i", "printf", "%s", "pyfltr", "run"],
    ],
)
def test_public_plan_keeps_generic_and_nonexecution_commands_successful(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """通常引数のpyfltrという語を実行名にせず、JSONを出さない汎用コマンドも検証できる。"""
    output = tmp_path / "stdout.txt"
    output.write_text("通常のテキスト出力\n", encoding="utf-8")

    def execute(*_args: object, **_kwargs: object) -> tuple[dict, int, None]:
        return {"stdout_path": str(output), "record_path": None, "timed_out": False}, 0, None

    monkeypatch.setattr(run_command, "execute", execute)
    assert _run(_plan(tmp_path / "plan.md", f"`{shlex.join(argv)}`"), tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["state"] == "success"


def test_public_plan_does_not_accept_unresolved_runtime_as_success(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """起動位置が未確定なら、終了0を検収済み成功へ変えず次の操作を示す。"""

    def execute(*_args: object, **_kwargs: object) -> tuple[dict, int, None]:
        return {"stdout_path": None, "record_path": None, "timed_out": False}, 0, None

    monkeypatch.setattr(run_command, "execute", execute)
    assert _run(_plan(tmp_path / "plan.md", "`uvx --unknown tool`"), tmp_path) == 1
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["state"] == "failure" and result["failure"]


@pytest.mark.parametrize("output_option", [True, False])
def test_public_plan_resolves_output_file_only_within_pyfltr_options(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    output_option: bool,
) -> None:
    """uvのcwd変更と、pyfltrのオプション終端以降の対象名を区別する。"""
    directory = tmp_path / "work"
    directory.mkdir()
    (directory / "results.jsonl").write_text(
        json.dumps({"kind": "summary", "completion": "completed", "files_reached": 1, "completed_commands": ["pytest"]}) + "\n",
        encoding="utf-8",
    )
    stdout = tmp_path / "stdout.jsonl"
    stdout.write_text(json.dumps({"kind": "summary", "completion": "incomplete"}) + "\n", encoding="utf-8")

    def execute(*_args: object, **_kwargs: object) -> tuple[dict, int, None]:
        return {"stdout_path": str(stdout), "record_path": None, "timed_out": False}, 0, None

    monkeypatch.setattr(run_command, "execute", execute)
    arguments = ["--output-file", "results.jsonl"] if output_option else ["--", f"--output-file={directory / 'results.jsonl'}"]
    argv = ["uvx", "--directory", str(directory), "pyfltr", "run", *arguments]
    assert _run(_plan(tmp_path / "plan.md", f"`{shlex.join(argv)}`"), tmp_path) == (0 if output_option else 1)
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["state"] == ("success" if output_option else "failure")
