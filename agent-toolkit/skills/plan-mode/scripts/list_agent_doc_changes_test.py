"""`atk run-script agent-doc-changes`の結合テスト。"""

import argparse
import json
import pathlib
import subprocess

import pytest

from agent_toolkit._atk import run_script


def _git(repository: pathlib.Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "HOME": str(repository),
        },
    )
    return completed.stdout.strip()


def _commit_all(repository: pathlib.Path, message: str) -> str:
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = run_script.dispatch(argparse.Namespace(script_name="agent-doc-changes", script_args=["--", *args]))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture(name="repository")
def _repository(tmp_path: pathlib.Path) -> pathlib.Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q")
    _write(repository / "README.md", "初版\n")
    _write(repository / "agent-toolkit" / "share" / "old.subagent.md", "旧\n")
    return repository


def test_lists_only_agent_documents_changed_between_revisions(
    repository: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """規範文書の追加・変更・削除・改名だけを重複なしの昇順で返し、通常コードと利用者向け文書を含めない。"""
    base = _commit_all(repository, "base")
    _write(repository / "AGENTS.md", "規範\n")
    _write(repository / "agent-toolkit" / "skills" / "search" / "SKILL.md", "検索\n")
    _write(repository / "README.md", "改訂\n")
    _write(repository / "agent-toolkit" / "agent_toolkit" / "tool.py", "VALUE = 1\n")
    (repository / "agent-toolkit" / "share" / "old.subagent.md").rename(
        repository / "agent-toolkit" / "share" / "new.subagent.md"
    )
    target = _commit_all(repository, "change")

    code, out, err = _run(capsys, "--repo", str(repository), base, target)

    assert (code, err) == (0, "")
    assert json.loads(out) == [
        "AGENTS.md",
        "agent-toolkit/share/new.subagent.md",
        "agent-toolkit/share/old.subagent.md",
        "agent-toolkit/skills/search/SKILL.md",
    ]


def test_prints_empty_array_without_agent_document_changes(
    repository: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """該当が無い場合は空配列を返す。"""
    base = _commit_all(repository, "base")
    _write(repository / "README.md", "改訂\n")
    target = _commit_all(repository, "change")

    assert _run(capsys, "--repo", str(repository), base, target) == (0, "[]\n", "")


def test_unknown_revision_fails_with_reason(repository: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """解決できないrevisionは標準エラーへ理由を書いて非0で終了する。"""
    base = _commit_all(repository, "base")

    code, out, err = _run(capsys, "--repo", str(repository), base, "no-such-revision")

    assert code == 1
    assert out == ""
    assert err.startswith("失敗: ")
