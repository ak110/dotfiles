"""旧rebase状態の移行とmerge競合後の公開再開を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.lane_integration_support import (
    _adopt_argv,
    _integration_args,
    _integration_selection,
    _plan_operation,
    _record_wi_commit,
    _write_empty_finalization_manifest,
)
from agent_toolkit._testing.lane_support import _create, _invoke


@pytest.mark.parametrize("phase", ["rebasing", "rebased", "awaiting-validation", "verified"])
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.547秒。"
    ),
)
def test_legacy_rebase_migrates_without_losing_wi_mapping(tmp_path, monkeypatch, capsys, phase):
    """保存済み旧対応を汎用操作で継承し、元成果と証拠を保って新方式へ再開する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    tree = pathlib.Path(record["worktree"])
    wi = "20261023-000000-001.md"
    (tree / "feature").write_text("成果", encoding="utf-8")
    old = git_repository.commit_all(tree, "成果")
    manifest = _write_empty_finalization_manifest(tmp_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    handoff = pathlib.Path(value["handoff"])
    _record_wi_commit(capsys, handoff, tree, wi, old, record["initial_head"], step="実装", result="成果")
    (repo / "upstream").write_text("先行成果", encoding="utf-8")
    base = git_repository.commit_all(repo, "先行成果")
    git_repository.run_git(tree, "rebase", base)
    head = git_repository.git_output(tree, "rev-parse", "HEAD")
    mapping = tmp_path / "legacy-map.json"
    mapping.write_text(json.dumps({old: head}), encoding="utf-8")
    legacy = {"phase": phase, "old_head": old, "new_head": head, "rewrite_map": str(mapping), "completed": []}
    path = pathlib.Path(record["record_path"])
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["integration"] = legacy
    path.write_text(json.dumps(stored), encoding="utf-8")
    args = _integration_args(record, repo, manifest, head)
    code, _, stderr = _invoke(capsys, *args)
    assert code == 2 and "plan-rewrite" in stderr
    assert json.loads(path.read_text(encoding="utf-8"))["integration"] == legacy
    _plan_operation(
        capsys,
        "plan-rewrite",
        "--worktree",
        str(tree),
        "--previous-head",
        old,
        "--rewrite-map",
        str(mapping),
        "--handoff",
        str(handoff),
        "--completed-step",
        "移行",
        "--result",
        "保存済み対応を継承",
    )
    code, final, stderr = _invoke(capsys, *args)
    assert code == 0 and final["phase"] == "finalized", stderr
    assert json.loads(path.read_text(encoding="utf-8"))["previous_integration"] == legacy
    events = commit_mapping.read_events(handoff, handoff.read_text(encoding="utf-8"))
    assert commit_mapping.get_commits(tree, events, [wi], {wi}) == {
        wi: [git_repository.git_output(tree, "rev-parse", "--short=7", head)]
    }
    assert mapping.is_file()
    assert (repo / "feature").read_text(encoding="utf-8") == "成果"


@pytest.mark.parametrize("advance", [False, True])
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.547秒。"
    ),
)
def test_merge_conflict_resolution_and_wi_assignment(tmp_path, monkeypatch, capsys, advance):
    """競合解消mergeの新差分だけをWIへ記録し、先進後も成果OIDを保持する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    tree = pathlib.Path(record["worktree"])
    (tree / "shared").write_text("レーン", encoding="utf-8")
    old = git_repository.commit_all(tree, "レーン成果")
    (repo / "shared").write_text("統合先", encoding="utf-8")
    upstream = git_repository.commit_all(repo, "統合先成果")
    manifest = _write_empty_finalization_manifest(tmp_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    handoff = pathlib.Path(value["handoff"])
    wi = "20261023-000000-001.md"
    git_repository.init_repository(tmp_path / "notes", commit_message="採用前のWI")
    _record_wi_commit(capsys, handoff, tree, wi, old, record["initial_head"], step="実装", result="レーン成果")
    args = _integration_args(record, repo, manifest, old)
    code, _, stderr = _invoke(capsys, *args)
    assert code == 2, stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == upstream
    (tree / "shared").write_text("両側の成果", encoding="utf-8")
    merged = git_repository.commit_all(tree, "競合解消")
    _record_wi_commit(capsys, handoff, tree, wi, merged, old, step="競合解消", result="両側の要求を保持")
    output, _ = _plan_operation(capsys, "plan-commits", str(handoff), "--worktree", str(tree), "--handoff", "--allowed-awi", wi)
    commits = json.loads(output)["commits"]
    assert {git_repository.git_output(tree, "rev-parse", oid) for oid in commits} == {old, merged}
    value["wis"] = [{"name": wi, "commit_record": str(handoff), "commands": [_adopt_argv(repo, wi, commits)]}]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    if advance:
        (repo / "later").write_text("追加成果", encoding="utf-8")
        git_repository.commit_all(repo, "追加統合")
    code, final, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, merged), "--restart-review")
    assert code == 0 and final["phase"] == "finalized", stderr
    for oid in (old, upstream, merged):
        git_repository.run_git(repo, "merge-base", "--is-ancestor", oid, "HEAD")
    assert (repo / "shared").read_text(encoding="utf-8") == "両側の成果"
