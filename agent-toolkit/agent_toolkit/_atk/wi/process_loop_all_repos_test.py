"""登録集合の取得と候補数の契約を検証する。"""

import argparse
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._atk import config
from agent_toolkit._atk.wi import listing
from agent_toolkit._testing import git_repository as git


def _write(notes: pathlib.Path, name: str, repo_id: str, *, extra: str = "", state: str = "inbox") -> pathlib.Path:
    path = notes / state / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: awi\ntarget_repo: {repo_id}\n{extra}---\n\n# 要求\n", encoding="utf-8")
    return path


def _repositories(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[config.ConfiguredRepository, ...]:
    paths = [
        git.init_repository(tmp_path / name, origin=f"https://example.test/team/{name}.git") for name in ("one", "two", "empty")
    ]
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(path) for path in paths]))
    monkeypatch.setenv(
        "AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / ("private-notes" if (tmp_path / "private-notes").exists() else "notes"))
    )
    return config.resolve_configured_repositories()


def test_configured_scope(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """設定CLIのJSON登録を読み、同originをまとめ、空・不正登録を区別する。"""
    one = git.init_repository(tmp_path / "one", origin="https://example.test/team/one.git")
    clone = git.init_repository(tmp_path / "clone", origin="git@example.test:team/one.git")
    two = git.init_repository(tmp_path / "two", origin="https://example.test/team/two.git")
    value = json.dumps([str(one), str(clone), str(two)])
    args = argparse.Namespace(config_subcommand="set", key="process_loop_repositories", value=value)
    assert config.dispatch(args, tmp_path) == 0
    repositories = config.resolve_configured_repositories()
    assert [repository.worktree for repository in repositories] == [one, two]
    args.value = "[]"
    assert config.dispatch(args, tmp_path) == 0
    assert not config.resolve_configured_repositories()
    for invalid in ('{"repo": 1}', "broken", '["relative"]', json.dumps([str(tmp_path / "missing")])):
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", invalid)
        with pytest.raises(ValueError, match=".+"):
            config.resolve_configured_repositories()


def test_configured_repo_snapshot(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """キューを1回だけ取得し、リポジトリ別の依存・候補上限・登録外の除外を反映した一覧を渡す。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    one, two, _empty = (repository.repo_id for repository in repositories)
    _write(notes, "root.md", one)
    _write(notes, "internal.md", one, extra="depends_on: [root.md]\n")
    _write(notes, "external.md", one, extra="depends_on: [other.md]\n")
    _write(notes, "other.md", two)
    _write(notes, "unregistered.md", "example.test/team/unregistered")
    snapshots = listing.configured_repo_snapshot(notes, repositories)
    assert snapshots[0].readiness.internal_dependency_waits == ("internal.md",)
    assert "external.md" in snapshots[0].readiness.blocked
    assert [len(snapshot.candidates) for snapshot in snapshots] == [2, 1, 0]
    assert [snapshot.lane_limit for snapshot in snapshots] == [1, 1, 0]
    reads: list[pathlib.Path] = []
    original_read = pathlib.Path.read_text

    def read_text(path: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        if path.is_relative_to(notes):
            reads.append(path)
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    args = argparse.Namespace(
        configured_repos=True,
        skip_pull=True,
        status=["active"],
        type=["all"],
        answered=["all"],
        source=None,
        count=False,
        summary_only=False,
        jsonl=True,
        with_staleness=True,
    )
    listing.cmd_list(args, notes)
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(reads) == 5 and len(set(reads)) == 5
    assert {record["filename"] for record in records} == {"root.md", "internal.md", "external.md", "other.md"}
    external = next(record for record in records if record["filename"] == "external.md")
    assert external["blocked_reason"] == "dependency-unmet-external"
    assert all("staleness" in record for record in records)


def test_processing_candidates_and_lane_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """外部依存を待つprocessingの項目を候補に保ち、通常の除外を適用した後の件数からレーン上限を算出する。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    one = repositories[0]
    two = repositories[1]
    _write(notes, "external.md", two.repo_id)
    for number in range(11):
        _write(notes, f"processing-{number:02}.md", one.repo_id, state="processing", extra="depends_on: [external.md]\n")
    _write(notes, "blocked-inbox.md", one.repo_id, extra="depends_on: [external.md]\n")
    run_skill = notes / "processing" / "run-skill.md"
    run_skill.write_text(
        f"---\ntype: uwi\ntarget_repo: {one.repo_id}\nsource: run-skill\n---\n\n# 確認\n\n## 回答\n実施する\n",
        encoding="utf-8",
    )
    snapshot = listing.configured_repo_snapshot(notes, repositories)[0]
    assert len(snapshot.candidates) == 11
    assert snapshot.lane_limit == 2
    records = snapshot.candidate_records()
    assert len(records) == 11
    assert all(
        {"filename", "target_repo", "ready", "blocked_reason", "source", "summary", "body", "staleness"} <= record.keys()
        for record in records
    )
    assert (
        next(record for record in records if record["filename"] == "processing-00.md")["blocked_reason"]
        == "dependency-unmet-external"
    )
