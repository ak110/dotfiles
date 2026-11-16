"""完了報告の省略方針が、主/入れ子の登録に応じて規範の適用範囲を判定することを確かめる。

実Gitの登録形式はworktrees_test.pyが検証する。本試験は所属一覧を入力にし、
Windowsの実パスを使った規範本文の読取と、入れ子の規範の拒否を検証する。
"""

import pathlib

import pytest

from agent_toolkit._git import worktrees
from agent_toolkit._hooks import termination_evidence, termination_evidence_decisions

pytestmark = pytest.mark.windows


def test_omission_policy_accepts_main_root_policy_and_rejects_nested_worktree_policy(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """主作業ツリー直下の規範を所属内として受理し、入れ子worktreeの規範は拒否する。"""
    repository = tmp_path / "主 repo"
    nested = repository / ".claude/worktrees/nested"
    nested.mkdir(parents=True)
    for root in (repository, nested):
        (root / "CLAUDE.md").write_text("完了報告を省く。\n", encoding="utf-8")
    entries = [
        worktrees.Worktree(
            path=root,
            branch=branch,
            head_ref=f"refs/heads/{branch}",
            detached=False,
            bare=False,
            locked=False,
            prunable=False,
        )
        for root, branch in ((repository, "main"), (nested, "nested"))
    ]
    monkeypatch.setattr(termination_evidence_decisions, "_git_root", lambda *_args, **_kwargs: repository)
    monkeypatch.setattr(worktrees, "list_worktrees", lambda *_args, **_kwargs: entries)
    work = {"deliverables": ["commit"], "repositories": [str(repository)]}
    document = {"policy_file": str(repository / "CLAUDE.md"), "target_repo": str(repository)}
    assert termination_evidence._omission_policy(document, work) == "完了報告を省く。\n"  # pylint: disable=protected-access
    document["policy_file"] = str(nested / "CLAUDE.md")
    with pytest.raises(ValueError, match="policy_fileは対象リポジトリ内"):
        termination_evidence._omission_policy(document, work)  # pylint: disable=protected-access
