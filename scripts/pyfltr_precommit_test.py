"""commitのhookがpytestを再利用する条件と、再利用しない場合の全体実行を確かめる。"""

import datetime
import json
import pathlib
import subprocess
from typing import Any

import pyfltr_precommit
import pytest
from agent_toolkit._testing.git_repository import commit_all, init_repository

_VERSION = "3.30.1"
# 整形段がファイルを書き直す実行を表すため、実行の開始は作業ツリーの変更より前に置く。
_RUN_STARTED_AT = "2000-01-01T00:00:00+00:00"


def _repository(tmp_path: pathlib.Path) -> pathlib.Path:
    repository = init_repository(tmp_path / "repo")
    (repository / "src").mkdir()
    (repository / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    commit_all(repository, "init")
    return repository


def _iso(offset_seconds: float) -> str:
    return (datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=offset_seconds)).isoformat()


def _install_pyfltr(
    monkeypatch: pytest.MonkeyPatch,
    repository: pathlib.Path,
    *,
    pytest_started_at: str,
    argv: list[str] | None = None,
    cwd: pathlib.Path | None = None,
    version: str = _VERSION,
    pytest_status: str = "succeeded",
    list_returncode: int = 0,
) -> list[list[str]]:
    """pyfltrの照会コマンドの公開出力を差し替え、呼び出された引数を記録する。"""
    calls: list[list[str]] = []
    run = {
        "run_id": "01RUN",
        "started_at": _RUN_STARTED_AT,
        "finished_at": _iso(120),
        "exit_code": 0,
        "commands": ["ruff-check", "pytest"],
    }
    shown = {
        "run_id": "01RUN",
        "meta": {
            "cwd": str(cwd or repository),
            "version": version,
            "argv": argv if argv is not None else ["run", "--no-fix", "src/a.py"],
            "started_at": _RUN_STARTED_AT,
        },
        "commands": [
            {"command": "ruff-check", "status": "succeeded"},
            {"command": "pytest", "status": pytest_status, "started_at": pytest_started_at},
        ],
    }

    def _fake(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(cmd[1:])
        if cmd[1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, f"pyfltr {_VERSION}\n", "")
        if cmd[1] == "list-runs":
            return subprocess.CompletedProcess(
                cmd, list_returncode, json.dumps({"runs": [run]}), "broken" if list_returncode else ""
            )
        return subprocess.CompletedProcess(cmd, 0, json.dumps(shown), "")

    monkeypatch.setattr(pyfltr_precommit, "run_subprocess", _fake)
    return calls


def test_reuses_successful_pytest_on_unchanged_tree(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pytestの開始後に作業ツリーが変わっていなければ、整形段より前の実行開始によらずpytestを再利用する。"""
    repository = _repository(tmp_path)
    _install_pyfltr(
        monkeypatch, repository, pytest_started_at=_iso(60), argv=["run", "--output-format", "jsonl", "--commands=pytest"]
    )

    assert pyfltr_precommit.find_reusable_run(repository, "pyfltr") == pyfltr_precommit.Reuse("01RUN", "")


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"pytest_started_at": _iso(-3600)}, id="changed-after-pytest-start"),
        pytest.param({"pytest_status": "failed"}, id="pytest-failed"),
        pytest.param({"version": "3.29.0"}, id="other-version"),
        pytest.param({"argv": ["run", "--pytest-args=-k one"]}, id="narrowed-pytest"),
        pytest.param({"argv": ["run", "--only-failed"]}, id="only-failed"),
        pytest.param({"argv": ["run", "--commands"]}, id="option-without-value"),
        pytest.param({"argv": ["grep", "x"]}, id="other-subcommand"),
        pytest.param({"cwd_other": True}, id="other-worktree"),
        pytest.param({"list_returncode": 1}, id="query-failed"),
    ],
)
def test_runs_pytest_unless_every_condition_holds(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    """条件のどれかを満たさない実行や、照会の失敗では再利用しない。"""
    repository = _repository(tmp_path)
    options: dict[str, Any] = {"pytest_started_at": _iso(60)}
    if overrides.pop("cwd_other", False):
        options["cwd"] = tmp_path
    options.update(overrides)
    _install_pyfltr(monkeypatch, repository, **options)

    reuse = pyfltr_precommit.find_reusable_run(repository, "pyfltr")

    assert reuse.run_id is None
    assert reuse.reason


@pytest.mark.parametrize(
    "change",
    [
        pytest.param("modify", id="unstaged-modification"),
        pytest.param("delete", id="unstaged-deletion"),
    ],
)
def test_worktree_differing_from_index_is_not_reused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """commit対象のindexと作業ツリーが異なる場合は、pyfltrを照会せずに全体を実行する。"""
    repository = _repository(tmp_path)
    target = repository / "src" / "a.py"
    if change == "modify":
        target.write_text("x = 2\n", encoding="utf-8")
    else:
        target.unlink()
    calls = _install_pyfltr(monkeypatch, repository, pytest_started_at=_iso(60))

    reuse = pyfltr_precommit.find_reusable_run(repository, "pyfltr")

    assert reuse.run_id is None
    assert not calls


@pytest.mark.parametrize(
    ("reuse", "expected_extra"),
    [
        (pyfltr_precommit.Reuse("01RUN", ""), ["--disable", "pytest"]),
        (pyfltr_precommit.Reuse(None, "理由"), []),
    ],
)
def test_main_disables_only_pytest_when_reusing(
    monkeypatch: pytest.MonkeyPatch, reuse: pyfltr_precommit.Reuse, expected_extra: list[str]
) -> None:
    """再利用する場合だけpytestを外し、他のチェックと終了コードはpyfltrのものを返す。"""
    launched: list[list[str]] = []

    def _find(directory: pathlib.Path, pyfltr: str) -> pyfltr_precommit.Reuse:
        del directory, pyfltr
        return reuse

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        launched.append(cmd)
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(pyfltr_precommit.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(pyfltr_precommit, "find_reusable_run", _find)
    monkeypatch.setattr(pyfltr_precommit.subprocess, "run", _run)

    assert pyfltr_precommit.main(["src/a.py"]) == 3
    assert launched == [["/bin/pyfltr", "fast", *expected_extra, "src/a.py"]]
