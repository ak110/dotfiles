"""lane integrationの契約を検証する。"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
from typing import Any

import pytest
import yaml

from agent_toolkit._atk import lane, lane_integration_input, run_script
from agent_toolkit._common import session_registry
from agent_toolkit._testing import git_repository, wi_entry_files
from agent_toolkit._testing.lane_integration_support import (
    _cleanup_integration_manifest,
    _integration_args,
    _integration_selection,
    _merged_integration,
    _write_empty_finalization_manifest,
)
from agent_toolkit._testing.lane_support import _create, _create_args, _invoke, _selection


@pytest.mark.parametrize(("planned", "mixed"), [(False, False), (True, False), (False, True)])
@pytest.mark.cpu_limit(
    5.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大2.572秒。"
)
def test_integrate_public_lifecycle(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    planned: bool,
    mixed: bool,
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    done = "20261023-000000-001.md"
    pending = ["20261023-000000-002.md", "20261023-000000-003.md"] if mixed else []
    if pending:
        document = yaml.safe_load(selected.read_text(encoding="utf-8"))
        document["追加WI"] = pending
        notes = selected.parent / "notes"
        for wi in pending:
            document["選定"].append(dict(document["選定"][0], WI=wi))
            wi_entry_files.entry_path(notes, wi, state="processing").write_bytes(
                wi_entry_files.entry_path(notes, done, state="processing").read_bytes()
            )
        selected.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
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
                "wis": [{"name": name, "commands": []} for name in [done, *pending]],
                **({"processed_wis": [done]} if mixed else {}),
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
    record_path = pathlib.Path(record["record_path"])
    previous = json.loads(record_path.read_text(encoding="utf-8")).get("previous_integration")
    code, repeated, stderr = _invoke(capsys, *args)
    assert code == 0 and repeated["completed"] == result["completed"], stderr
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    assert stored.get("previous_integration") == previous
    rows = yaml.safe_load(selected.read_text(encoding="utf-8"))["選定"]
    assert [row["WI"] for row in rows if row.get("統合状態") == "統合済み"] == [done]
    assert result["selection_integrated"] is not mixed
    if mixed:
        assert stored["selection"]["integrated"] is False
        assert stored["selection"]["processed_wis"] == [done]
        selected.unlink()
        code, held, stderr = _invoke(capsys, "delete")
        assert code == 1 and held["retained"], stderr
        remaining = json.loads(manifest.read_text(encoding="utf-8"))
        remaining["processed_wis"] = pending
        manifest.write_text(json.dumps(remaining), encoding="utf-8")
        code, final, stderr = _invoke(capsys, *args)
        assert code == 0 and final["selection_integrated"] is True, stderr
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0 and deleted["removed"] == ["lane-01"], stderr


def test_processed_wis_uses_explicit_names_without_interpreting_argv() -> None:
    rows: list[dict[str, Any]] = [
        {"name": "done.md", "commands": [["atk", "wi", "adopt", "--note=確認済み", "done"]]},
        {"name": "rejected.md", "commands": [["echo", "wi", "reject", "--if-inbox", "rejected"]]},
        {"name": "partial.md", "commands": [["atk", "wi", "edit", "partial.md"]]},
        {"name": "new.md", "commands": []},
    ]
    assert lane_integration_input.processed_wis({"wis": rows}) == [row["name"] for row in rows]
    assert lane_integration_input.processed_wis({"wis": rows, "processed_wis": ["done.md"]}) == ["done.md"]
    assert lane_integration_input.processed_wis({"wis": rows, "processed_wis": []}) == []


def test_cannot_amend_processed_wis_after_selection_write() -> None:
    rows = [{"name": "done.md", "commands": []}, {"name": "pending.md", "commands": []}]
    base = {"reviewed_head": "a" * 40, "target": "repo", "branch": "main", "no_merge": False}
    old = {**base, "manifest": {"plans": [], "wis": rows, "processed_wis": ["done.md"]}}
    new = {**base, "manifest": {"plans": [], "wis": rows, "processed_wis": ["pending.md"]}}
    state = {"phase": "git-integrated", "completed": ["selection-mark"], "input": old}
    with pytest.raises(ValueError, match="記録済みの処置済みWIは変更できない"):
        lane_integration_input.amend_finalization(state, new, "new-digest")


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.parametrize("new_run", [False, True])
def test_integration_and_reuse_protect_dirty_submodules(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], new_run: bool
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    submodule = git_repository.init_repository(tmp_path / "module", files={"tracked": "基準\n"}, commit_message="基準")
    git_repository.run_git(repo, "-c", "protocol.file.allow=always", "submodule", "add", str(submodule), "sub")
    base = git_repository.commit_all(repo, "サブモジュールを登録")
    git_repository.run_git(worktree, "merge", "--ff-only", base)
    git_repository.run_git(worktree, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
    git_repository.run_git(repo, "config", "diff.ignoreSubmodules", "all")
    (worktree / "feature").write_text("成果\n", encoding="utf-8")
    head = git_repository.commit_all(worktree, "統合する成果")
    manifest = _cleanup_integration_manifest(tmp_path, handoff_text="検収済み")
    old_start = "2026-10-22T00:00:00+09:00"
    old_input = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps({**old_input, "lane_started_at": old_start}), encoding="utf-8")
    integrate_args = _integration_args(record, repo, manifest, head)

    lane_change = worktree / "sub" / "tracked"
    target_change = repo / "sub" / "untracked"
    for tree, change in ((worktree, lane_change), (repo, target_change)):
        change.write_text("未保存\n", encoding="utf-8")
        code, _result, stderr = _invoke(capsys, *integrate_args)
        assert code == 2 and f"統合作業ツリーに未commitまたは未追跡の変更がある: {tree}" in stderr
        assert change.read_text(encoding="utf-8") == "未保存\n"
        assert git_repository.git_output(repo, "rev-parse", "HEAD") == base
        assert git_repository.git_output(worktree, "rev-parse", "HEAD") == head
        if change == lane_change:
            change.write_text("基準\n", encoding="utf-8")
        else:
            change.unlink()

    code, integrated, stderr = _invoke(capsys, *integrate_args)
    assert code == 0 and integrated["phase"] == "finalized" and integrated["integrated_head"] == head, stderr
    args = [str(selected), "--reopen-wi", "20261023-000000-001.md", "--output", str(selected), "--work-dir", str(repo)]
    assert run_script.dispatch(argparse.Namespace(script_name="pick-wi-check", script_args=args)) == 0
    capsys.readouterr()

    lane_change.write_text("再開前の未保存\n", encoding="utf-8")
    if new_run:
        next_selection = selected.with_name("next-selection.yaml")
        next_selection.write_bytes(selected.read_bytes())
        selected = next_selection
    create_args = _create_args(repo, selected)
    code, _result, stderr = _invoke(capsys, *create_args)
    assert code == 2 and "未コミット変更" in stderr
    (repo / "other-lane").write_text("次の統合基点\n", encoding="utf-8")
    advanced = git_repository.commit_all(repo, "統合基点を進める")
    code, _result, stderr = _invoke(capsys, *create_args)
    assert code == 2 and "未コミット変更" in stderr
    assert lane_change.read_text(encoding="utf-8") == "再開前の未保存\n"
    assert git_repository.git_output(worktree, "rev-parse", "HEAD") == head
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == advanced

    lane_change.write_text("基準\n", encoding="utf-8")
    code, reused, stderr = _invoke(capsys, *create_args)
    assert code == 0 and reused["reused"] and reused["worktree"] == str(worktree), stderr
    assert git_repository.git_output(worktree, "rev-parse", "HEAD") == advanced
    assert lane_change.read_text(encoding="utf-8") == "基準\n"

    if new_run:
        assert "integration" not in reused
        assert reused["previous_integration"]["phase"] == "finalized"
        assert "lane_started_at" not in reused
        assert reused["initial_head"] == advanced
        (worktree / "feature").write_text("今回の成果\n", encoding="utf-8")
        current_head = git_repository.commit_all(worktree, "今回の成果")
        started = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=2)
        manifest.write_text(json.dumps({**old_input, "lane_started_at": started.isoformat()}), encoding="utf-8")
        again = _integration_args(reused, repo, manifest, current_head)
        code, finalized, stderr = _invoke(capsys, *again)
        assert code == 0 and finalized["phase"] == "finalized", stderr
        persisted = json.loads(pathlib.Path(reused["record_path"]).read_text(encoding="utf-8"))
        values = persisted["integration"]["finalization_values"]
        assert persisted["lane_started_at"] == started.isoformat()
        ended = datetime.datetime.fromisoformat(values["completed_at"])
        assert abs(int(values["lane_seconds"]) - (ended - started).total_seconds()) <= 1
        code, retried, stderr = _invoke(capsys, *again)
        assert code == 0 and retried["phase"] == "finalized", stderr
        latest = json.loads(pathlib.Path(reused["record_path"]).read_text(encoding="utf-8"))
        assert latest["integration"]["finalization_values"] == values
    else:
        assert reused["integration"]["phase"] == "finalized"
        assert reused["lane_started_at"] == old_start


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.parametrize("scenario", ["clean", "dirty", "restart"])
def test_integration_distinguishes_own_gitlink_update_from_child_edit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    name = "sub module"
    source = git_repository.init_repository(tmp_path / "module", files={"tracked": "基準\n"}, commit_message="基準")
    original = git_repository.git_output(source, "rev-parse", "HEAD")
    git_repository.run_git(repo, "-c", "protocol.file.allow=always", "submodule", "add", str(source), name)
    base = git_repository.commit_all(repo, "サブモジュールを登録")
    git_repository.run_git(worktree, "merge", "--ff-only", base)
    git_repository.run_git(worktree, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
    git_repository.run_git(repo, "config", "diff.ignoreSubmodules", "all")
    (worktree / "feature").write_text("成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "レーンの成果")
    (source / "tracked").write_text("次の版\n", encoding="utf-8")
    updated = git_repository.commit_all(source, "子の次の版")
    git_repository.run_git(repo / name, "fetch", "origin")
    git_repository.run_git(repo / name, "checkout", "--detach", updated)
    target = git_repository.commit_all(repo, "統合先で子を更新")
    manifest = _cleanup_integration_manifest(tmp_path, handoff_text="検収済み")
    script = "from pathlib import Path; assert Path('feature').read_text(encoding='utf-8') == '成果'"
    if scenario == "dirty":
        script += f"; Path({name!r}, 'tracked').write_text('未保存', encoding='utf-8')"
    if scenario == "restart":
        counter = tmp_path / "validation-attempt"
        script += (
            f"; counter = Path({str(counter)!r}); n = int(counter.read_text()) if counter.exists() else 0;"
            " counter.write_text(str(n + 1)); raise SystemExit(42 if n < 2 else 0)"
        )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["verification"] = [[sys.executable, "-c", script]]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert all(
        not git_repository.git_output(tree, "status", "--porcelain=v1", "--ignore-submodules=none") for tree in (worktree, repo)
    )
    args = _integration_args(record, repo, manifest, head)
    code, result, stderr = _invoke(capsys, *args)

    assert git_repository.git_output(worktree / name, "rev-parse", "HEAD") == original
    assert git_repository.git_output(repo / name, "rev-parse", "HEAD") == updated
    if scenario == "dirty":
        assert code == 2 and "統合作業ツリーに未commitまたは未追跡の変更がある" in stderr
        assert git_repository.git_output(repo, "rev-parse", "HEAD") == target
        assert (worktree / name / "tracked").read_text(encoding="utf-8") == "未保存"
    else:
        if scenario == "restart":
            assert code == 2 and "終了42" in stderr
            current = git_repository.git_output(worktree, "rev-parse", "HEAD")
            args = (*_integration_args(record, repo, manifest, current), "--restart-review")

            git_repository.run_git(worktree / name, "commit", "--allow-empty", "-m", "別の子HEAD")
            alternate = git_repository.git_output(worktree / name, "rev-parse", "HEAD")
            git_repository.run_git(worktree, "add", "--", name)
            code, _result, stderr = _invoke(capsys, *args)
            assert code == 2 and "未commitまたは未追跡の変更" in stderr
            git_repository.run_git(worktree, "-c", "submodule.recurse=false", "restore", "--staged", "--", name)
            code, _result, stderr = _invoke(capsys, *args)
            assert code == 2 and "終了42" in stderr
            assert git_repository.git_output(repo, "rev-parse", "HEAD") == target
            code, result, stderr = _invoke(capsys, *args)
            assert git_repository.git_output(worktree / name, "rev-parse", "HEAD") == alternate
            args = _integration_args(record, repo, manifest, current)
        assert code == 0 and result["phase"] == "finalized" and "verification-0" in result["completed"], stderr
        assert git_repository.git_output(repo, "rev-parse", "HEAD") == result["integrated_head"]
        assert (repo / "feature").read_text(encoding="utf-8") == "成果"
        code, resumed, stderr = _invoke(capsys, *args)
        assert code == 0 and resumed["phase"] == "finalized", stderr


@pytest.mark.cpu_limit(
    2.5, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大1.147秒。"
)
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
@pytest.mark.cpu_limit(1.5, reason="実Gitの成果と所有登録で、拒否後のHEADと資源保持を確かめる4阻止条件。所有CPU最大0.533秒。")
def test_integrate_rejects_unreviewed_or_unowned_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], condition: str
) -> None:
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


@pytest.mark.cpu_limit(
    1.5, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大0.791秒。"
)
def test_integrate_observation_and_saved_plan(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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


@pytest.mark.cpu_limit(
    3.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大1.419秒。"
)
def test_integrate_additional_wi_uses_current_review_and_collection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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


@pytest.mark.cpu_limit(
    2.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大0.874秒。"
)
def test_integrate_refreshes_publication_before_rewriting(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
    assert code == 0, stderr
    git_repository.run_git(repo, "merge-base", "--is-ancestor", head, "HEAD")
    git_repository.run_git(repo, "merge-base", "--is-ancestor", before, "HEAD")


@pytest.mark.cpu_limit(
    3.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大1.459秒。"
)
def test_integrate_restarts_after_reviewed_correction(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Git統合前の検証失敗は記録を残し、是正後の独立文脈レビュー入力で公開操作から再開する。"""
    repo, record, manifest, value, _original = _merged_integration(tmp_path, monkeypatch, capsys, failed=True)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature").write_text("是正済み", encoding="utf-8")
    reviewed = git_repository.commit_all(worktree, "是正")
    value["verification"] = [[sys.executable, "-c", "pass"]]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    args = _integration_args(record, repo, manifest, reviewed)
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2 and "--restart-review" in stderr
    code, result, stderr = _invoke(capsys, *args, "--restart-review")
    assert code == 0 and result["phase"] == "finalized", stderr
    stored = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
    assert stored["previous_integration"]["phase"] == "verified"
    assert "verification-0" in result["completed"]
    assert (repo / "feature").read_text(encoding="utf-8") == "是正済み"


@pytest.mark.cpu_limit(
    2.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大0.891秒。"
)
def test_integrate_uses_reviewed_oid_when_branch_advances_before_merge(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
