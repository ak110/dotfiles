"""対応記録ファイルのAWI対応と履歴変更の継承を実Gitで検証する。"""

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
    git(tmp_path, "init", "--initial-branch=main")
    git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "first")
    return tmp_path


def _commit(repo: pathlib.Path, message: str) -> str:
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def _short(repo: pathlib.Path, oid: str) -> str:
    return git(repo, "rev-parse", "--short", oid)


def test_many_to_many_roundtrip_and_rewrite(repo: pathlib.Path) -> None:
    """複数AWIのcommitと複数commitのAWIを短縮OIDで保ち、amend後は新OIDだけを取得する。"""
    base = git(repo, "rev-parse", "HEAD")
    first = _commit(repo, "first-recorded")
    events = [commit_mapping.commit_event(repo, first, base, [WI_A, WI_B], {WI_A, WI_B})]
    assert events[0] == {"commits": [_short(repo, first)], "awi": sorted([WI_A, WI_B])}
    old = _commit(repo, "second")
    # 前HEADは短縮OIDでも完全OIDと同じ記録になる。
    events.append(commit_mapping.commit_event(repo, old, _short(repo, first), [WI_A], {WI_A, WI_B}))
    assert events[1] == commit_mapping.commit_event(repo, old, first, [WI_A], {WI_A, WI_B})
    assert commit_mapping.get_commits(repo, events, [WI_A, WI_B], {WI_A, WI_B}) == {
        WI_A: [_short(repo, first), _short(repo, old)],
        WI_B: [_short(repo, first)],
    }
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
    for old_value, new_value in ((old, new), (_short(repo, old), _short(repo, new))):
        replacements = repo / "rewrite.json"
        replacements.write_text(json.dumps({old_value: new_value}), encoding="utf-8")
        event = commit_mapping.rewrite_event(repo, replacements, commit_mapping.read_mapping(repo, events, {WI_A, WI_B}))
        assert event == {"rewrite": {_short(repo, old): _short(repo, new)}}
    assert commit_mapping.get_commits(repo, [*events, event], [WI_A, WI_B], {WI_A, WI_B}) == {
        WI_A: [_short(repo, first), _short(repo, new)],
        WI_B: [_short(repo, first)],
    }


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
        commit_mapping.get_commits(repo, commit_mapping.body_events(content), [WI_A], {WI_A})
    assert raised.value.reason
    assert "実装担当" in raised.value.next_action


def test_rejects_unresolvable_or_ambiguous_short_oid(repo: pathlib.Path) -> None:
    """存在しない短縮OIDと複数のcommitに一致する短縮OIDは、誤ったcommitを返さずに失敗する。"""
    with pytest.raises(commit_mapping.CommitMappingError, match="一意に解決できません"):
        commit_mapping.get_commits(repo, [{"commits": ["0000000"], "awi": [WI_A]}], [WI_A], {WI_A})
    # 先頭4文字が同じcommitを2件以上含む履歴を1回のfast-importで作成し、4文字の短縮OIDを一意に解決できない状態にする。
    stream = "".join(
        f"commit refs/heads/many\nmark :{index + 1}\ncommitter Test <test@example.invalid> {1_700_000_000 + index} +0000\n"
        f"data {len(str(index))}\n{index}\n" + (f"from :{index}\n" if index else "") + "\n"
        for index in range(2000)
    )
    subprocess.run(
        ["git", "fast-import", "--quiet"], cwd=repo, input=stream, check=True, capture_output=True, text=True, timeout=60
    )
    heads = git(repo, "rev-list", "many").splitlines()
    prefixes = [oid[:4] for oid in heads]
    prefix = next(head for head in prefixes if prefixes.count(head) > 1)
    with pytest.raises(commit_mapping.CommitMappingError, match="一意に解決できません"):
        commit_mapping.get_commits(repo, [{"commits": [prefix], "awi": [WI_A]}], [WI_A], {WI_A})


def test_rejects_unrecorded_rewrite_even_if_old_object_exists(repo: pathlib.Path) -> None:
    """旧OIDがobjectとして残っていても現在のHEADのcommitへ代用しない。"""
    base = git(repo, "rev-parse", "HEAD")
    old = _commit(repo, "old")
    events = [commit_mapping.commit_event(repo, old, base, [WI_A], {WI_A})]
    git(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--amend", "--allow-empty", "-m", "new"
    )
    with pytest.raises(commit_mapping.CommitMappingError, match="現在のHEAD"):
        commit_mapping.get_commits(repo, events, [WI_A], {WI_A})


def test_squash_inherits_union_of_wis(repo: pathlib.Path) -> None:
    """複数の旧commitが同じ新commitになる場合もAWI集合を維持する。"""
    old_a, old_b, new = "a" * 40, "b" * 40, git(repo, "rev-parse", "HEAD")
    events: list[dict[str, object]] = [
        {"commit": old_a, "awi": [WI_A]},
        {"commit": old_b, "awi": [WI_B]},
        {"rewrite": {old_a: new, old_b: new}},
    ]
    assert commit_mapping.get_commits(repo, events, [WI_A, WI_B], {WI_A, WI_B}) == {
        WI_A: [_short(repo, new)],
        WI_B: [_short(repo, new)],
    }


def test_commit_event_requires_new_head_and_its_previous_parent(repo: pathlib.Path) -> None:
    """旧HEADや前HEADの取り違えをWIの実装commitとして記録しない。"""
    base = git(repo, "rev-parse", "HEAD")
    current = _commit(repo, "recorded")
    assert commit_mapping.commit_event(repo, current, base, [WI_A], {WI_A}) == {
        "commits": [_short(repo, current)],
        "awi": [WI_A],
    }
    for revision, previous in ((base, base), (current, current), (current, "a" * 40), (current, "missing")):
        with pytest.raises(commit_mapping.CommitMappingError) as raised:
            commit_mapping.commit_event(repo, revision, previous, [WI_A], {WI_A})
        assert "--previous-head" in raised.value.next_action


def _branch_with_two_commits(repo: pathlib.Path) -> tuple[str, list[str]]:
    """別branchへ2件のcommitを作成し、mainへ戻って取り込み前のHEADと群のcommitを返す。"""
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "group")
    group = [_commit(repo, "group-1"), _commit(repo, "group-2")]
    git(repo, "switch", "main")
    return base, group


def test_records_range_added_by_cherry_pick_in_one_event(repo: pathlib.Path) -> None:
    """範囲指定のcherry-pickで加えた全commitを1回の記録で同じAWI集合へ対応付け、1件の置き換えを継承する。"""
    _branch_with_two_commits(repo)
    _commit(repo, "lane")
    previous = git(repo, "rev-parse", "HEAD")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "cherry-pick", "--allow-empty", "main..group")
    picked = git(repo, "rev-list", "--reverse", f"{previous}..HEAD").splitlines()
    assert len(picked) == 2
    event = commit_mapping.commit_event(repo, "HEAD", previous, [WI_A, WI_B], {WI_A, WI_B})
    assert event == {"commits": [_short(repo, oid) for oid in picked], "awi": sorted([WI_A, WI_B])}
    expected = sorted(_short(repo, oid) for oid in picked)
    result = commit_mapping.get_commits(repo, [event], [WI_A, WI_B], {WI_A, WI_B})
    assert {wi: sorted(commits) for wi, commits in result.items()} == {WI_A: expected, WI_B: expected}

    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--amend", "--allow-empty", "-m", "re")
    new = git(repo, "rev-parse", "HEAD")
    replacements = repo / "rewrite.json"
    replacements.write_text(json.dumps({_short(repo, picked[1]): new}), encoding="utf-8")
    rewrite = commit_mapping.rewrite_event(repo, replacements, commit_mapping.read_mapping(repo, [event], {WI_A, WI_B}))
    result = commit_mapping.get_commits(repo, [event, rewrite], [WI_A], {WI_A, WI_B})
    assert sorted(result[WI_A]) == sorted([_short(repo, picked[0]), _short(repo, new)])


def test_records_range_added_by_fast_forward(repo: pathlib.Path) -> None:
    """fast-forwardマージで加えた範囲もcherry-pickと同じく全commitを記録する。"""
    base, group = _branch_with_two_commits(repo)
    git(repo, "merge", "--ff-only", "group")
    event = commit_mapping.commit_event(repo, "HEAD", base, [WI_A], {WI_A})
    assert event == {"commits": [_short(repo, oid) for oid in group], "awi": [WI_A]}
    assert commit_mapping.get_commits(repo, [event], [WI_A], {WI_A}) == {WI_A: [_short(repo, oid) for oid in group]}


def test_rejects_range_with_merge_or_non_ancestor(repo: pathlib.Path) -> None:
    """マージcommitを含む範囲と、前HEADがfirst-parentの祖先でない場合を理由と次の操作を示して拒否する。"""
    base, group = _branch_with_two_commits(repo)
    _commit(repo, "lane")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "merge", "--no-ff", "-m", "merge", "group")
    with pytest.raises(commit_mapping.CommitMappingError, match="マージcommit") as merged:
        commit_mapping.commit_event(repo, "HEAD", base, [WI_A], {WI_A})
    assert "cherry-pick" in merged.value.next_action
    with pytest.raises(commit_mapping.CommitMappingError, match="first-parentの祖先ではありません"):
        commit_mapping.commit_event(repo, "HEAD", group[1], [WI_A], {WI_A})


def test_rebase_map_accepts_only_recorded_old_oids(repo: pathlib.Path) -> None:
    """WI対応のないcommitを含むrebaseでは、記録済み旧OIDだけの対応表で継承し、記録外OIDと記録済みOIDの欠落を失敗にする。

    統合手順は`git range-diff`で全commitを検収するが、`--rewrite-map`へ渡すのは対応記録を持つ旧OIDに限る。
    記録外OIDを含めると対応を確定できず失敗し、記録済み旧OIDが対応表に無いと終端前の取得が失敗する。
    """
    base = git(repo, "rev-parse", "HEAD")
    recorded = _commit(repo, "recorded")
    events = [commit_mapping.commit_event(repo, recorded, base, [WI_A], {WI_A})]
    _commit(repo, "unrecorded")
    unrecorded = git(repo, "rev-parse", "HEAD")
    git(repo, "reset", "--hard", base)
    _commit(repo, "upstream")
    rebased_recorded = _commit(repo, "recorded")
    rebased_unrecorded = _commit(repo, "unrecorded")
    mapping = commit_mapping.read_mapping(repo, events, {WI_A})

    with_outside = repo / "with-outside.json"
    with_outside.write_text(json.dumps({recorded: rebased_recorded, unrecorded: rebased_unrecorded}), encoding="utf-8")
    with pytest.raises(commit_mapping.CommitMappingError, match="履歴変更前の対応がありません"):
        commit_mapping.rewrite_event(repo, with_outside, mapping)

    with pytest.raises(commit_mapping.CommitMappingError, match="現在のHEAD"):
        commit_mapping.get_commits(repo, events, [WI_A], {WI_A})

    recorded_only = repo / "recorded-only.json"
    recorded_only.write_text(json.dumps({recorded: rebased_recorded}), encoding="utf-8")
    events.append(commit_mapping.rewrite_event(repo, recorded_only, mapping))
    assert commit_mapping.get_commits(repo, events, [WI_A], {WI_A}) == {WI_A: [_short(repo, rebased_recorded)]}


def test_reads_legacy_body_comments_before_attachment(tmp_path: pathlib.Path, repo: pathlib.Path) -> None:
    """本文の旧形式の記録を対応記録ファイルの記録より前に適用し、両方を合わせた対応を返す。"""
    legacy = _commit(repo, "legacy")
    record = tmp_path / "record.md"
    record.write_text("本文\n" + commit_mapping.encode_event({"commit": legacy, "awi": [WI_A]}) + "\n", encoding="utf-8")
    current = _commit(repo, "current")
    commit_mapping.append_event(record, commit_mapping.commit_event(repo, current, legacy, [WI_B], {WI_A, WI_B}))
    assert commit_mapping.mapping_path(record) == tmp_path / "record.wi-commits.jsonl"
    events = commit_mapping.read_events(record, record.read_text(encoding="utf-8"))
    assert commit_mapping.get_commits(repo, events, [WI_A, WI_B], {WI_A, WI_B}) == {
        WI_A: [_short(repo, legacy)],
        WI_B: [_short(repo, current)],
    }
