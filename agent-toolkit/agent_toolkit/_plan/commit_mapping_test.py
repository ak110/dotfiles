"""進捗記録のAWI対応と履歴変更の継承を実Gitで検証する。"""

import json
import pathlib
import subprocess

import pytest

from agent_toolkit._plan import commit_mapping

WI_A = "20261004-044311-001.md"
WI_B = "20261004-044247-001.md"


def git(repo: pathlib.Path, *args: str) -> str:
    """隔離されたrepoでGitを実行する。"""
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=30).stdout.strip()


@pytest.fixture(name="repo")
def git_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """現在のworktreeに作用しない一時repoを作成する。"""
    git(tmp_path, "init")
    git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "first")
    return tmp_path


def test_many_to_many_roundtrip_and_rewrite(repo: pathlib.Path) -> None:
    """複数AWIのcommitと複数commitのAWIを保ち、amend後は新OIDだけを取得する。"""
    first = git(repo, "rev-parse", "HEAD")
    content = commit_mapping.encode_event(commit_mapping.commit_event(repo, first, [WI_A, WI_B], {WI_A, WI_B}))
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "second")
    old = git(repo, "rev-parse", "HEAD")
    content += "\n" + commit_mapping.encode_event(commit_mapping.commit_event(repo, old, [WI_A], {WI_A, WI_B}))
    assert commit_mapping.get_commits(repo, content, [WI_A, WI_B], {WI_A, WI_B}) == {WI_A: [first, old], WI_B: [first]}
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--amend",
        "--allow-empty",
        "-m",
        "rewritten",
    )
    new = git(repo, "rev-parse", "HEAD")
    replacements = repo / "rewrite.json"
    replacements.write_text(json.dumps({old: new}), encoding="utf-8")
    event = commit_mapping.rewrite_event(repo, replacements, commit_mapping.read_mapping(content, {WI_A, WI_B}))
    content += "\n" + commit_mapping.encode_event(event)
    assert commit_mapping.get_commits(repo, content, [WI_A, WI_B], {WI_A, WI_B}) == {WI_A: [first, new], WI_B: [first]}


@pytest.mark.parametrize("failure", ["missing", "outside", "oid", "malformed", "unknown-rewrite"])
def test_rejects_incomplete_records(repo: pathlib.Path, failure: str) -> None:
    """対象名と補完操作を伴う失敗を返し、推測した対応を返さない。"""
    oid = git(repo, "rev-parse", "HEAD")
    content = {
        "missing": "自由記述のみ",
        "outside": commit_mapping.encode_event({"commit": oid, "awi": [WI_B]}),
        "oid": commit_mapping.encode_event({"commit": "a" * 40, "awi": [WI_A]}),
        "malformed": commit_mapping.PREFIX + "{broken" + commit_mapping.SUFFIX,
        "unknown-rewrite": commit_mapping.encode_event({"rewrite": {"b" * 40: oid}}),
    }[failure]
    with pytest.raises(commit_mapping.CommitMappingError) as raised:
        commit_mapping.get_commits(repo, content, [WI_A], {WI_A})
    assert raised.value.reason
    assert "実装担当" in raised.value.next_action


def test_rejects_unrecorded_rewrite_even_if_old_object_exists(repo: pathlib.Path) -> None:
    """旧OIDがobjectとして残っていても現在のHEADのcommitへ代用しない。"""
    old = git(repo, "rev-parse", "HEAD")
    content = commit_mapping.encode_event(commit_mapping.commit_event(repo, old, [WI_A], {WI_A}))
    git(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--amend", "--allow-empty", "-m", "new"
    )
    with pytest.raises(commit_mapping.CommitMappingError, match="現在のHEAD"):
        commit_mapping.get_commits(repo, content, [WI_A], {WI_A})


def test_squash_inherits_union_of_wis(repo: pathlib.Path) -> None:
    """複数の旧commitが同じ新commitになる場合もAWI集合を維持する。"""
    old_a, old_b, new = "a" * 40, "b" * 40, git(repo, "rev-parse", "HEAD")
    events: list[dict[str, object]] = [
        {"commit": old_a, "awi": [WI_A]},
        {"commit": old_b, "awi": [WI_B]},
        {"rewrite": {old_a: new, old_b: new}},
    ]
    content = "\n".join(commit_mapping.encode_event(event) for event in events)
    assert commit_mapping.get_commits(repo, content, [WI_A, WI_B], {WI_A, WI_B}) == {WI_A: [new], WI_B: [new]}
