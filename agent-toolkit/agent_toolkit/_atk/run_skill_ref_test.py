"""報告UWIの同期判定がGitの参照名前空間の衝突によらず成立することを確かめる。"""

import datetime
import pathlib

import pytest

from agent_toolkit._atk.wi import add as wi_add
from agent_toolkit._testing import git_repository
from agent_toolkit.atk_test import _setup_notes


@pytest.mark.parametrize("same_name_tag", [False, True])
def test_synced_report_with_branch_tag_collision(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, same_name_tag: bool
) -> None:
    """追跡設定とpush済み報告を持つbranchへ同名tagを加えても保存到達を確認できる。"""
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes, files={"README.md": "報告\n"}, commit_message="初期状態")
    git_repository.run_git(notes, "checkout", "-b", "feature/reports")
    remote = git_repository.init_bare_repository(tmp_path / "origin.git")
    git_repository.run_git(notes, "remote", "add", "origin", str(remote))
    git_repository.run_git(notes, "push", "-u", "origin", "HEAD")
    monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", "run-namespace")
    wi_add.add_entries(
        notes,
        messages=["---\nrun_skill_report: true\n---\n実行結果を確認してください。"],
        target_repo="github.com/example/repo",
        source="run-skill",
        now=datetime.datetime(2026, 10, 18),
        entry_type="uwi",
        scope="my-skill",
        question_type="post-approval",
    )
    if same_name_tag:
        git_repository.run_git(notes, "tag", "feature/reports")
    assert wi_add.has_synced_run_report(notes, run_id="run-namespace", skill="my-skill")
