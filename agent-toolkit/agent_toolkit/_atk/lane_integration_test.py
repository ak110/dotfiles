"""lane integrationの契約を検証する。"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

import pytest
import yaml

from agent_toolkit._atk import lane
from agent_toolkit._common import session_registry
from agent_toolkit._testing import git_repository, wi_entry_files
from agent_toolkit._testing.lane_integration_support import (
    _integration_args,
    _integration_selection,
    _rebased_integration,
    _write_empty_finalization_manifest,
)
from agent_toolkit._testing.lane_support import _create, _invoke, _selection


@pytest.mark.parametrize("planned", [False, True])
def test_integrate_public_lifecycle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], planned: bool
) -> None:
    """レビュー済み成果を公開操作で統合し、計画あり・なしの最終化後だけ回収できる。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature.txt").write_text("成果", encoding="utf-8")
    git_repository.commit_all(worktree, "成果")
    head = git_repository.git_output(worktree, "rev-parse", "HEAD")
    handoff = tmp_path / "handoff.md"
    handoff.write_text("検収済み採否と完成条件", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    saved = tmp_path / "saved-plan"
    argv = [sys.executable, "-c", "import pathlib,sys;pathlib.Path(sys.argv[1]).touch()", str(saved)]
    manifest.write_text(
        json.dumps(
            {
                "plans": [{"name": "plan.md", "commands": [argv]}] if planned else [],
                "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                "verification": [],
                "handoff": str(handoff),
            }
        ),
        encoding="utf-8",
    )
    args = _integration_args(record, repo, manifest, head)
    code, result, stderr = _invoke(capsys, *args)
    assert code == 0, stderr
    assert result["phase"] == "finalized" and result["integrated_head"] == head
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == head
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "成果"
    assert saved.exists() is planned
    code, repeated, stderr = _invoke(capsys, *args)
    assert code == 0 and repeated["completed"] == result["completed"], stderr
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0 and deleted["removed"] == ["lane-01"], stderr


def test_integrate_mixed_manifest_reviews_implemented_deferral_and_keeps_no_merge_items(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """実装を伴う延期は証拠確認へ残し、実装なしの計画とWIは同じ最終化で扱う。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    document = yaml.safe_load(selected.read_text(encoding="utf-8"))
    additional_wi = "20261023-000000-002.md"
    document["選定"].append({**document["選定"][0], "WI": additional_wi})
    document["初回配分"]["候補WI"].append(additional_wi)
    document["初回配分"]["レーン割当"][additional_wi] = "lane-01"
    document["初回配分"]["不可分成分"].append({"WI": [additional_wi], "結合条件": []})
    selected.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    notes = selected.parent / "notes"
    original_wi = wi_entry_files.entry_path(notes, "20261023-000000-001.md", state="processing")
    wi_entry_files.entry_path(notes, additional_wi, state="processing").write_text(
        original_wi.read_text(encoding="utf-8"), encoding="utf-8"
    )
    head = git_repository.git_output(pathlib.Path(record["worktree"]), "rev-parse", "HEAD")
    handoff = tmp_path / "handoff.md"
    handoff.write_text("20261023-000000-001.mdは実装を伴う延期。20261023-000000-002.mdは実装なし。", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [{"name": "no-implementation.md", "no_merge": True, "commands": [[sys.executable, "-c", "pass"]]}],
                "wis": [
                    {"name": "20261023-000000-001.md", "commands": []},
                    {"name": "20261023-000000-002.md", "no_merge": True, "commands": []},
                ],
                "handoff": str(handoff),
            }
        ),
        encoding="utf-8",
    )
    args = _integration_args(record, repo, manifest, head)
    code, result, stderr = _invoke(capsys, *args)
    assert code == 0 and result["phase"] == "finalized", stderr


def test_integrate_resume_and_preservation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """最終化の途中失敗はGit統合と成功済み保存を繰り返さず、未完了のWI終了から再開する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    git_repository.commit_all(worktree, "成果")
    head = git_repository.git_output(worktree, "rev-parse", "HEAD")
    manifest = tmp_path / "integration.json"
    calls, permit = tmp_path / "calls", tmp_path / "permit"
    save = [
        sys.executable,
        "-c",
        "import pathlib,sys;p=pathlib.Path(sys.argv[1]);p.write_text(p.read_text()+'s' if p.exists() else 's')",
        str(calls),
    ]
    terminal = [
        sys.executable,
        "-c",
        "import pathlib,sys;sys.exit(0 if pathlib.Path(sys.argv[1]).exists() else 4)",
        str(permit),
    ]
    manifest.write_text(
        json.dumps(
            {
                "plans": [{"name": "plan.md", "commands": [save]}],
                "wis": [{"name": "20261023-000000-001.md", "commands": [terminal]}],
                "verification": [],
            }
        ),
        encoding="utf-8",
    )
    args = _integration_args(record, repo, manifest, head)
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2 and "終了4" in stderr
    stored = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
    assert stored["integration"]["phase"] == "git-integrated"
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == head
    assert "統合済み" not in selected.read_text(encoding="utf-8")
    permit.touch()
    code, result, stderr = _invoke(capsys, *args)
    assert code == 0 and result["phase"] == "finalized", stderr
    assert calls.read_text(encoding="utf-8") == "s"


@pytest.mark.parametrize("condition", ["dirty", "review-head", "foreign-owner", "active-session"])
def test_integrate_rejects_unreviewed_or_unowned_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], condition: str
) -> None:
    """検収不能な作業を統合せず、成果と所有記録を保持する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _selection(selected, integrated=False)
    worktree = pathlib.Path(record["worktree"])
    old_head = git_repository.git_output(worktree, "rev-parse", "HEAD")
    git_repository.commit_all(worktree, "成果")
    head = git_repository.git_output(worktree, "rev-parse", "HEAD")
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    manifest.write_text(json.dumps({"plans": [], "wis": [], "verification": [], "handoff": str(handoff)}), encoding="utf-8")
    if condition == "dirty":
        (worktree / "unfinished").touch()
    elif condition == "review-head":
        head = old_head
    elif condition == "foreign-owner":
        monkeypatch.setenv(lane.SESSION_ENV, "foreign")
    else:
        session_registry.publish("child", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(worktree))
    code, _result, _stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 2
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == old_head
    assert worktree.exists() and pathlib.Path(record["record_path"]).exists()


def test_integrate_observation_and_saved_plan(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """観測のみの再開はGitを変更せず、保存済み計画と延期WIを検収して最終化する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    saved = tmp_path / "plan.md"
    saved.write_text("保存済み計画", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [{"name": "plan.md", "commands": [], "saved": str(saved)}],
                "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                "verification": [],
            }
        ),
        encoding="utf-8",
    )
    before = git_repository.git_output(repo, "rev-parse", "HEAD")
    code, result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, before), "--no-merge")
    assert code == 0 and result["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == before
    assert saved.read_text(encoding="utf-8") == "保存済み計画"


def test_integrate_additional_wi_uses_current_review_and_collection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ資源への追加WIは今回のレビューHEADと全WI集合で再統合する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    wis = [{"name": "20261023-000000-001.md", "commands": []}]
    for index in range(2):
        (worktree / f"feature-{index}").write_text("成果", encoding="utf-8")
        head = git_repository.commit_all(worktree, "成果")
        if index:
            document = yaml.safe_load(selected.read_text(encoding="utf-8"))
            document["選定"].append(dict(document["選定"][0], WI="20261023-000000-002.md"))
            document["追加WI"] = ["20261023-000000-002.md"]
            document["レーンの所要時間"][0]["統合状態"] = "未統合"
            selected.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
            notes = tmp_path / "notes"
            wi_entry_files.entry_path(notes, "20261023-000000-002.md", state="processing").write_bytes(
                wi_entry_files.entry_path(notes, "20261023-000000-001.md", state="processing").read_bytes()
            )
            wis.append({"name": "20261023-000000-002.md", "commands": []})
        manifest.write_text(
            json.dumps({"plans": [], "wis": wis, "verification": [], "handoff": str(handoff)}), encoding="utf-8"
        )
        code, result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
        assert code == 0 and result["phase"] == "finalized", stderr
        assert git_repository.git_output(repo, "rev-parse", "HEAD") == head
    assert (repo / "feature-0").exists() and (repo / "feature-1").exists()


def test_integrate_refreshes_publication_before_rewriting(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """古いremote追跡refが無くても、公開済みcommitをfetchして書換えを拒否する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    remote = git_repository.init_bare_repository(tmp_path / "origin.git")
    git_repository.run_git(repo, "remote", "add", "publication", str(remote))
    worktree = pathlib.Path(record["worktree"])
    (worktree / "published").write_text("公開済み", encoding="utf-8")
    head = git_repository.commit_all(worktree, "公開済み")
    git_repository.run_git(worktree, "push", "publication", record["branch"])
    git_repository.run_git(repo, "update-ref", "-d", f"refs/remotes/publication/{record['branch']}")
    (repo / "upstream").write_text("先行統合", encoding="utf-8")
    before = git_repository.commit_all(repo, "先行統合")
    manifest = _write_empty_finalization_manifest(tmp_path)
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 2 and "push済みcommit" in stderr
    assert git_repository.git_output(worktree, "rev-parse", "HEAD") == head
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == before


def test_integrate_restarts_after_reviewed_correction(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Git統合前の検証失敗は記録を残し、是正後の独立文脈レビュー入力で公開操作から再開する。"""
    repo, record, manifest, value, _original = _rebased_integration(tmp_path, monkeypatch, capsys, failed=True)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature").write_text("是正済み", encoding="utf-8")
    reviewed = git_repository.commit_all(worktree, "是正")
    value["verification"] = [[sys.executable, "-c", "pass"]]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    args = _integration_args(record, repo, manifest, reviewed)
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2 and "--restart-review" in stderr
    code, result, stderr = _invoke(capsys, *args, "--restart-review")
    assert code == 1 and result["phase"] == "awaiting-validation", stderr
    assert result["old_head"] == _original["old_head"]
    assert result["additional_commits"] == [reviewed]
    assert json.loads(pathlib.Path(result["rewrite_map"]).read_text(encoding="utf-8")) == {
        _original["old_head"]: _original["new_head"]
    }
    code, result, stderr = _invoke(capsys, *args, "--accept-rebased-head", reviewed)
    assert code == 0 and result["phase"] == "finalized", stderr
    stored = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
    assert stored["previous_integration"]["phase"] == "rebased"
    assert "verification-0" in result["completed"]
    assert (repo / "feature").read_text(encoding="utf-8") == "是正済み"


def test_integrate_uses_reviewed_oid_when_branch_advances_before_merge(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """最後の検収後にbranchが進んでも、未レビューcommitをffへ混入しない。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "reviewed").write_text("受理済み", encoding="utf-8")
    head = git_repository.commit_all(worktree, "レビュー済み")
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                "handoff": str(handoff),
                "verification": [],
            }
        ),
        encoding="utf-8",
    )
    real_run = lane.git_command.run

    def advancing_run(argv: list[str], cwd: pathlib.Path, *extra: Any, **kwargs: Any) -> Any:
        if argv[:1] == ["merge"] and cwd == repo:
            (worktree / "unreviewed").write_text("追加の未レビュー成果", encoding="utf-8")
            git_repository.commit_all(worktree, "未レビュー")
        return real_run(argv, cwd, *extra, **kwargs)

    monkeypatch.setattr(lane.git_command, "run", advancing_run)
    code, result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 0 and result["integrated_head"] == head, stderr
    assert not (repo / "unreviewed").exists()
    assert (worktree / "unreviewed").exists()
