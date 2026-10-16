"""`history-rewrite.md`が指示するautosquashのコマンドを、実Gitの一時リポジトリで実行して確かめる。

環境変数`AGENT_TOOLKIT_TEST_GIT`で最低版のGitを指定すると、同じ手順を最低版で実行できる
（`agent_toolkit/_testing/isolation.py`）。
"""

import os
import pathlib
import re
import shlex

from agent_toolkit._testing import git_repository

_HISTORY_REWRITE = pathlib.Path(__file__).with_name("history-rewrite.md")


def _autosquash_command(base: str) -> list[str]:
    """手順書が指示するautosquashのコマンドを、`<base>`を置き換えた引数列で返す。"""
    found = re.findall(r"`GIT_SEQUENCE_EDITOR=: (git [^`]*<base>)`", _HISTORY_REWRITE.read_text(encoding="utf-8"))
    assert len(found) == 1, found
    return [base if token == "<base>" else token for token in shlex.split(found[0])]


def _refs(repo: pathlib.Path) -> list[str]:
    return git_repository.git_output(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads").splitlines()


def test_autosquash_procedure_keeps_other_local_branches(tmp_path: pathlib.Path) -> None:
    """手順書のautosquashは、ref更新を有効にした設定でも作業branch以外のlocal branch refを動かさない。"""
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="基準")
    base = git_repository.git_output(repo, "rev-parse", "HEAD")
    (repo / "first.txt").write_text("一", encoding="utf-8")
    first = git_repository.commit_all(repo, "一つ目")
    git_repository.run_git(repo, "branch", "stacked", first)
    (repo / "second.txt").write_text("二", encoding="utf-8")
    git_repository.commit_all(repo, "二つ目")
    (repo / "first.txt").write_text("一修正", encoding="utf-8")
    git_repository.commit_all(repo, f"fixup! {first}")
    # Git 2.38以降でref更新を有効にする設定を置き、手順書の抑止が設定より優先されることを確かめる。
    git_repository.run_git(repo, "config", "rebase.updateRefs", "true")
    before = [line for line in _refs(repo) if not line.startswith("refs/heads/main ")]

    git, *argv = _autosquash_command(base)
    assert git == "git"
    git_repository.run_git(repo, *argv, env={**os.environ, "GIT_SEQUENCE_EDITOR": ":", "GIT_EDITOR": "true"})

    assert [line for line in _refs(repo) if not line.startswith("refs/heads/main ")] == before
    assert git_repository.git_output(repo, "log", "--format=%s", f"{base}..HEAD").splitlines() == ["二つ目", "一つ目"]
    assert git_repository.git_output(repo, "show", "HEAD~1:first.txt") == "一修正"
