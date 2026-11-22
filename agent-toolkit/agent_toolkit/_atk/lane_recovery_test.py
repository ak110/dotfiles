"""lane recoveryの契約を検証する。"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

from agent_toolkit._atk import lane, lane_activity, lane_retirement
from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._common import session_registry
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.lane_integration_support import _integration_args, _integration_selection, _legacy, _selection_with
from agent_toolkit._testing.lane_support import _create, _create_args, _create_in, _invoke, _selection


@pytest.mark.parametrize("integrated", [True, False])
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.480秒。"
    ),
)
def test_delete_uses_held_selection_after_selection_file_expires(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], integrated: bool
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _selection(selected, integrated=integrated)
    code, _held, stderr = _invoke(capsys, *_create_args(repo, selected))
    assert code == 0, stderr
    selected.unlink()
    code, result, stderr = _invoke(capsys, "delete")
    if integrated:
        assert code == 0 and result["removed"] == ["lane-01"], stderr
        assert not pathlib.Path(record["worktree"]).exists()
    else:
        assert code == 1 and result["retained"][0]["reason"] == "選定の統合状態が未統合"
        assert pathlib.Path(record["worktree"]).is_dir() and pathlib.Path(record["record_path"]).is_file()


@pytest.mark.cpu_limit(3.0, reason="実Gitの旧レーン、統合先の履歴と公開回収操作を比較する。")
@pytest.mark.parametrize("kind", ["merged", "same-tree", "equivalent", "whitespace", "dirty", "running", "unique", "initial"])
def test_retired_lane_only_removes_covered_changes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _selection(selected, integrated=False)
    worktree = pathlib.Path(record["worktree"])
    record_path = pathlib.Path(record["record_path"])
    if kind != "initial":
        (worktree / "lane-content.txt").write_text("  成果\n", encoding="utf-8")
        head = git_repository.commit_all(worktree, "レーン成果")
        if kind in {"merged", "dirty", "running"}:
            git_repository.run_git(repo, "merge", "--ff-only", head)
        elif kind == "same-tree":
            git_repository.run_git(repo, "commit", "--allow-empty", "-m", "別の履歴")
            git_repository.run_git(repo, "cherry-pick", head)
        elif kind == "equivalent":
            (repo / "other.txt").write_text("他の変更\n", encoding="utf-8")
            git_repository.commit_all(repo, "統合先の別の変更")
            git_repository.run_git(repo, "cherry-pick", head)
        elif kind == "whitespace":
            (repo / "lane-content.txt").write_text("成果\n", encoding="utf-8")
            git_repository.commit_all(repo, "別の内容")
    if kind == "dirty":
        (worktree / "local.txt").write_text("未保存", encoding="utf-8")
    if kind == "running":
        monkeypatch.setattr(lane_activity, "worktree_activity", lambda *_a, **_k: "使用中")
    git_repository.run_git(repo, "branch", "user-owned")

    code, result, stderr = _invoke(capsys, "delete", "--session-id", record["session_id"], "--retire-integrated")

    saved = json.loads(pathlib.Path(result["result_path"]).read_text(encoding="utf-8"))
    assert saved["retire_integrated"] is True

    if kind in {"merged", "same-tree"}:
        assert code == 0 and result["removed"] == ["lane-01"], stderr
        assert not record_path.exists() and not worktree.exists()
        proof = result["retirement_inputs"][0]
        assert proof["basis"] == ("ancestor" if kind == "merged" else "identical-tree")
        assert proof["selection"]["integrated"] is False
        assert selected.exists()
    else:
        assert code == 1 and result["retained"], stderr
        assert record_path.exists() and worktree.is_dir()
    assert git_repository.git_output(repo, "branch", "--list", "user-owned").strip() == "user-owned"


@pytest.mark.cpu_limit(2.0, reason="実Gitのworktreeと未登録branchを、削除を伴わずに検出する。")
def test_list_candidates_distinguishes_unregistered_resources(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    git_repository.run_git(repo, "branch", "orch/unregistered")
    detached = tmp_path / "managed-temp" / "old-copy"
    detached.parent.mkdir()
    git_repository.run_git(repo, "worktree", "add", "--detach", str(detached))

    code, listed, stderr = _invoke(capsys, "delete", "--list", "--repo", str(repo))

    assert code == 0, stderr
    candidates = listed["orphan_candidates"]
    assert {(r["worktree"], r["branch"]) for r in candidates} == {
        (None, "orch/unregistered"),
        (str(detached.resolve()), None),
    }
    assert all(r["origin"] == "agent" and r["reason"] is None for r in candidates)
    assert pathlib.Path(record["worktree"]).is_dir() and detached.is_dir()


@pytest.mark.cpu_limit(4.0, reason="実Gitの登録外資源を通常のlane deleteで分類し、回収を確かめる。")
def test_delete_sweeps_orphans(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    other_session = "lane-other-session"
    other_path = tmp_path / "other-session"
    code, _other, stderr = _create_in(
        capsys,
        monkeypatch,
        other_session,
        repo,
        selected,
        "--branch",
        "orch/other-session",
        "--worktree",
        str(other_path),
        lane_id="lane-02",
    )
    assert code == 0, stderr
    monkeypatch.setenv(lane.SESSION_ENV, record["session_id"])
    git_repository.run_git(repo, "branch", "orch/obsolete")
    git_repository.run_git(repo, "branch", "user-owned")
    git_repository.run_git(repo, "branch", "--track", "lead/upstream", "main")
    git_repository.run_git(repo, "branch", "worktree-process-loop")
    trees = {}
    for name in ("clean", "dirty", "busy", "unique", "caller", "prepared"):
        path = tmp_path / name
        git_repository.run_git(repo, "worktree", "add", "-b", f"orch/{name}", str(path))
        trees[name] = path
    (trees["dirty"] / "local.txt").write_text("未保存", encoding="utf-8")
    (trees["unique"] / "unique.txt").write_text("未取込み", encoding="utf-8")
    git_repository.commit_all(trees["unique"], "固有の変更")
    state = pathlib.Path(record["record_path"]).parents[2] / "process-loop-ownership"
    state.mkdir()
    (state / "ownership.json").write_text(
        json.dumps({"common_dir": record["common_dir"], "prepared_worktree": str(trees["prepared"])}),
        encoding="utf-8",
    )
    monkeypatch.chdir(trees["caller"])
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=trees["busy"]) as busy:
        try:
            code, result, stderr = _invoke(capsys, "delete", "--repo", str(repo))
        finally:
            busy.terminate()

    assert code == 0, stderr
    orphans = result["orphans"]
    assert {(item["resource"], item["branch"]) for item in orphans["removed"]} == {
        ("worktree", "orch/clean"),
        ("branch", "orch/clean"),
        ("branch", "orch/obsolete"),
    }
    assert [item["branch"] for item in orphans["user_confirmation"]] == ["user-owned"]
    retained = {item["branch"]: item["reason"] for item in orphans["retained"]}
    assert "未追跡" in retained["orch/dirty"]
    assert "使用中" in retained["orch/busy"]
    assert "含まれない" in retained["orch/unique"]
    for name in ("caller", "prepared"):
        assert "回収しない" in retained[f"orch/{name}"]
    assert "upstream" in retained["lead/upstream"]
    assert "回収しない" in retained["worktree-process-loop"]
    assert other_path.is_dir() and repo.is_dir()
    assert not pathlib.Path(record["worktree"]).exists()
    assert not trees["clean"].exists()
    assert all(path.is_dir() for name, path in trees.items() if name != "clean")


@pytest.mark.cpu_limit(2.0, reason="gitlinkとGitのdiff設定がある実Gitで安全判定を検証する。")
def test_retirement_proof_never_ignores_unmerged_gitlink(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "same.txt").write_text("取込済み", encoding="utf-8")
    git_repository.run_git(worktree, "add", "same.txt")
    git_repository.run_git(worktree, "update-index", "--add", "--cacheinfo", f"160000,{record['initial_head']},sub")
    git_repository.run_git(worktree, "commit", "-m", "通常ファイルとgitlink")
    (repo / "same.txt").write_text("取込済み", encoding="utf-8")
    git_repository.commit_all(repo, "通常ファイルだけ")
    git_repository.run_git(repo, "config", "diff.ignoreSubmodules", "all")

    with pytest.raises(ValueError, match="統合先へ含まれない変更"):
        lane_retirement.prove(record, repo)


@pytest.mark.cpu_limit(2.0, reason="Gitの実サブモジュール内の変更を隠す設定に対して保護を確かめる。")
def test_lane_usage_check_keeps_dirty_submodule(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    child = git_repository.init_repository(
        tmp_path / "local-submodule", files={"sub.txt": "original"}, commit_message="submodule"
    )
    git_repository.run_git(worktree, "-c", "protocol.file.allow=always", "submodule", "add", str(child), "sub")
    git_repository.commit_all(worktree, "gitlink")
    git_repository.run_git(worktree, "config", "diff.ignoreSubmodules", "all")
    (worktree / "sub" / "sub.txt").write_text("未保存の変更", encoding="utf-8")

    assert lane_activity.has_unignored_changes(worktree)


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.480秒。"
    ),
)
def test_integrate_finalizes_and_cleans_after_selection_file_expires(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)

    code, _reused, stderr = _invoke(capsys, *_create_args(repo, selected))
    assert code == 0, stderr
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature.txt").write_text("成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "成果")
    handoff = tmp_path / "handoff.md"
    handoff.write_text("検収済み採否", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                "verification": [],
                "handoff": str(handoff),
            }
        ),
        encoding="utf-8",
    )
    args = _integration_args(record, repo, manifest, head)
    correct = manifest.read_text(encoding="utf-8")
    wrong = json.loads(correct)
    wrong["wis"] = [{"name": "other.md", "commands": []}]
    manifest.write_text(json.dumps(wrong), encoding="utf-8")
    selected.unlink()
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2 and "WI集合" in stderr
    manifest.write_text(correct, encoding="utf-8")
    code, result, stderr = _invoke(capsys, *args)
    assert code == 0 and result["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == head
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0 and deleted["removed"] == ["lane-01"], stderr


@pytest.mark.parametrize("entity", ["present", "missing"])
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.480秒。"
    ),
)
def test_recover_releases_legacy_registration_and_keeps_branch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], entity: str
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "unmerged.txt").write_text("未統合の成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "未統合の成果")
    _legacy(record)
    if entity == "missing":
        subprocess.run(["rm", "-rf", str(worktree)], check=True)
    code, held, stderr = _invoke(capsys, "delete")
    assert code == 1 and "atk lane recover --session-id lane-test-session --lane lane-01" in held["retained"][0]["reason"]
    code, recovered, stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
    assert code == 0, stderr
    assert recovered["recovered"][0]["branch"] == record["branch"]
    assert recovered["recovered"][0]["head"] == head and recovered["recovered"][0]["phase"] == "done"
    assert git_repository.git_output(repo, "rev-parse", f"refs/heads/{record['branch']}") == head
    assert str(worktree.resolve()) not in git_repository.git_output(repo, "worktree", "list", "--porcelain")
    _selection(selected)
    code, created, stderr = _create_in(capsys, monkeypatch, "next-session", repo, selected)
    assert code == 0 and created["worktree"] == str(worktree), stderr
    code, listed, stderr = _invoke(capsys, "delete", "--list")
    assert code == 0, stderr
    old = next(entry["record"] for entry in listed["records"] if entry["record"]["session_id"] == "lane-test-session")
    assert old["recovery"]["branch"] == record["branch"] and old["recovery"]["head"] == head
    monkeypatch.setenv(lane.SESSION_ENV, "lane-test-session")
    code, cleaned, stderr = _invoke(capsys, "delete")
    assert code == 0 and cleaned["recovered"][0]["head"] == head and not cleaned["removed"], stderr
    code, repeated, stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
    assert code == 0 and repeated["recovered"][0]["head"] == head, stderr


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.480秒。"
    ),
)
def test_recover_keeps_registrations_with_selection_information(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    before = pathlib.Path(record["record_path"]).read_bytes()
    for _ in range(2):
        code, result, _stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
        assert code == 1 and "回復の対象外" in result["retained"][0]["reason"]
        assert pathlib.Path(record["record_path"]).read_bytes() == before
        assert pathlib.Path(record["worktree"]).is_dir()

        selected.unlink(missing_ok=True)


@pytest.mark.parametrize("condition", ["dirty", "active-session", "foreign-owner", "partial-failure"])
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.480秒。"
    ),
)
def test_recover_protects_unfinished_work_and_resumes_after_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], condition: str
) -> None:
    """dirty・稼働中・所有不一致の登録を保持し、途中失敗は同じ回復操作で残りから再開する。"""
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    _legacy(record)
    record_path = pathlib.Path(record["record_path"])
    if condition == "dirty":
        (worktree / "unfinished.txt").write_text("未commitの成果", encoding="utf-8")
    elif condition == "active-session":
        owner = session_registry.owner_process()
        assert owner is not None
        session_registry.publish(
            "child", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(worktree), processes=(owner,)
        )
    elif condition == "foreign-owner":
        stored = json.loads(record_path.read_text(encoding="utf-8"))
        stored["session_id"] = "another-session"
        record_path.write_text(json.dumps(stored), encoding="utf-8")
    else:
        original = lane._git  # pylint: disable=protected-access  # noqa: SLF001

        def _fail_once(repo_path: pathlib.Path, *argv: str, **kwargs: Any) -> str:
            del kwargs
            if argv[:2] == ("worktree", "remove"):
                monkeypatch.setattr(_lane_records, "git", original)
                raise ValueError("git worktreeが終了1: 模擬した失敗")
            return original(repo_path, *argv)

        monkeypatch.setattr(_lane_records, "git", _fail_once)
    code, result, _stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
    assert code == 1 and result["retained"]
    assert worktree.is_dir()
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    if condition == "partial-failure":
        assert stored["recovery"]["phase"] == "started"
        code, result, stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
        assert code == 0 and result["recovered"][0]["phase"] == "done", stderr
        assert not worktree.exists()
        assert record["branch"] in git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
    else:
        assert "recovery" not in stored


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.480秒。"
    ),
)
def test_reuse_switches_held_selection_to_current_selection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _selection_with(selected, ["previous.md"])
    code, _reused, stderr = _invoke(capsys, *_create_args(repo, selected))
    assert code == 0, stderr
    current = tmp_path / "current-selection.yaml"
    _selection_with(current, ["current.md"], integrated=False)
    code, resumed, stderr = _create_in(
        capsys, monkeypatch, "next-session", repo, current, "--reuse-record", record["record_path"]
    )
    assert code == 0, stderr
    stored = json.loads(pathlib.Path(resumed["record_path"]).read_text(encoding="utf-8"))
    assert stored["selection"] == {"lane_wis": ["current.md"], "processed_wis": [], "integrated": False}
    current.unlink()
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and held["retained"][0]["reason"] == "選定の統合状態が未統合"
