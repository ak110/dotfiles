"""laneの契約を検証する。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

import pytest
import yaml

from agent_toolkit._atk import lane, managed_temp, run_script
from agent_toolkit._common import session_registry
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.lane_integration_support import (
    _cleanup_integration_manifest,
    _integration_args,
    _integration_selection,
    prepare_lane_verification,
)
from agent_toolkit._testing.lane_support import _create, _create_args, _create_in, _fixed, _invoke, _selection

__all__ = ["prepare_lane_verification"]


@pytest.mark.cpu_limit(
    3.5, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大1.766秒。"
)
def test_create_reuses_integrated_resources_then_delete_removes_only_owned_lane(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, selected, first = _create(tmp_path, monkeypatch, capsys)
    assert first["worktree"] == str(_fixed(repo, "lane-01")) and first["managed_temp"] is None

    assert not git_repository.git_output(repo, "status", "--porcelain=v1", "--untracked-files=all")
    foreign = tmp_path / "foreign-worktree"
    git_repository.run_git(repo, "worktree", "add", "-b", "foreign-branch", str(foreign))
    code, reused, stderr = _invoke(
        capsys,
        *_create_args(repo, selected),
    )
    assert code == 0, stderr
    assert reused["reused"] and reused["worktree"] == first["worktree"]
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"] and not deleted["retained"]
    assert not pathlib.Path(first["worktree"]).exists()
    assert not pathlib.Path(first["record_path"]).exists()
    assert foreign.is_dir() and repo.is_dir() and selected.is_file()
    branches = git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
    assert first["branch"] not in branches and "foreign-branch" in branches
    assert pathlib.Path(deleted["result_path"]).is_file()
    code, listed, stderr = _invoke(capsys, "delete", "--list")
    assert code == 0, stderr
    assert not listed["records"] and listed["results"][0]["record"]["removed"] == ["lane-01"]
    code, repeated, _stderr = _invoke(capsys, "delete")
    assert code == 0 and not repeated["removed"]
    assert repeated["result_path"] != deleted["result_path"]
    assert json.loads(pathlib.Path(deleted["result_path"]).read_text(encoding="utf-8"))["removed"] == ["lane-01"]


@pytest.mark.cpu_limit(
    4.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大2.137秒。"
)
def test_reopen_existing_wi_refreshes_same_lane_and_finalizes_again(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    manifest = _cleanup_integration_manifest(tmp_path, handoff_text="再開の証拠")
    (worktree / "first").write_text("最初の成果", encoding="utf-8")
    first = git_repository.commit_all(worktree, "最初の成果")
    code, initial, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, first))
    assert code == 0 and initial["phase"] == "finalized", stderr
    original = yaml.safe_load(selected.read_text(encoding="utf-8"))["初回配分"]
    (repo / "other-lane").write_text("他レーンの成果", encoding="utf-8")
    advanced = git_repository.commit_all(repo, "他レーンの成果")
    args = [str(selected), "--reopen-wi", "20261023-000000-001.md", "--output", str(selected), "--work-dir", str(repo)]
    assert run_script.dispatch(argparse.Namespace(script_name="pick-wi-check", script_args=args)) == 0
    capsys.readouterr()
    code, reused, stderr = _invoke(capsys, *_create_args(repo, selected))
    assert code == 0 and reused["worktree"] == record["worktree"] and reused["branch"] == record["branch"], stderr
    assert git_repository.git_output(worktree, "rev-parse", "HEAD") == advanced
    assert (worktree / "other-lane").read_text(encoding="utf-8") == "他レーンの成果"
    (worktree / "second").write_text("再開後の成果", encoding="utf-8")
    second = git_repository.commit_all(worktree, "再開後の成果")
    code, final, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, second))
    assert code == 0 and final["phase"] == "finalized" and final["integrated_head"] == second, stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == second
    document = yaml.safe_load(selected.read_text(encoding="utf-8"))
    assert document["初回配分"] == original and document["レーンの所要時間"][0]["統合状態"] == "統合済み"

    assert run_script.dispatch(argparse.Namespace(script_name="pick-wi-check", script_args=args)) == 0
    capsys.readouterr()
    code, repeated, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, second))
    assert code == 0 and repeated["phase"] == "finalized", stderr
    assert yaml.safe_load(selected.read_text(encoding="utf-8"))["レーンの所要時間"][0]["統合状態"] == "統合済み"


@pytest.mark.parametrize("unmerged", [None, "commit", "worktree"])
@pytest.mark.cpu_limit(3.5, reason="マージなしの最終化から再開まで、実Gitで履歴と未保存成果を照合する。")
def test_reopen_after_no_merge_preserves_unmerged_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], unmerged: str | None
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    original = git_repository.git_output(worktree, "rev-parse", "HEAD")
    (repo / "accepted.txt").write_text("統合先で保存済み", encoding="utf-8")
    accepted = git_repository.commit_all(repo, "統合先に先行成果を保存")
    manifest = _cleanup_integration_manifest(tmp_path, handoff_text="追加差分なし")
    code, finalized, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, original), "--no-merge")
    assert code == 0 and finalized["phase"] == "finalized" and finalized["integrated_head"] == accepted, stderr
    assert git_repository.git_output(worktree, "rev-parse", "HEAD") == original != accepted

    (repo / "later.txt").write_text("他レーンの後続成果", encoding="utf-8")
    advanced = git_repository.commit_all(repo, "統合先をさらに進める")
    assert (
        run_script.dispatch(
            argparse.Namespace(
                script_name="pick-wi-check",
                script_args=[
                    str(selected),
                    "--reopen-wi",
                    "20261023-000000-001.md",
                    "--output",
                    str(selected),
                    "--work-dir",
                    str(repo),
                ],
            )
        )
        == 0
    )
    capsys.readouterr()
    local_file = worktree / "local.txt"
    local_head = original
    if unmerged:
        local_file.write_text("レーンにのみある成果", encoding="utf-8")
        if unmerged == "commit":
            local_head = git_repository.commit_all(worktree, "レーン固有の成果")

    code, reused, stderr = _invoke(capsys, *_create_args(repo, selected))
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == advanced
    if unmerged:
        assert code == 2, stderr
        assert git_repository.git_output(worktree, "rev-parse", "HEAD") == local_head
        assert local_file.read_text(encoding="utf-8") == "レーンにのみある成果"
    else:
        assert code == 0 and reused["reused"] and reused["worktree"] == str(worktree), stderr
        assert git_repository.git_output(worktree, "rev-parse", "HEAD") == advanced
        assert (worktree / "later.txt").read_text(encoding="utf-8") == "他レーンの後続成果"


@pytest.mark.cpu_limit(
    2.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大0.881秒。"
)
def test_fixed_creation_reclaims_missing_previous_resource(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    shutil.rmtree(record["worktree"])

    code, created, stderr = _create_in(capsys, monkeypatch, "next-session", repo, selected)

    assert "を実行できない" not in stderr
    assert code == 0, stderr
    assert created["session_id"] == "next-session"
    assert created["worktree"] == record["worktree"]
    assert not pathlib.Path(record["record_path"]).exists()


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "実Gitの登録だけ残したworktree消失を、同じ登録の再利用拒否と回復案内で検収する。"
        "同じ実Git資源群の所有CPU観測と起動数から有限上限を設定する。"
    ),
)
def test_reuse_rejects_record_whose_worktree_is_missing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    shutil.rmtree(record["worktree"])
    monkeypatch.setenv(lane.SESSION_ENV, "next-session")

    code, _created, stderr = _invoke(capsys, *_create_args(repo, selected), "--reuse-record", record["record_path"])

    assert "を実行できない" not in stderr
    assert code == 2
    assert "再利用元のworktreeが無い" in stderr
    assert "atk lane delete --session-id lane-test-session" in stderr


@pytest.mark.parametrize("phase", ["create", "prepare"])
@pytest.mark.cpu_limit(
    3.5, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大1.905秒。"
)
def test_failed_preparation_recovers_using_the_same_registered_resource(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], phase: str
) -> None:
    """作成前と環境準備の失敗を登録へ残し、入力を是正して同じ資源から再開する。"""
    monkeypatch.setenv(lane.SESSION_ENV, "preparation-session")
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="基準")
    selected, commands = tmp_path / "selection.yaml", tmp_path / "commands.json"
    _selection(selected)
    commands.write_text(json.dumps({phase: [sys.executable, "-c", "raise SystemExit(3)"]}), encoding="utf-8")
    args = (*_create_args(repo, selected), "--commands-file", str(commands))
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2 and f"{phase}手順が終了3" in stderr
    code, listed, stderr = _invoke(capsys, "delete", "--list")
    assert code == 0, stderr
    original = listed["records"][0]["record"]
    assert not original["ready"] and pathlib.Path(original["record_path"]).is_file()
    commands.write_text("{}", encoding="utf-8")
    code, resumed, stderr = _invoke(capsys, *args)
    assert code == 0, stderr
    assert resumed["ready"] and resumed["reused"]
    assert resumed["worktree"] == original["worktree"] and resumed["branch"] == original["branch"]
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


@pytest.mark.cpu_limit(
    5.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大2.653秒。"
)
def test_interrupted_lane_can_transfer_to_new_session_without_losing_uncommitted_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, selected, original = _create(tmp_path, monkeypatch, capsys)
    unfinished = pathlib.Path(original["worktree"]) / "unfinished.txt"
    unfinished.write_text("再開する成果", encoding="utf-8")
    monkeypatch.setenv(lane.SESSION_ENV, "resumed-session")
    code, resumed, stderr = _invoke(capsys, *_create_args(repo, selected), "--reuse-record", original["record_path"])
    assert code == 0, stderr
    assert resumed["session_id"] == "resumed-session" and resumed["worktree"] == original["worktree"]
    assert unfinished.read_text(encoding="utf-8") == "再開する成果"
    assert not pathlib.Path(original["record_path"]).exists()
    assert pathlib.Path(resumed["record_path"]).is_file()
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and held["retained"]
    git_repository.commit_all(pathlib.Path(resumed["worktree"]), "再開した成果")
    git_repository.run_git(repo, "merge", "--ff-only", resumed["branch"])
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


@pytest.mark.cpu_limit(
    8.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大4.279秒。"
)
def test_fixed_placement_is_shared_across_sessions_callers_and_recreation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="初期状態")
    other = git_repository.init_repository(tmp_path / "other", initial_branch="main", commit_message="初期状態")
    selected = tmp_path / "selection.yaml"
    _selection(selected)

    caller = repo / ".claude" / "worktrees" / "process-loop"
    git_repository.run_git(repo, "worktree", "add", "-b", "process-loop", str(caller))
    observed: list[str] = []
    for session_id, origin in (("session-a", repo), ("session-b", caller), ("session-c", repo)):
        code, created, stderr = _create_in(capsys, monkeypatch, session_id, origin, selected)
        assert code == 0, stderr
        assert not created["reused"] and created["session_id"] == session_id
        observed.append(created["worktree"])
        if session_id == "session-a":
            code, deleted, stderr = _invoke(capsys, "delete")
            assert code == 0 and deleted["removed"] == ["lane-01"], stderr

    assert observed == [str(_fixed(repo, "lane-01"))] * 3
    code, listed, stderr = _invoke(capsys, "delete", "--list")
    assert code == 0, stderr
    owners = {entry["record"]["session_id"] for entry in listed["records"]}
    assert owners == {"session-c"}
    code, second, stderr = _create_in(capsys, monkeypatch, "session-c", caller, selected, lane_id="lane-02")
    assert code == 0, stderr
    code, foreign, stderr = _create_in(capsys, monkeypatch, "session-other", other, selected)
    assert code == 0, stderr
    assert second["worktree"] == str(_fixed(repo, "lane-02"))
    assert foreign["worktree"] == str(_fixed(other, "lane-01"))
    assert len({observed[0], second["worktree"], foreign["worktree"]}) == 3

    assert not (caller / ".claude" / "worktrees").exists()


@pytest.mark.parametrize("condition", ["dirty", "unintegrated", "active-session"])
@pytest.mark.cpu_limit(
    2.5, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大1.122秒。"
)
def test_fixed_placement_keeps_previous_unfinished_resource_and_returns_recovery(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], condition: str
) -> None:
    """固定作成先に残る未完了の前回資源を上書き・別パスへ回避せず、理由と回復操作を返して再開できる。"""
    repo, selected, original = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(original["worktree"])
    unfinished = worktree / "unfinished.txt"
    if condition == "dirty":
        unfinished.write_text("前回の成果", encoding="utf-8")
    elif condition == "unintegrated":
        _selection(selected, integrated=False)
    else:
        owner = session_registry.owner_process()
        assert owner is not None
        session_registry.publish(
            "child", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(worktree), processes=(owner,)
        )
    code, _result, stderr = _create_in(capsys, monkeypatch, "next-session", repo, selected)
    assert code == 2
    assert f"--reuse-record {original['record_path']}" in stderr
    assert "atk lane delete --session-id lane-test-session" in stderr
    assert pathlib.Path(original["record_path"]).is_file() and worktree.is_dir()
    if condition == "dirty":
        assert unfinished.read_text(encoding="utf-8") == "前回の成果"
    assert not (repo / ".claude" / "worktrees").exists() or sorted(
        path.name for path in (repo / ".claude" / "worktrees").iterdir()
    ) == ["process-wi-lane-01"]
    if condition == "active-session":
        return
    code, resumed, stderr = _create_in(
        capsys, monkeypatch, "next-session", repo, selected, "--reuse-record", original["record_path"]
    )
    assert code == 0, stderr
    assert resumed["worktree"] == original["worktree"] and resumed["session_id"] == "next-session"


@pytest.mark.cpu_limit(
    6.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大3.193秒。"
)
def test_project_create_procedure_receives_the_fixed_placement(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="初期状態")
    selected = tmp_path / "selection.yaml"
    _selection(selected)
    commands = tmp_path / "commands.json"
    commands.write_text(
        json.dumps({"create": ["git", "-C", "{repo}", "worktree", "add", "--detach", "{worktree}"]}), encoding="utf-8"
    )
    for session_id in ("session-a", "session-b"):
        code, created, stderr = _create_in(capsys, monkeypatch, session_id, repo, selected, "--commands-file", str(commands))
        assert code == 0, stderr
        assert created["worktree"] == str(_fixed(repo, "lane-01"))
        code, deleted, stderr = _invoke(capsys, "delete")
        assert code == 0 and deleted["removed"] == ["lane-01"], stderr


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "実Gitの固定作成先にある所有不明の成果を、登録せず変更しないことを確認する。"
        "同じ実Git資源群の所有CPU観測と起動数から有限上限を設定する。"
    ),
)
def test_fixed_placement_does_not_overwrite_unregistered_entity(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="初期状態")
    selected = tmp_path / "selection.yaml"
    _selection(selected)
    unknown = _fixed(repo, "lane-01") / "unknown.txt"
    unknown.parent.mkdir(parents=True)
    unknown.write_text("所有不明", encoding="utf-8")
    code, _result, stderr = _create_in(capsys, monkeypatch, "session-a", repo, selected)
    assert code == 2 and "所有登録の無い資源" in stderr
    assert unknown.read_text(encoding="utf-8") == "所有不明"
    code, listed, stderr = _invoke(capsys, "delete", "--list")
    assert code == 0 and not listed["records"], stderr


@pytest.mark.cpu_limit(
    3.5, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大1.916秒。"
)
def test_legacy_managed_temp_lane_is_resumed_and_cleaned_with_its_area(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="初期状態")
    selected = tmp_path / "selection.yaml"
    _selection(selected)
    area = managed_temp.create_managed_temp("lane-01")
    code, legacy, stderr = _create_in(capsys, monkeypatch, "old-session", repo, selected, "--worktree", str(area / "wt"))
    assert code == 0, stderr
    # 旧版のcreateが保存した個別managed-tempの値を再現する。
    record_path = pathlib.Path(legacy["record_path"])
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    record_path.write_text(json.dumps(dict(stored, managed_temp=str(area)), ensure_ascii=False) + "\n", encoding="utf-8")
    (pathlib.Path(legacy["worktree"]) / "unfinished.txt").write_text("旧配置の成果", encoding="utf-8")
    code, resumed, stderr = _create_in(capsys, monkeypatch, "new-session", repo, selected, "--reuse-record", str(record_path))
    assert code == 0, stderr
    assert resumed["worktree"] == legacy["worktree"] and resumed["managed_temp"] == str(area)
    git_repository.commit_all(pathlib.Path(resumed["worktree"]), "旧配置の成果")
    git_repository.run_git(repo, "merge", "--ff-only", resumed["branch"])
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0 and deleted["removed"] == ["lane-01"], stderr
    assert not area.exists()


@pytest.mark.parametrize("remove_exit_code", [0, 7])
@pytest.mark.parametrize("detached", [False, True])
@pytest.mark.cpu_limit(
    4.5, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大2.323秒。"
)
def test_hook_owned_resource(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    remove_exit_code: int,
    detached: bool,
) -> None:
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="基準")
    destination = tmp_path / "hook-created"
    script = tmp_path / "hook.py"
    script.write_text(
        "import json,pathlib,subprocess,sys\n"
        "p=json.load(sys.stdin)\n"
        "if p['hook_event_name']=='WorktreeCreate':\n"
        " assert p['name']=='process-wi-lane-01'\n"
        " mode=['--detach'] if sys.argv[3]=='True' else ['-b',p['name']]\n"
        " subprocess.run(['git','worktree','add',*mode,sys.argv[1]],check=True,stdout=sys.stderr)\n"
        " pathlib.Path(sys.argv[1],'hook-result').write_text('hookの成果',encoding='utf-8')\n"
        " print(sys.argv[1])\n"
        "else:\n"
        " assert p['worktree_path']==sys.argv[1]\n"
        " subprocess.run(['git','worktree','remove',sys.argv[1]],check=True)\n"
        " raise SystemExit(int(sys.argv[2]))\n",
        encoding="utf-8",
    )
    argv = [sys.executable, str(script), str(destination), str(remove_exit_code), str(detached)]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    event: [{"hooks": [{"type": "command", "command": command}]}]
                    for event in ("WorktreeCreate", "WorktreeRemove")
                }
            }
        ),
        encoding="utf-8",
    )
    git_repository.commit_all(repo, "hook設定を保存")
    selected = tmp_path / "selection.yaml"
    _selection(selected)
    code, record, stderr = _create_in(capsys, monkeypatch, "hook-owner", repo, selected)
    assert code == 0 and record["worktree"] == str(destination), stderr
    diagnostics = pathlib.Path(record["hook_diagnostics"]["WorktreeCreate"])
    assert diagnostics.parent == managed_temp.temp_root_path()
    observed = json.loads((diagnostics / "WorktreeCreate-0.json").read_text(encoding="utf-8"))
    assert observed["exit_code"] == 0 and observed["input"]["session_id"] == "hook-owner"
    assert git_repository.git_output(destination, "symbolic-ref", "--short", "HEAD") == record["branch"]
    assert git_repository.git_output(destination, "rev-parse", "HEAD") == record["initial_head"]
    assert (destination / "hook-result").read_text(encoding="utf-8") == "hookの成果"
    code, reused, stderr = _create_in(capsys, monkeypatch, "hook-owner", repo, selected)
    assert code == 0 and reused["reused"] and reused["worktree"] == str(destination), stderr
    assert reused["ready"] and reused["branch"] == record["branch"]
    _integration_selection(selected, monkeypatch, repo)
    head = git_repository.commit_all(destination, "hookの成果を保存")
    manifest = _cleanup_integration_manifest(tmp_path, handoff_text="検収済みhook成果")
    code, integrated, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 0 and integrated["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == head
    assert (repo / "hook-result").read_text(encoding="utf-8") == "hookの成果"
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0 and deleted["removed"] == ["lane-01"], stderr
    assert not destination.exists() and not pathlib.Path(record["record_path"]).exists()
    assert (diagnostics / "WorktreeCreate-0.json").is_file()


@pytest.mark.parametrize("returned", ["main", "other", "other-detached"])
@pytest.mark.cpu_limit(
    3.0,
    reason=(
        "実hookから返る主作業先/別所有のworktreeで、履歴と未保存成果が変わらない境界を残す。"
        "同じ実Git資源群の所有CPU観測と起動数から有限上限を設定する。"
    ),
)
def test_hook_cannot_adopt_preexisting_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], returned: str
) -> None:
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="基準")
    (repo / "user.txt").write_text("保存済み", encoding="utf-8")
    other = tmp_path / "other"
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir()
    destination = repo if returned == "main" else other
    argv = [sys.executable, "-c", "import sys;print(sys.argv[1])", str(destination)]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    settings.write_text(
        json.dumps({"hooks": {"WorktreeCreate": [{"hooks": [{"type": "command", "command": command}]}]}}),
        encoding="utf-8",
    )
    git_repository.commit_all(repo, "ユーザーの開始版とhook設定")
    mode = ("--detach",) if returned == "other-detached" else ("-b", "other-owner")
    git_repository.run_git(repo, "worktree", "add", *mode, str(other))
    for tree in (repo, other):
        (tree / "user.txt").write_text("未commitのユーザー成果", encoding="utf-8")
        (tree / "untracked.txt").write_text("未追跡のユーザー成果", encoding="utf-8")
    process = session_registry.owner_process()
    assert process is not None
    session_registry.publish(
        "other-writer", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(other), processes=(process,)
    )

    def snapshot() -> list[tuple[str, str, str, bytes, bytes]]:
        return [
            (
                git_repository.git_output(tree, "branch", "--show-current"),
                git_repository.git_output(tree, "rev-parse", "HEAD"),
                git_repository.git_output(tree, "diff", "--binary"),
                (tree / "user.txt").read_bytes(),
                (tree / "untracked.txt").read_bytes(),
            )
            for tree in (repo, other)
        ]

    before = snapshot()
    selected = tmp_path / "selection.yaml"
    _selection(selected)
    code, _result, _stderr = _create_in(capsys, monkeypatch, "new-hook-owner", repo, selected)
    assert snapshot() == before
    assert code == 2
    code, listed, stderr = _invoke(capsys, "delete", "--list")
    assert code == 0, stderr
    record = listed["records"][0]["record"]
    assert not record["ready"] and record["worktree"] == str(_fixed(repo, "lane-01"))
    assert pathlib.Path(record["record_path"]).is_file()
