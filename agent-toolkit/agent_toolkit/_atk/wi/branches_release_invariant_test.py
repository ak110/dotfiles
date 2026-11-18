"""公開手順に書いたリリース前の取り込み元の確認が、未取り込みのcommitを検出することを確かめる。"""

import pathlib
import re

import pytest

from agent_toolkit._testing import git_repository as git

_PUBLISH = pathlib.Path(__file__).resolve().parents[3] / "skills/commit/references/publish.md"


@pytest.mark.cpu_limit(
    1.5, reason="bare originへ2ブランチをpushし、公開手順の確認コマンドを実Gitで実行する（Linux CPU合計約0.5秒）。"
)
def test_release_merge_check_detects_unmerged_commits(tmp_path: pathlib.Path) -> None:
    """取り込み元にだけあるcommitを、手順の2行を置換してそのまま実行すると検出し、マージ後は検出しない。"""
    origin = git.init_bare_repository(tmp_path / "origin.git")
    seed = git.init_repository(tmp_path / "seed", initial_branch="main", origin=str(origin), commit_message="base")
    git.run_git(seed, "push", "--quiet", "-u", "origin", "main")
    git.run_git(seed, "switch", "--quiet", "-c", "release")
    git.commit_all(seed, "保守ブランチだけの修正")
    git.run_git(seed, "push", "--quiet", "-u", "origin", "release")
    git.run_git(seed, "switch", "--quiet", "main")
    repo = tmp_path / "repo"
    git.run_git(tmp_path, "clone", "--quiet", str(origin), str(repo))
    fences = re.findall(r"```sh\n(.*?)```", _PUBLISH.read_text(encoding="utf-8"), flags=re.DOTALL)
    (fence,) = [block for block in fences if "origin/<取り込む側>..origin/<取り込み元>" in block]
    replacements = {"<対象リポジトリの絶対パス>": str(repo), "<取り込む側>": "main", "<取り込み元>": "release"}

    def run_procedure() -> str:
        output = ""
        for line in fence.strip().splitlines():
            for placeholder, value in replacements.items():
                line = line.replace(placeholder, value)
            command = line.split()
            assert command[:3] == ["git", "-C", str(repo)]
            output = git.git_output(repo, *command[3:])
        return output

    assert "保守ブランチだけの修正" in run_procedure()
    git.run_git(seed, "merge", "--quiet", "--no-edit", "origin/release")
    git.run_git(seed, "push", "--quiet")
    assert run_procedure() == ""
