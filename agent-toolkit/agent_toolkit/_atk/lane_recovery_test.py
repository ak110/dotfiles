"""lane recoveryの契約を検証する。"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

from agent_toolkit._atk import lane, lane_activity, lane_cleanup, lane_retirement
from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._atk.managed_temp import paths as temp_paths
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
@pytest.mark.parametrize(
    "kind",
    [
        "merged",
        "same-tree",
        "equivalent",
        "whitespace",
        "dirty",
        "running",
        "unique",
        "initial",
        "ignored",
        "nested",
        "cache-note",
        "cache",
        "cached-repo",
    ],
)
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
        if kind in {"merged", "dirty", "running", "ignored", "nested", "cache", "cached-repo", "cache-note"}:
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
    if kind in {"ignored", "nested"}:
        with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as exclude:
            exclude.write("\nkeep.txt\nnested/\n")
        if kind == "ignored":
            (worktree / "keep.txt").write_text("保持するデータ", encoding="utf-8")
        else:
            code, _nested, stderr = _create_in(
                capsys,
                monkeypatch,
                "lane-other-session",
                repo,
                selected,
                "--worktree",
                str(worktree / "nested"),
                lane_id="lane-02",
            )
            assert code == 0, stderr
    if kind in {"cache", "cached-repo", "cache-note"}:
        with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as exclude:
            exclude.write("\n/.ruff_cache/\n")
        cache = worktree / ".ruff_cache"
        cache.mkdir()
        (cache / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55", encoding="ascii")
        if kind == "cached-repo":
            (cache / ".git").write_text("gitdir: elsewhere", encoding="ascii")
        if kind == "cache-note":
            (cache / "notes.txt").write_text("保持するデータ", encoding="utf-8")
    if kind == "running":
        monkeypatch.setattr(lane_activity, "worktree_activity", lambda *_a, **_k: "使用中")
    git_repository.run_git(repo, "branch", "user-owned")

    mode = ("--retire-integrated",) if kind == "same-tree" else ("--repo", str(repo))
    code, result, stderr = _invoke(capsys, "delete", "--session-id", record["session_id"], *mode)

    saved = json.loads(pathlib.Path(result["result_path"]).read_text(encoding="utf-8"))
    assert saved["retire_integrated"] is True

    if kind in {"merged", "same-tree", "cache"}:
        assert code == 0 and result["removed"] == ["lane-01"], stderr
        assert not record_path.exists() and not worktree.exists()
        proof = result["retirement_inputs"][0]
        assert proof["basis"] == ("identical-tree" if kind == "same-tree" else "ancestor")
        assert proof["selection"]["integrated"] is False
        assert selected.exists()
    else:
        assert code == 1 and result["retained"], stderr
        assert record_path.exists() and worktree.is_dir()
        if kind == "ignored":
            assert "無視対象" in result["retained"][0]["reason"]
            assert (worktree / "keep.txt").is_file()
        if kind == "cached-repo":
            assert (worktree / ".ruff_cache" / ".git").is_file()
        if kind == "cache-note":
            assert (worktree / ".ruff_cache" / "notes.txt").is_file()
        if kind == "nested":
            assert "内包" in result["retained"][0]["reason"]
            assert (worktree / "nested" / ".git").is_file()
    assert git_repository.git_output(repo, "branch", "--list", "user-owned").strip() == "user-owned"


@pytest.mark.cpu_limit(2.0, reason="実Gitのworktreeと未登録branchを、削除を伴わずに検出する。")
def test_list_candidates_distinguishes_unregistered_resources(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    git_repository.run_git(repo, "branch", "orch/unregistered")
    detached = temp_paths.temp_root_path() / "old-copy"
    detached.parent.mkdir(parents=True, exist_ok=True)
    git_repository.run_git(repo, "worktree", "add", "--detach", str(detached))

    unrelated = tmp_path / "outside" / "managed-temp" / "user-copy"
    unrelated.parent.mkdir(parents=True)
    git_repository.run_git(repo, "worktree", "add", "--detach", str(unrelated))

    code, listed, stderr = _invoke(capsys, "delete", "--list", "--repo", str(repo))

    assert code == 0, stderr
    candidates = listed["orphan_candidates"]
    assert {(r["worktree"], r["branch"]) for r in candidates} == {
        (None, "orch/unregistered"),
        (str(detached.resolve()), None),
        (str(unrelated.resolve()), None),
    }
    by_path = {item["worktree"]: item for item in candidates}
    assert by_path[str(detached.resolve())]["origin"] == "agent"
    assert by_path[str(unrelated.resolve())]["origin"] == "unknown"
    assert "ユーザー確認" in by_path[str(unrelated.resolve())]["reason"]
    assert pathlib.Path(record["worktree"]).is_dir() and detached.is_dir()


@pytest.mark.cpu_limit(4.0, reason="実Gitの登録外資源を通常のlane deleteで分類し、回収を確かめる。")
def test_delete_sweeps_orphans(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    other_session = "lane-other-session"
    nested = tmp_path / "nested"
    git_repository.run_git(repo, "worktree", "add", "-b", "orch/nested", str(nested))
    other_path = nested / ".claude" / "worktrees" / "other-session"
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
    _selection(selected, integrated=False)
    original = pathlib.Path(record["worktree"])
    (original / "covered.txt").write_text("取込み済み", encoding="utf-8")
    covered = git_repository.commit_all(original, "旧レーンの成果")
    git_repository.run_git(repo, "merge", "--ff-only", covered)
    git_repository.run_git(repo, "branch", "orch/obsolete")
    git_repository.run_git(repo, "branch", "orch/race")
    protected_branches = ("worktree-Foo", "orch/reuse")
    for branch in protected_branches:
        git_repository.run_git(repo, "branch", branch)
    with (repo / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write(
            '\n[branch.worktree-Foo]\n\tpushRemote = legacy-remote\n[Branch "orch/reuse"]\n\tpushRemote = reuse-remote\n'
        )
    git_repository.run_git(repo, "symbolic-ref", "refs/heads/orch/alias", "refs/heads/main")
    for branch in ("orch/rebase", "orch/bisect", "orch/sequencer"):
        git_repository.run_git(repo, "branch", branch)
    interrupted = tmp_path / "interrupted"
    git_repository.run_git(repo, "worktree", "add", "--detach", str(interrupted))
    gitdir = pathlib.Path(git_repository.git_output(interrupted, "rev-parse", "--absolute-git-dir").strip())
    (gitdir / "rebase-merge").mkdir()
    (gitdir / "rebase-merge" / "head-name").write_text("refs/heads/orch/rebase\n", encoding="utf-8")
    (gitdir / "BISECT_START").write_text("orch/bisect\n", encoding="utf-8")
    (gitdir / "BISECT_LOG").touch()
    (gitdir / "rebase-merge" / "update-refs").write_text(
        f"refs/heads/orch/sequencer\n{covered}\n{'0' * len(covered)}\n", encoding="utf-8"
    )
    git_repository.run_git(repo, "branch", "user-owned")
    git_repository.run_git(repo, "branch", "--track", "lead/upstream", "main")
    git_repository.run_git(repo, "branch", "worktree-process-loop")
    trees = {}
    for name in ("clean", "dirty", "busy", "unique", "caller", "prepared", "ignored"):
        path = tmp_path / name
        git_repository.run_git(repo, "worktree", "add", "-b", f"orch/{name}", str(path))
        trees[name] = path
    with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as exclude:
        exclude.write("\nignored.txt\n.claude/\n")
    (trees["ignored"] / "ignored.txt").write_text("保持するデータ", encoding="utf-8")
    (trees["dirty"] / "local.txt").write_text("未保存", encoding="utf-8")
    (trees["unique"] / "unique.txt").write_text("未取込み", encoding="utf-8")
    unique_head = git_repository.commit_all(trees["unique"], "固有の変更")
    state = pathlib.Path(record["record_path"]).parents[2] / "process-loop-ownership"
    state.mkdir()
    rounds = state / "rounds"
    rounds.mkdir()
    manifest = rounds / "round.json"
    manifest.write_text(
        json.dumps({"repositories": [{"common_dir": record["common_dir"], "worktree": str(trees["prepared"])}]}),
        encoding="utf-8",
    )
    (state / "ownership.json").write_text(
        json.dumps({"common_dir": record["common_dir"], "manifest": str(manifest)}), encoding="utf-8"
    )
    git = _lane_records.git

    def race(path: pathlib.Path, *args: str) -> str:
        if args[:4] == ("update-ref", "--no-deref", "-d", "refs/heads/orch/race"):
            git_repository.run_git(repo, "update-ref", args[3], unique_head)
        return git(path, *args)

    monkeypatch.setattr(_lane_records, "git", race)
    monkeypatch.chdir(trees["caller"])
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=trees["busy"]) as busy:
        try:
            code, result, stderr = _invoke(capsys, "delete", "--session-id", record["session_id"], "--repo", str(repo))
        finally:
            busy.terminate()

    assert code == 0, stderr
    orphans = result["orphans"]
    assert {(item["resource"], item["branch"]) for item in orphans["removed"]} == {
        ("worktree", "orch/clean"),
        ("branch", "orch/clean"),
        ("branch", "orch/obsolete"),
    }
    assert {item["branch"] for item in orphans["user_confirmation"]} == {None, "user-owned"}
    retained = {item["branch"]: item["reason"] for item in orphans["retained"]}
    assert "orch/clean" not in retained and "orch/obsolete" not in retained
    assert "未追跡" in retained["orch/dirty"]
    assert "使用中" in retained["orch/busy"]
    assert "含まれない" in retained["orch/unique"]
    for name in ("caller", "prepared"):
        assert "回収しない" in retained[f"orch/{name}"]
    assert "無視対象" in retained["orch/ignored"]
    assert "内包" in retained["orch/nested"]
    assert "update-ref" in retained["orch/race"]
    for branch in protected_branches:
        assert "branch固有の設定" in retained[branch]
        assert "--show-origin" in retained[branch]
        assert git_repository.git_output(repo, "rev-parse", f"refs/heads/{branch}").strip() == covered
    assert git_repository.git_output(repo, "config", "--local", "branch.worktree-foo.pushRemote").strip() == "legacy-remote"
    assert git_repository.git_output(repo, "config", "--local", "branch.orch/reuse.pushRemote").strip() == "reuse-remote"
    assert "シンボリックref" in retained["orch/alias"]
    assert git_repository.git_output(repo, "symbolic-ref", "refs/heads/orch/alias").strip() == "refs/heads/main"
    assert git_repository.git_output(repo, "rev-parse", "refs/heads/main").strip() == covered
    for branch in ("orch/rebase", "orch/bisect", "orch/sequencer"):
        assert "中断中" in retained[branch]
        assert git_repository.git_output(repo, "rev-parse", f"refs/heads/{branch}").strip() == covered
    assert git_repository.git_output(repo, "rev-parse", "orch/race").strip() == unique_head
    assert result["retirement_inputs"][0]["basis"] == "ancestor"
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


@pytest.mark.parametrize("state", ["unchanged", "edited", "unrecorded", "legacy"])
@pytest.mark.cpu_limit(3.0, reason="統合済みの原本と生成物を実Gitと公開の回収操作で比較する。")
def test_retired_lane_compares_plugin_content(tmp_path, monkeypatch, capsys, state):
    from agent_toolkit._atk.lane_cleanup import PROJECTION_MANIFEST, projection_manifest  # pylint: disable=import-outside-toplevel

    repo, selection, record = _create(tmp_path, monkeypatch, capsys)
    _selection(selection, integrated=False)
    worktree = pathlib.Path(record["worktree"])
    source = worktree / "agent-toolkit"
    generated = worktree / "agent-toolkit-codex"
    source.mkdir()
    (worktree / ".gitignore").write_text("/agent-toolkit-codex/\n", encoding="ascii")
    (source / "GENERATED.md").write_text("# 自動生成ファイル\n", encoding="utf-8")
    (source / "README.md").write_text("original", encoding="ascii")
    committed = git_repository.commit_all(worktree, "配布物の原本")
    git_repository.run_git(repo, "merge", "--ff-only", committed)
    generated.mkdir()
    (generated / "GENERATED.md").write_bytes((source / "GENERATED.md").read_bytes())
    output = generated / "README.md"
    output.write_text("modified" if state == "edited" else "projection", encoding="ascii")
    if state != "legacy":
        files = {
            pathlib.Path("GENERATED.md"): ((generated / "GENERATED.md").read_bytes(), 0o644),
            pathlib.Path("README.md"): (b"projection", 0o644),
        }
        (generated / PROJECTION_MANIFEST).write_bytes(projection_manifest(generated.name, files)[0])
    if state == "unrecorded":
        (generated / "note.md").write_text("作業者の記録", encoding="utf-8")

    status, result, stderr = _invoke(capsys, "delete", "--session-id", record["session_id"], "--retire-integrated")

    if state != "unchanged":
        assert status == 1 and result["retained"], stderr
        assert output.is_file()
    else:
        assert status == 0 and result["removed"] == ["lane-01"], stderr
        assert not worktree.exists()


@pytest.mark.parametrize("root", [".hypothesis", "rust/claude-statusline/target", "agent-toolkit/.claude-plugin/types"])
@pytest.mark.parametrize("state", ["unchanged", "edited", "unrecorded", "legacy"])
@pytest.mark.cpu_limit(3.0, reason="固定生成先のハッシュ照合と保持理由を実Gitと公開の回収操作で確かめる。")
def test_retired_lane_compares_fixed_generated_output(tmp_path, monkeypatch, capsys, root, state):
    repo, selection, record = _create(tmp_path, monkeypatch, capsys)
    _selection(selection, integrated=False)
    worktree = pathlib.Path(record["worktree"])
    (worktree / ".gitignore").write_text(f"/{root}/\n", encoding="ascii")
    committed = git_repository.commit_all(worktree, "生成先の設定")
    git_repository.run_git(repo, "merge", "--ff-only", committed)

    generated = worktree / root
    output = generated / "nested" / "output"
    output.parent.mkdir(parents=True)
    outputs = {output.relative_to(generated): (b"generated\n", 0o644)}
    output.write_bytes(b"generated\n")
    if state != "legacy":
        (generated / lane_cleanup.PROJECTION_MANIFEST).write_bytes(lane_cleanup.projection_manifest(generated.name, outputs)[0])
    if state == "edited":
        with output.open("ab") as handle:
            handle.write(b"manual\n")
    unknown = generated / "nested" / "__pycache__" / "manual.pyc"
    if state == "unrecorded":
        unknown.parent.mkdir()
        unknown.write_bytes(b"manual\n")

    status, result, stderr = _invoke(capsys, "delete", "--session-id", record["session_id"], "--retire-integrated")

    if state == "unchanged":
        assert status == 0 and result["removed"] == ["lane-01"], stderr
        assert not worktree.exists()
    else:
        assert status == 1 and result["retained"], stderr
        blocker = {"edited": "/nested/output", "unrecorded": "/nested/__pycache__", "legacy": ""}[state]
        assert result["retained"][0]["reason"].endswith(f": {root}{blocker}")
        assert output.read_bytes() == (b"generated\nmanual\n" if state == "edited" else b"generated\n")
        if state == "unrecorded":
            assert unknown.read_bytes() == b"manual\n"
