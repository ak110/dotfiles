"""公開CLIからレーンの所有、保持、統合、終了時回収と再実行を確かめる。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import subprocess
import sys
from typing import Any

import pytest
import yaml

from agent_toolkit import atk
from agent_toolkit._atk import agents_exit_session, lane, managed_temp
from agent_toolkit._atk.wi import process_loop_session
from agent_toolkit._common import session_registry
from agent_toolkit._testing import git_repository


def _invoke(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any], str]:
    with pytest.raises(SystemExit) as ended:
        atk.main(["lane", *args])
    output = capsys.readouterr()
    assert isinstance(ended.value.code, int)
    return ended.value.code, json.loads(output.out) if output.out.strip() else {}, output.err


def _create_args(repo: pathlib.Path, selected: pathlib.Path) -> tuple[str, ...]:
    return ("create", "--repo", str(repo), "--base-branch", "main", "--lane", "lane-01", "--selection-file", str(selected))


def _selection(path: pathlib.Path, *, integrated: bool = True) -> None:
    state = "統合済み" if integrated else "未統合"
    path.write_text(
        yaml.safe_dump({"レーンの所要時間": [{"レーン": lane, "統合状態": state} for lane in ("lane-01", "lane-02")]}),
        encoding="utf-8",
    )


def _fixed(repo: pathlib.Path, lane_id: str) -> pathlib.Path:
    return repo.resolve() / ".claude" / "worktrees" / f"process-wi-{lane_id}"


def _create_in(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    session_id: str,
    repo: pathlib.Path,
    selected: pathlib.Path,
    *extra: str,
    lane_id: str = "lane-01",
) -> tuple[int, dict[str, Any], str]:
    monkeypatch.setenv(lane.SESSION_ENV, session_id)
    args = ("create", "--repo", str(repo), "--base-branch", "main", "--lane", lane_id, "--selection-file", str(selected))
    return _invoke(capsys, *args, *extra)


def _create(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *extra: str,
) -> tuple[pathlib.Path, pathlib.Path, dict[str, Any]]:
    monkeypatch.setenv(lane.SESSION_ENV, "lane-test-session")
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="初期状態")
    selected = tmp_path / "selection.yaml"
    _selection(selected)
    code, record, stderr = _invoke(
        capsys,
        *_create_args(repo, selected),
        *extra,
    )
    assert code == 0, stderr
    assert record["ready"]
    assert pathlib.Path(record["record_path"]).is_file()
    return repo, selected, record


def test_create_reuses_integrated_resources_then_delete_removes_only_owned_lane(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """統合後の再開は同じ資源を使い、終了時だけ所有資源を削除する。"""
    repo, selected, first = _create(tmp_path, monkeypatch, capsys)
    assert first["worktree"] == str(_fixed(repo, "lane-01")) and first["managed_temp"] is None
    # 固定作成先は主作業ツリーの状態へ未追跡として現れない。
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


@pytest.mark.parametrize("condition", ["unintegrated", "dirty", "unmerged", "active-session", "identity-less-session"])
def test_delete_preserves_unfinished_work_and_recovers_after_condition_is_resolved(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], condition: str
) -> None:
    """判定不能・未完了を削除せず、成立した後は同じ公開操作で回収する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    if condition == "unintegrated":
        _selection(selected, integrated=False)
    elif condition == "dirty":
        (worktree / "unfinished.txt").write_text("未commitの成果", encoding="utf-8")
    elif condition == "unmerged":
        git_repository.commit_all(worktree, "未統合の成果")
    else:
        # 生存するプロセスを記録した実行中の記録と、識別を持たない実行中の記録は、どちらも担当の終了を確認できない。
        owner = session_registry.owner_process()
        assert owner is not None
        processes = (owner,) if condition == "active-session" else ()
        session_registry.publish("child", terminal=False, cwd=str(worktree), processes=processes)
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and held["retained"] and worktree.is_dir()
    assert pathlib.Path(record["record_path"]).is_file()
    before = pathlib.Path(record["record_path"]).read_bytes()
    code, listed, stderr = _invoke(capsys, "delete", "--list", "--session-id", "lane-test-session")
    assert code == 0, stderr
    assert listed["records"][0]["record_path"] == record["record_path"]
    assert pathlib.Path(record["record_path"]).read_bytes() == before
    if condition == "unintegrated":
        _selection(selected)
    elif condition == "dirty":
        git_repository.commit_all(worktree, "保存した成果")
        git_repository.run_git(repo, "merge", "--ff-only", record["branch"])
    elif condition == "unmerged":
        git_repository.run_git(repo, "merge", "--ff-only", record["branch"])
    else:
        session_registry.publish("child", terminal=True, cwd=str(worktree))
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


def test_delete_treats_session_whose_recorded_processes_exited_as_finished(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """所有側が終端を公開せずに終了し、記録した全プロセスが存在しない担当は終了として回収する。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    with subprocess.Popen([sys.executable, "-c", "pass"]) as exited:
        identity = session_registry.ProcessIdentity.of(exited.pid)
        exited.wait()
    assert identity is not None
    session_registry.publish("child", terminal=False, cwd=str(worktree), processes=(identity,))
    assert lane._session_activity(worktree.resolve()) is None  # pylint: disable=protected-access
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


def test_cleanup_uses_branch_refs_when_tags_have_the_same_names(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """同名tagで未統合成果の判定を変えず、統合後はbranchだけを回収する。"""
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    git_repository.commit_all(worktree, "未統合の成果")
    git_repository.run_git(repo, "tag", "main", f"refs/heads/{record['branch']}")
    git_repository.run_git(repo, "tag", record["branch"], "refs/heads/main")
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and held["retained"] and worktree.is_dir()
    git_repository.run_git(repo, "merge", "--ff-only", f"refs/heads/{record['branch']}")
    code, removed, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert removed["removed"] == ["lane-01"] and not worktree.exists()
    refs = git_repository.git_output(repo, "for-each-ref", "--format=%(refname)").splitlines()
    assert f"refs/heads/{record['branch']}" not in refs
    assert f"refs/tags/{record['branch']}" in refs and "refs/tags/main" in refs


def test_live_external_process_holds_worktree_until_it_ends(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """担当が終端しても、その作業先を使う外部プロセスを削除で巻き込まない。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    with subprocess.Popen(
        [sys.executable, "-c", "import sys;print('ready',flush=True);sys.stdin.read()"],
        cwd=record["worktree"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as child:
        assert child.stdout is not None and child.stdout.readline().strip() == "ready"
        try:
            code, held, _stderr = _invoke(capsys, "delete")
            assert code == 1 and held["retained"] and pathlib.Path(record["worktree"]).is_dir()
        finally:
            child.communicate(input="", timeout=30)
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


def test_project_teardown_failure_preserves_record_and_retries_remaining_cleanup(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """固有撤去の失敗後も資源と記録を失わず、成功後の回収へ進める。"""
    permit = tmp_path / "allow-delete"
    commands = tmp_path / "commands.json"
    commands.write_text(
        json.dumps(
            {
                "delete": [
                    sys.executable,
                    "-c",
                    "import pathlib,sys;sys.exit(0 if pathlib.Path(sys.argv[1]).exists() else 3)",
                    str(permit),
                ]
            }
        ),
        encoding="utf-8",
    )
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys, "--commands-file", str(commands))
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and held["retained"] and pathlib.Path(record["record_path"]).is_file()
    permit.touch()
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


def test_cleanup_rechecks_git_registration_after_project_teardown(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """固有手順の後にbranchが変わった作業先を削除せず、元の登録へ戻った後だけ回収する。"""
    commands = tmp_path / "commands.json"
    commands.write_text(json.dumps({"delete": ["git", "-C", "{worktree}", "switch", "-c", "different"]}), encoding="utf-8")
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys, "--commands-file", str(commands))
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and "worktreeの登録" in held["retained"][0]["reason"]
    worktree = pathlib.Path(record["worktree"])
    assert worktree.is_dir() and pathlib.Path(record["record_path"]).is_file()
    git_repository.run_git(worktree, "switch", record["branch"])
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]
    assert "different" in git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()


@pytest.mark.parametrize("phase", ["create", "prepare"])
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


def test_interrupted_lane_can_transfer_to_new_session_without_losing_uncommitted_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """終端済み担当の資源を新しい所有sessionへ再登録し、中断した成果を保持する。"""
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


def test_fixed_placement_is_shared_across_sessions_callers_and_recreation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じrepo・laneは別session・linked worktreeからの作成と回収後の再作成で同じパスになり、別lane・別repoは区別される。"""
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="初期状態")
    other = git_repository.init_repository(tmp_path / "other", initial_branch="main", commit_message="初期状態")
    selected = tmp_path / "selection.yaml"
    _selection(selected)
    # process-loopと同じく主作業ツリー配下のlinked worktreeを呼出し元にする。
    caller = repo / ".claude" / "worktrees" / "process-loop"
    git_repository.run_git(repo, "worktree", "add", "-b", "process-loop", str(caller))
    observed: list[str] = []
    for session_id, origin in (("session-a", repo), ("session-b", caller), ("session-c", repo)):
        code, created, stderr = _create_in(capsys, monkeypatch, session_id, origin, selected)
        assert code == 0, stderr
        assert not created["reused"] and created["session_id"] == session_id
        observed.append(created["worktree"])
        if session_id == "session-a":
            # 終了時回収の後に同じパスへ再作成する。
            code, deleted, stderr = _invoke(capsys, "delete")
            assert code == 0 and deleted["removed"] == ["lane-01"], stderr
        # session-bの資源は回収しないまま残し、session-cの作成が回収条件を確かめて同じパスへ再作成する。
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
    # 呼出し元のlinked worktreeの内側へレーンを入れ子で作成しない。
    assert not (caller / ".claude" / "worktrees").exists()


@pytest.mark.parametrize("condition", ["dirty", "unintegrated", "active-session"])
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
        session_registry.publish("child", terminal=False, cwd=str(worktree), processes=(owner,))
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


def test_project_create_procedure_receives_the_fixed_placement(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """固有create手順にも同じ固定作成先を渡し、回収後の再作成でも同じパスを使う。"""
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


def test_fixed_placement_does_not_overwrite_unregistered_entity(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """所有登録の無い実体が固定作成先にあれば変更せず、理由と回復操作を返す。"""
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


def test_legacy_managed_temp_lane_is_resumed_and_cleaned_with_its_area(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """旧配置の登録は同じ作成先のまま再開でき、回収時に個別managed-tempも回収する。"""
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


def test_exit_entrypoint_cleans_resources_even_when_interactive_host_is_unidentified(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """終了対象の識別と資源回収を分け、ホスト不明でも登録済みの安全な資源を回収する。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)

    def unsupported(**_kwargs: Any) -> tuple[str, None]:
        return "unsupported", None

    monkeypatch.setattr(agents_exit_session, "request_termination", unsupported)
    with pytest.raises(SystemExit) as ended:
        atk.main(["agents-exit-session"])
    assert ended.value.code == 0
    observed = json.loads(capsys.readouterr().out)
    assert observed["status"] == "unsupported" and observed["lane_cleanup"]["removed"] == ["lane-01"]
    assert not pathlib.Path(record["worktree"]).exists()


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_process_loop_child_exit_cleans_before_normal_or_abnormal_classification(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str, exit_code: int
) -> None:
    """両engineの子終了境界で同じ回収を実行し、異常終了後も判定より前に資源を保全する。"""
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="初期状態")
    selected = tmp_path / "selection.yaml"
    snapshot = tmp_path / "created.json"
    _selection(selected)
    script = tmp_path / "child.py"
    script.write_text(
        "import contextlib,io,json,pathlib,sys\n"
        "from agent_toolkit import atk\n"
        "stream=io.StringIO()\n"
        "with contextlib.redirect_stdout(stream):\n"
        "    try:\n"
        "        atk.main(['lane','create','--repo',sys.argv[1],'--base-branch','main',"
        "'--lane','lane-01','--selection-file',sys.argv[2]])\n"
        "    except SystemExit as result:\n"
        "        if result.code: raise\n"
        "pathlib.Path(sys.argv[3]).write_text(stream.getvalue(),encoding='utf-8')\n"
        "raise SystemExit(int(sys.argv[4]))\n",
        encoding="utf-8",
    )

    def child_argv(*_args: Any, **_kwargs: Any) -> tuple[list[str], None]:
        # 外部ホストの起動だけを有限の実プロセスへ差し替え、CLI・登録・Git・回収は実物を使う。
        return [sys.executable, str(script), str(repo), str(selected), str(snapshot), str(exit_code)], None

    monkeypatch.setattr(process_loop_session, "_build_session_argv", child_argv)
    args = argparse.Namespace(no_update=True)

    def execute() -> bool:
        return process_loop_session.run_process_session(
            args,
            repo,
            "test",
            dict(os.environ),
            orchestrator=engine,
            model="test-model",
            effort="low",
            resume_pending=False,
            dotfiles_root=None,
        )

    if exit_code:
        with pytest.raises(SystemExit) as ended:
            execute()
        assert ended.value.code == exit_code
    else:
        assert not execute()
    created = json.loads(snapshot.read_text(encoding="utf-8"))
    assert not pathlib.Path(created["worktree"]).exists() and not pathlib.Path(created["record_path"]).exists()
    assert repo.is_dir() and selected.is_file()


def _integration_args(record: dict[str, Any], repo: pathlib.Path, manifest: pathlib.Path, head: str) -> tuple[str, ...]:
    return (
        "integrate",
        "--record",
        record["record_path"],
        "--reviewed-head",
        head,
        "--target-worktree",
        str(repo),
        "--target-branch",
        "main",
        "--manifest",
        str(manifest),
    )


def _integration_selection(path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notes = path.parent / "notes"
    (notes / "processing").mkdir(parents=True, exist_ok=True)
    (notes / "processing/awi.md").write_text(
        "---\ntype: awi\nsource: test\ntarget_repo: github.com/test/repo\n---\n\n# 観測\n\n"
        "## 反映内容と反映先\n\n観測を保存する。\n\n## 完成条件\n\n観測を完了する。\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    document = {
        "選定": [{"WI": "awi.md", "レーン": "lane-01", "書込対象": [], "鮮度": {"status": "current", "later_commit_count": 0}}],
        "レーンの所要時間": [
            {"レーン": "lane-01", "統合状態": "未統合", "実装秒数": 60, "統合秒数": 60, "根拠": "隔離した観測試験"}
        ],
        "初回配分": {
            "候補WI": ["awi.md"],
            "レーン割当": {"awi.md": "lane-01"},
            "不可分成分": [{"WI": ["awi.md"], "結合条件": []}],
        },
    }
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")


@pytest.mark.parametrize("planned", [False, True])
def test_integrate_public_lifecycle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], planned: bool
) -> None:
    """レビュー済み成果を公開操作で統合し、計画あり・なしの最終化後だけ回収できる。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch)
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
                "wis": [{"name": "awi.md", "commands": []}],
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


def test_integrate_resume_and_preservation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """最終化の途中失敗はGit統合と成功済み保存を繰り返さず、未完了のWI終端から再開する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch)
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
                "wis": [{"name": "awi.md", "commands": [terminal]}],
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
        session_registry.publish("child", terminal=False, cwd=str(worktree))
    code, _result, _stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 2
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == old_head
    assert worktree.exists() and pathlib.Path(record["record_path"]).exists()


def test_integrate_rebase_returns_mapping_and_requires_diagnostic_acceptance(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """分岐した未push成果は内容比較と再検証を保存し、受理後だけffと最終化へ進む。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature").write_text("成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "成果")
    (repo / "upstream").write_text("先行統合", encoding="utf-8")
    before = git_repository.commit_all(repo, "先行統合")
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    terminal = [
        sys.executable,
        "-c",
        "import subprocess,sys;assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==sys.argv[1]",
        head,
    ]
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "awi.md", "commands": [terminal]}],
                "handoff": str(handoff),
                "verification": [
                    [
                        sys.executable,
                        "-c",
                        "import pathlib;assert pathlib.Path('feature').read_text()=='成果';"
                        "assert pathlib.Path('upstream').exists()",
                    ]
                ],
            }
        ),
        encoding="utf-8",
    )
    args = _integration_args(record, repo, manifest, head)
    code, pending, stderr = _invoke(capsys, *args)
    assert code == 1, stderr
    assert pending["phase"] == "awaiting-validation"
    mapping = pathlib.Path(pending["rewrite_map"])
    assert mapping.is_file()
    assert (mapping.parent / ".agent-toolkit-managed-temp.json").is_file()
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == before
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["wis"][0]["commands"][0][-1] = pending["new_head"]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    code, _result, stderr = _invoke(capsys, *args, "--accept-rebased-head", pending["new_head"])
    assert code == 2 and "--amend-finalization" in stderr
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", pending["new_head"], "--amend-finalization")
    assert code == 0 and final["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == pending["new_head"]
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0 and deleted["removed"] == ["lane-01"], stderr
    assert mapping.is_file()


@pytest.mark.parametrize("remove_exit_code", [0, 7])
@pytest.mark.parametrize("detached", [False, True])
def test_hook_owned_resource(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    remove_exit_code: int,
    detached: bool,
) -> None:
    """hookの返却先をlane所有へ接続し、再利用と終了時Removeを同じ資源へ適用する。"""
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
    assert (diagnostics / ".agent-toolkit-managed-temp.json").is_file()
    observed = json.loads((diagnostics / "WorktreeCreate-0.json").read_text(encoding="utf-8"))
    assert observed["exit_code"] == 0 and observed["input"]["session_id"] == "hook-owner"
    assert git_repository.git_output(destination, "symbolic-ref", "--short", "HEAD") == record["branch"]
    assert git_repository.git_output(destination, "rev-parse", "HEAD") == record["initial_head"]
    assert (destination / "hook-result").read_text(encoding="utf-8") == "hookの成果"
    code, reused, stderr = _create_in(capsys, monkeypatch, "hook-owner", repo, selected)
    assert code == 0 and reused["reused"] and reused["worktree"] == str(destination), stderr
    assert reused["ready"] and reused["branch"] == record["branch"]
    _integration_selection(selected, monkeypatch)
    head = git_repository.commit_all(destination, "hookの成果を保存")
    handoff = tmp_path / "handoff.md"
    handoff.write_text("検収済みhook成果", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps({"plans": [], "wis": [{"name": "awi.md", "commands": []}], "verification": [], "handoff": str(handoff)}),
        encoding="utf-8",
    )
    code, integrated, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 0 and integrated["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == head
    assert (repo / "hook-result").read_text(encoding="utf-8") == "hookの成果"
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0 and deleted["removed"] == ["lane-01"], stderr
    assert not destination.exists() and not pathlib.Path(record["record_path"]).exists()
    assert (diagnostics / "WorktreeCreate-0.json").is_file()


@pytest.mark.parametrize("returned", ["main", "other", "other-detached"])
def test_hook_cannot_adopt_preexisting_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], returned: str
) -> None:
    """新規hookの既存作業先を拒否し、主作業ツリーと別所有の成果・branchを変えない。"""
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
    session_registry.publish("other-writer", terminal=False, cwd=str(other), processes=(process,))

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


def test_integrate_observation_and_saved_plan(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """観測のみの再開はGitを変更せず、保存済み計画と延期WIを検収して最終化する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch)
    saved = tmp_path / "plan.md"
    saved.write_text("保存済み計画", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [{"name": "plan.md", "commands": [], "saved": str(saved)}],
                "wis": [{"name": "awi.md", "commands": []}],
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
    _integration_selection(selected, monkeypatch)
    worktree = pathlib.Path(record["worktree"])
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    wis = [{"name": "awi.md", "commands": []}]
    for index in range(2):
        (worktree / f"feature-{index}").write_text("成果", encoding="utf-8")
        head = git_repository.commit_all(worktree, "成果")
        if index:
            document = yaml.safe_load(selected.read_text(encoding="utf-8"))
            document["選定"].append(dict(document["選定"][0], WI="additional.md"))
            document["追加WI"] = ["additional.md"]
            document["レーンの所要時間"][0]["統合状態"] = "未統合"
            selected.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
            notes = tmp_path / "notes/processing"
            (notes / "additional.md").write_bytes((notes / "awi.md").read_bytes())
            wis.append({"name": "additional.md", "commands": []})
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
    _integration_selection(selected, monkeypatch)
    remote = git_repository.init_bare_repository(tmp_path / "origin.git")
    git_repository.run_git(repo, "remote", "add", "origin", str(remote))
    worktree = pathlib.Path(record["worktree"])
    (worktree / "published").write_text("公開済み", encoding="utf-8")
    head = git_repository.commit_all(worktree, "公開済み")
    git_repository.run_git(worktree, "push", "origin", record["branch"])
    git_repository.run_git(repo, "update-ref", "-d", f"refs/remotes/origin/{record['branch']}")
    (repo / "upstream").write_text("先行統合", encoding="utf-8")
    before = git_repository.commit_all(repo, "先行統合")
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "awi.md", "commands": []}],
                "handoff": str(handoff),
                "verification": [[sys.executable, "-c", "pass"]],
            }
        ),
        encoding="utf-8",
    )
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 2 and "push済みcommit" in stderr
    assert git_repository.git_output(worktree, "rev-parse", "HEAD") == head
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == before


def test_integrate_restarts_after_reviewed_correction(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Git統合前の検証失敗は記録を残し、是正後の独立文脈レビュー入力で公開操作から再開する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature").write_text("成果", encoding="utf-8")
    reviewed = git_repository.commit_all(worktree, "成果")
    (repo / "upstream").write_text("先行統合", encoding="utf-8")
    git_repository.commit_all(repo, "先行統合")
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    value = {
        "plans": [],
        "wis": [{"name": "awi.md", "commands": []}],
        "handoff": str(handoff),
        "verification": [[sys.executable, "-c", "raise SystemExit(3)"]],
    }
    manifest.write_text(json.dumps(value), encoding="utf-8")
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, reviewed))
    assert code == 2 and "verification-0" in stderr
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
    assert stored["previous_integration"]["phase"] == "rebased"
    assert "verification-0" in result["completed"]
    assert (repo / "feature").read_text(encoding="utf-8") == "是正済み"


def test_integrate_uses_reviewed_oid_when_branch_advances_before_merge(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """最後の検収後にbranchが進んでも、未レビューcommitをffへ混入しない。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "reviewed").write_text("受理済み", encoding="utf-8")
    head = git_repository.commit_all(worktree, "レビュー済み")
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps({"plans": [], "wis": [{"name": "awi.md", "commands": []}], "handoff": str(handoff), "verification": []}),
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
