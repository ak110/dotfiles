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


def _commit(repo: pathlib.Path, message: str) -> str:
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def test_rebase_map_accepts_only_recorded_old_oids(repo: pathlib.Path) -> None:
    """WI対応のないcommitを含むrebaseでは、記録済み旧OIDだけの対応表で継承し、記録外OIDと記録済みOIDの欠落を失敗にする。

    統合手順は`git range-diff`で全commitを検収するが、`--rewrite-map`へ渡すのは進捗記録にWI対応を持つ旧OIDに限る。
    記録外OIDを含めると対応を確定できず失敗し、記録済み旧OIDが対応表に無いと終端前の取得が失敗する。
    """
    base = git(repo, "rev-parse", "HEAD")
    recorded = _commit(repo, "recorded")
    _commit(repo, "unrecorded")
    unrecorded = git(repo, "rev-parse", "HEAD")
    content = commit_mapping.encode_event(commit_mapping.commit_event(repo, recorded, [WI_A], {WI_A}))
    git(repo, "reset", "--hard", base)
    _commit(repo, "upstream")
    rebased_recorded = _commit(repo, "recorded")
    rebased_unrecorded = _commit(repo, "unrecorded")
    mapping = commit_mapping.read_mapping(content, {WI_A})

    with_outside = repo / "with-outside.json"
    with_outside.write_text(json.dumps({recorded: rebased_recorded, unrecorded: rebased_unrecorded}), encoding="utf-8")
    with pytest.raises(commit_mapping.CommitMappingError, match="履歴変更前の対応がありません"):
        commit_mapping.rewrite_event(repo, with_outside, mapping)

    with pytest.raises(commit_mapping.CommitMappingError, match="現在のHEAD"):
        commit_mapping.get_commits(repo, content, [WI_A], {WI_A})

    recorded_only = repo / "recorded-only.json"
    recorded_only.write_text(json.dumps({recorded: rebased_recorded}), encoding="utf-8")
    content += "\n" + commit_mapping.encode_event(commit_mapping.rewrite_event(repo, recorded_only, mapping))
    assert commit_mapping.get_commits(repo, content, [WI_A], {WI_A}) == {WI_A: [rebased_recorded]}
