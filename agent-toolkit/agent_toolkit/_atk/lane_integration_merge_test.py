"""通常mergeによる公開統合を検証する。"""

from __future__ import annotations

import pathlib

import pytest

from agent_toolkit._testing import git_repository
from agent_toolkit._testing.lane_integration_support import (
    _integration_args,
    _integration_selection,
    _write_empty_finalization_manifest,
)
from agent_toolkit._testing.lane_support import _create, _invoke


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.406秒。"
    ),
)
def test_merge_preserves_lane_and_target_oids(tmp_path, monkeypatch, capsys):
    """分岐を接続して元OIDを保ち、同じ公開入力で再開できる。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature").write_text("成果", encoding="utf-8")
    original = git_repository.commit_all(worktree, "成果")
    (repo / "upstream").write_text("先行成果", encoding="utf-8")
    upstream = git_repository.commit_all(repo, "先行成果")
    manifest = _write_empty_finalization_manifest(tmp_path)
    args = _integration_args(record, repo, manifest, original)
    code, result, stderr = _invoke(capsys, *args)
    assert code == 0, stderr
    assert result["phase"] == "finalized"
    parents = git_repository.git_output(repo, "show", "--format=%P", "--no-patch", "HEAD").split()
    assert parents == [original, upstream]
    code, repeated, stderr = _invoke(capsys, *args)
    assert code == 0, stderr
    assert repeated["integrated_head"] == result["integrated_head"]
