"""計画・選定・履歴比較の試験で共有する入力と補助処理。"""

import os
import pathlib
import subprocess

import pytest

from agent_toolkit import atk


def _git(repo: pathlib.Path, *argv: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=履歴試験", "-c", "user.email=test@example.com", "-C", str(repo), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env={**os.environ, "GIT_SEQUENCE_EDITOR": "true", "GIT_EDITOR": "true"},
        timeout=30,
    )
    return result.stdout.strip()


def _commit(repo: pathlib.Path, filename: str, text: str, subject: str) -> str:
    (repo / filename).write_text(text, encoding="utf-8")
    _git(repo, "add", "--", filename)
    _git(repo, "commit", "--no-verify", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture(name="repo")
def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _commit(repo, "base.txt", "基準", "基準")
    return repo


def _compare(repo: pathlib.Path, output: pathlib.Path, operation: str, bases: tuple[str, str, str, str], *extra: str) -> int:
    argv = ["run-script", "history-compare", "--", "--operation", operation, "--work-dir", str(repo), "--output", str(output)]
    for name, value in zip(("old-base", "old-head", "new-base", "new-head"), bases, strict=True):
        argv.extend((f"--{name}", value))
    argv.extend(extra)
    with pytest.raises(SystemExit) as raised:
        atk.main(argv)
    assert isinstance(raised.value.code, int)
    return raised.value.code


def _rewrite_records(
    repo: pathlib.Path, previous: str, mapping: pathlib.Path, plan: pathlib.Path, *handoffs: pathlib.Path
) -> int:
    argv = [
        "run-script",
        "plan-rewrite",
        "--",
        "--worktree",
        str(repo),
        "--previous-head",
        previous,
        "--rewrite-map",
        str(mapping),
        "--completed-step",
        "履歴検収",
        "--result",
        "公開対応表を消費",
        "--plan",
        str(plan),
    ]
    for handoff in handoffs:
        argv.extend(("--handoff", str(handoff)))
    with pytest.raises(SystemExit) as ended:
        atk.main(argv)
    assert isinstance(ended.value.code, int)
    return ended.value.code


def _commit_files(repo: pathlib.Path, files: dict[str, str], subject: str) -> str:
    for filename, text in files.items():
        (repo / filename).write_text(text, encoding="utf-8")
    _git(repo, "add", "--", *files)
    _git(repo, "commit", "--no-verify", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")
