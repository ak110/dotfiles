"""公開CLIからレーンの所有、保持、統合、終了時回収と再実行を確かめる。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import json
import os
import pathlib
import shlex
import stat
import subprocess
import sys
import types
from collections.abc import Callable, Generator, Iterator
from typing import Any, Literal

import pytest
import yaml

from agent_toolkit import atk
from agent_toolkit._atk import agents_exit_session, lane, managed_temp
from agent_toolkit._atk.wi import process_loop_session
from agent_toolkit._common import session_registry, task_documents, wait_schedule
from agent_toolkit._testing import git_repository, isolation
from agent_toolkit._testing.session_release_support import retained_executor


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


@pytest.mark.skipif(os.name != "posix", reason="POSIXのディレクトリ権限を実機で検証する")
@pytest.mark.parametrize("dirty", [False, True])
@pytest.mark.parametrize("operation", ["delete", "recover"])
def test_delete_recovers_permissions_before_checking_untracked_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], dirty: bool, operation: str
) -> None:
    """読めない木の未追跡内容を再判定し、保存済みの木だけ登録と共に撤去する。"""
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    locked = worktree / "locked"
    locked.mkdir()
    payload = locked / "payload"
    payload.write_text("成果", encoding="utf-8")
    if not dirty:
        git_repository.commit_all(worktree, "保存した成果")
        git_repository.run_git(repo, "merge", "--ff-only", record["branch"])
    if operation == "recover":
        _legacy(record)
    locked.chmod(0)
    try:
        code, _listed, stderr = _invoke(capsys, "delete", "--list")
        assert code == 0, stderr
        assert stat.S_IMODE(locked.stat().st_mode) == 0
        code, result, stderr = _invoke(capsys, operation, "--session-id", "lane-test-session")
        if dirty:
            assert code == 1, stderr
            assert "未commitまたは未追跡" in result["retained"][0]["reason"]
            assert payload.read_text(encoding="utf-8") == "成果"
            assert pathlib.Path(record["record_path"]).exists()
            assert str(worktree) in git_repository.git_output(repo, "worktree", "list", "--porcelain")
        else:
            assert code == 0, stderr
            if operation == "delete":
                assert result["removed"] == ["lane-01"]
                assert not pathlib.Path(record["record_path"]).exists()
            else:
                assert result["recovered"][0]["phase"] == "done"
            assert not worktree.exists()
            assert str(worktree) not in git_repository.git_output(repo, "worktree", "list", "--porcelain")
    finally:
        if locked.exists():
            locked.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="POSIXの所有者とmodeを検証する")
@pytest.mark.parametrize("operation", ["delete", "recover"])
def test_lane_keeps_foreign_os_owner_and_reports_owner_action(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: str
) -> None:
    """所有者が異なる木はmode・内容・Git登録を保持し、所有者による回収を案内する。"""
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    locked = worktree / "foreign-owner"
    locked.mkdir()
    payload = locked / "payload"
    payload.write_text("保持する成果", encoding="utf-8")
    metadata = locked.lstat()
    foreign_uid = metadata.st_uid + 1
    locked.chmod(0)
    if operation == "recover":
        _legacy(record)
    original_scandir = os.scandir

    @contextlib.contextmanager
    def foreign_owner_entries(path: Any, **kwargs: Any) -> Generator[Iterator[Any]]:
        with original_scandir(path, **kwargs) as entries:

            def entry_with_owner(entry: os.DirEntry[str]) -> Any:
                observed = entry.stat(follow_symlinks=False)
                if (observed.st_dev, observed.st_ino) != (metadata.st_dev, metadata.st_ino):
                    return entry
                fields = list(observed)
                fields[4] = foreign_uid
                return types.SimpleNamespace(name=entry.name, stat=lambda **_kwargs: os.stat_result(fields))

            yield (entry_with_owner(entry) for entry in entries)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "scandir", foreign_owner_entries)
            code, result, stderr = _invoke(capsys, operation, "--session-id", "lane-test-session")
        assert code == 1, stderr
        retained = result["retained"][0]
        assert str(locked) in retained["reason"] and f"uid={foreign_uid}" in retained["reason"]
        assert str(locked) in retained["next_action"] and f"uid={foreign_uid}" in retained["next_action"]
        assert "所有者" in retained["next_action"]
        assert stat.S_IMODE(locked.stat().st_mode) == 0
        assert pathlib.Path(record["record_path"]).exists()
        assert str(worktree) in git_repository.git_output(repo, "worktree", "list", "--porcelain")
    finally:
        locked.chmod(0o700)
    assert payload.read_text(encoding="utf-8") == "保持する成果"


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


@pytest.mark.parametrize("entrypoint", ["delete", "cli", "exit", "reuse", "fixed", "residual"])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_lane_releases_retained_executor_through_owner_monitor(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entrypoint: str,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """cwd保持中の終端資源を所有側が解放し、公開CLI・終了時回収・再利用・移管を通して未回収結果を取得できる。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    monkeypatch.setattr(lane, "get_wait_timeout", lambda _bucket: 5.0)
    async with retained_executor(
        pathlib.Path(record["worktree"]),
        host_environ(),
        monkeypatch,
        keep_executor=entrypoint == "residual",
    ) as retained:
        if entrypoint == "cli":
            environment = host_environ()
            for name in (*isolation.HOME_ENVIRONMENT_NAMES, *isolation.CONFIG_DIRECTORY_ENVIRONMENT_NAMES):
                environment[name] = os.environ[name]
            command = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    str(pathlib.Path(atk.__file__).resolve()),
                    "lane",
                    "delete",
                    "--session-id",
                    "lane-test-session",
                ],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=15,
            )
            code, result, stderr = command.returncode, json.loads(command.stdout), command.stderr
        elif entrypoint == "exit":
            monkeypatch.setattr(agents_exit_session, "request_termination", lambda **_kwargs: ("unsupported", None))

            def exit_session() -> tuple[int, dict[str, Any], str]:
                with pytest.raises(SystemExit) as ended:
                    atk.main(["agents-exit-session"])
                output = capsys.readouterr()
                assert isinstance(ended.value.code, int)
                return ended.value.code, json.loads(output.out)["lane_cleanup"], output.err

            code, result, stderr = await asyncio.to_thread(exit_session)
        elif entrypoint in {"reuse", "fixed"}:
            monkeypatch.setenv(lane.SESSION_ENV, "next-session")
            extra = ("--reuse-record", record["record_path"]) if entrypoint == "reuse" else ()
            code, result, stderr = await asyncio.to_thread(_invoke, capsys, *_create_args(repo, selected), *extra)
        else:
            code, result, stderr = await asyncio.to_thread(_invoke, capsys, "delete")
        if entrypoint == "residual":
            assert code == 1 and retained.executor.poll() is None
            assert retained.session_id in result["retained"][0]["reason"]
            assert "stop" in result["retained"][0]["reason"]
            assert pathlib.Path(record["worktree"]).is_dir()
            assert session_registry.resolve(retained.session_id).state is session_registry.Resolution.RELEASED
            return
        assert code == 0, stderr
        assert retained.executor.poll() == 0
        assert retained.release_calls == [retained.session_id]
        assert session_registry.resolve(retained.session_id).state is session_registry.Resolution.RELEASED
        if entrypoint in {"delete", "cli", "exit"}:
            assert result["removed"] == ["lane-01"]
            assert not pathlib.Path(record["worktree"]).exists()
            branches = git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
            assert record["branch"] not in branches
        else:
            assert result["session_id"] == "next-session"
            assert result["worktree"] == record["worktree"]
        assert not list(session_registry.registry_directory(retained.state_root).glob("*.release-*"))
        assert retained.wait_result() == 0
        assert json.loads(capsys.readouterr().out)["agent_message"] == "回収後も受領する結果"
        resumed = await retained.resume("再開")
        assert resumed["delivery"] == "reply_started"


@pytest.mark.parametrize("terminal", [False, True])
def test_lane_preserves_session_when_running_or_owner_unresponsive(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], terminal: bool
) -> None:
    """実行中は終端・結果回収後のstopを案内し、無応答の所有側も資源を保持して再回収へ接続する。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    owner = session_registry.owner_process()
    assert owner is not None
    session_registry.publish("held-session", terminal=terminal, cwd=record["worktree"], processes=(owner,))
    monkeypatch.setattr(lane, "get_wait_timeout", lambda _bucket: 0.02)
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1
    reason = held["retained"][0]["reason"]
    assert "held-session" in reason and "stop" in reason and "lane delete" in reason
    if not terminal:
        assert reason.index("自然終端") < reason.index("atk agents wait") < reason.index("stop") < reason.index("lane delete")
        assert session_registry.resolve("held-session").state is session_registry.Resolution.RUNNING
    assert pathlib.Path(record["worktree"]).is_dir()
    assert not list(session_registry.registry_directory().glob("*.release-*"))


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
    """Claude・Codexのどちらでもprocess-loopの子セッションの終了時に同じ回収を実行し、異常終了でも終了の判定より前にレーン資源を回収して、対象リポジトリと選定ファイルは残す。"""
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


def _integration_selection(path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, repo: pathlib.Path) -> None:
    # WIの所属と統合先の識別子を揃え、模擬remoteへの外部fetchは行わない。
    git_repository.run_git(repo, "remote", "add", "origin", "https://github.com/test/repo.git")
    git_repository.run_git(repo, "config", "remote.origin.skipFetchAll", "true")
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
        "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
        "レーンの所要時間": [
            {
                "レーン": "lane-01",
                "統合状態": "未統合",
                "実装秒数": 60,
                "統合秒数": 60,
                "補正前の実装秒数": 60,
                "補正前の統合秒数": 60,
                "根拠": "隔離した観測試験",
            }
        ],
        "初回配分": {
            "候補WI": ["awi.md"],
            "レーン割当": {"awi.md": "lane-01"},
            "不可分成分": [{"WI": ["awi.md"], "結合条件": []}],
        },
    }
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")


def prepare_lane_verification(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    verification_argv: list[str],
) -> list[str]:
    """共有検証枠の競合試験へ、公開createで準備したlane integrateのargvを渡す。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    head = git_repository.git_output(pathlib.Path(record["worktree"]), "rev-parse", "HEAD")
    handoff = tmp_path / "handoff.md"
    handoff.write_text("共有検証枠の観測", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "awi.md", "commands": []}],
                "verification": [verification_argv],
                "handoff": str(handoff),
            }
        ),
        encoding="utf-8",
    )
    return [
        sys.executable,
        str(pathlib.Path(atk.__file__).resolve()),
        "lane",
        *_integration_args(record, repo, manifest, head),
        "--session-id",
        record["session_id"],
    ]


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
    _integration_selection(selected, monkeypatch, repo)
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
    # 対応表は所有登録と同じ状態ディレクトリへ置き、managed-tempの失効に依存しない。
    assert mapping.parent == pathlib.Path(record["record_path"]).with_name("lane-01-integration")
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
    # 回収後も対応表を証拠として回収結果の領域に残し、状態ディレクトリからは外す。
    assert not mapping.exists()
    assert (pathlib.Path(deleted["integration_materials"]["lane-01"]) / mapping.name).is_file()


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
    _integration_selection(selected, monkeypatch, repo)
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
    """新規作成でhookが既存の作業先を返した場合は拒否し、主作業ツリーと別所有の成果・branchを変えない。"""
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
    _integration_selection(selected, monkeypatch, repo)
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
    _integration_selection(selected, monkeypatch, repo)
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
    _integration_selection(selected, monkeypatch, repo)
    remote = git_repository.init_bare_repository(tmp_path / "origin.git")
    git_repository.run_git(repo, "remote", "set-url", "origin", str(remote))
    git_repository.run_git(repo, "config", "--unset", "remote.origin.skipFetchAll")
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


def test_integrate_ignores_session_in_nested_lane_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """統合先配下の別レーンの担当を所有へ含めずに統合を完了できる。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    nested = repo / ".claude/worktrees/other"
    git_repository.run_git(repo, "worktree", "add", "-b", "other", str(nested))
    session_registry.publish("other-writer", terminal=False, cwd=str(nested))
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps({"plans": [], "wis": [{"name": "awi.md", "commands": []}], "handoff": str(handoff)}),
        encoding="utf-8",
    )
    code, result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, record["initial_head"]))
    assert code == 0 and result["phase"] == "finalized", stderr


@pytest.mark.parametrize("relative", [".", "direct"])
def test_integrate_rejects_session_in_target_outside_nested_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], relative: str
) -> None:
    """統合先の直下と通常の子ディレクトリを使う合意外の担当を検出する。"""
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    session_registry.publish("target-writer", terminal=False, cwd=str(repo / relative))
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, tmp_path / "unused", record["initial_head"]))
    assert code == 2 and "現在の書込所有に含まれない担当が稼働中" in stderr


def test_worktree_activity_ignores_nested_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """解放対象の選別と再走査で、入れ子の別worktreeの担当を区別する。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    nested = worktree / ".claude/worktrees/nested"
    git_repository.run_git(worktree, "worktree", "add", "-b", "nested", str(nested))
    session_registry.publish("nested-writer", terminal=False, cwd=str(nested))
    assert lane._worktree_activity(worktree) is None  # pylint: disable=protected-access
    session_registry.publish("direct-writer", terminal=False, cwd=str(worktree))
    assert "direct-writer" in (lane._worktree_activity(worktree) or "")  # pylint: disable=protected-access


@pytest.mark.parametrize("source", ["cwd", "argv"])
def test_process_activity_ignores_nested_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    """プロセスのcwdと絶対引数の双方で入れ子の別worktreeを区別する。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    nested = worktree / ".claude/worktrees/nested"
    git_repository.run_git(worktree, "worktree", "add", "-b", "nested", str(nested))
    location = nested

    class Process:
        """外部プロセスの照会を決定論的なパスへ向ける。"""

        pid = 12345
        info = {"username": lane.psutil.Process().username()}

        def cwd(self) -> str:
            """照会対象のcwdを返す。"""
            return str(location if source == "cwd" else tmp_path)

        def cmdline(self) -> list[str]:
            """照会対象の絶対引数を返す。"""
            return ["program", f"--file={location / 'file'}"] if source == "argv" else ["program"]

    monkeypatch.setattr(lane.psutil, "process_iter", lambda *_args, **_kwargs: [Process()])
    assert lane._process_activity(worktree) is None  # pylint: disable=protected-access
    location = worktree
    assert lane._process_activity(worktree)  # pylint: disable=protected-access


def _rebased_integration(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *, failed: bool = False
) -> tuple[pathlib.Path, dict[str, Any], pathlib.Path, dict[str, Any], dict[str, Any]]:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature").write_text("成果", encoding="utf-8")
    reviewed = git_repository.commit_all(worktree, "成果")
    (repo / "upstream").write_text("先行統合", encoding="utf-8")
    git_repository.commit_all(repo, "先行統合")
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    value: dict[str, Any] = {
        "plans": [],
        "wis": [{"name": "awi.md", "commands": []}],
        "handoff": str(handoff),
        "verification": [[sys.executable, "-c", "raise SystemExit(3)" if failed else "pass"]],
    }
    manifest.write_text(json.dumps(value), encoding="utf-8")
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, reviewed))
    assert code == (2 if failed else 1), stderr
    if failed:
        assert "verification-0" in stderr
    state = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))["integration"]
    return repo, record, manifest, value, state


@pytest.mark.parametrize("phase", ["rebased", "awaiting-validation", "verified"])
@pytest.mark.parametrize("changed", ["verification", "plans", "wis", "handoff"])
@pytest.mark.parametrize("reviewed_key", ["old_head", "new_head"])
def test_integrate_restart_review_keeps_rebase_validation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    phase: str,
    changed: str,
    reviewed_key: str,
) -> None:
    """全ての工程と入力是正で、同じrebase結果を再検証・検収してから統合する。"""
    repo, record, manifest, value, original = _rebased_integration(tmp_path, monkeypatch, capsys)
    record_path = pathlib.Path(record["record_path"])
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    # Git統合前の永続状態を再現する。verifiedは受理後・ff前の中断に相当する。
    stored["integration"]["phase"] = phase
    record_path.write_text(json.dumps(stored), encoding="utf-8")
    if changed == "verification":
        value[changed] = [[sys.executable, "-c", "assert True"]]
    elif changed in {"plans", "wis"}:
        value[changed] = [{"name": "plan.md" if changed == "plans" else "awi.md", "commands": [[sys.executable, "-c", "pass"]]}]
    else:
        handoff = tmp_path / "corrected-handoff.md"
        handoff.touch()
        value[changed] = str(handoff)
    manifest.write_text(json.dumps(value), encoding="utf-8")
    args = _integration_args(record, repo, manifest, original[reviewed_key])
    code, pending, stderr = _invoke(capsys, *args, "--restart-review")
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    assert pending["new_head"] == original["new_head"] and pending["rewrite_map"] == original["rewrite_map"]
    previous = json.loads(record_path.read_text(encoding="utf-8"))["previous_integration"]
    assert previous == stored["integration"]
    assert pending["results"]["history-compare"] == original["results"]["history-compare"]
    assert pending["results"]["verification-0"]["record_path"] != original["results"]["verification-0"]["record_path"]
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == original["new_base"]
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", original["new_head"])
    assert code == 0 and final["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == original["new_head"]


def test_integrate_restart_review_after_failed_rebased_verification(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """検証失敗後、argvだけの是正をrebase前のレビューHEADで再開し検収を経る。"""
    repo, record, manifest, value, original = _rebased_integration(tmp_path, monkeypatch, capsys, failed=True)
    value["verification"] = [[sys.executable, "-c", "pass"]]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    args = _integration_args(record, repo, manifest, original["old_head"])
    code, pending, stderr = _invoke(capsys, *args, "--restart-review", "--accept-rebased-head", original["new_head"])
    assert code == 1 and pending["new_head"] == original["new_head"], stderr
    assert pending["phase"] == "awaiting-validation"
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", pending["new_head"])
    assert code == 0 and final["phase"] == "finalized", stderr


def test_integrate_rejects_changed_input_with_restart_guidance(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    record_property: Callable[[str, object], None],
) -> None:
    """manifest是正だけの再開が、検収へ戻る操作を拒否文から選べる。"""
    repo, record, manifest, value, original = _rebased_integration(tmp_path, monkeypatch, capsys)
    value["verification"] = [[sys.executable, "-c", "assert True"]]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, original["old_head"]))
    record_property("restart_diagnostic", stderr)
    assert code == 2
    assert all(word in stderr for word in ("--restart-review", "検証argv", "最終化argv", "handoff", "検収待ち"))


def _timed_integration(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *, rebased: bool
) -> tuple[pathlib.Path, dict[str, Any], pathlib.Path, str]:
    # 統合の操作より短い固有手順の上限で所有登録を作成する。
    # history-compareは同じ上限で動くため、負荷の揺れを吸収する余裕を残す。
    repo, selected, record = _create(tmp_path, monkeypatch, capsys, "--timeout", "5")
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature").write_text("成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "成果")
    if rebased:
        (repo / "upstream").write_text("先行統合", encoding="utf-8")
        git_repository.commit_all(repo, "先行統合")
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    return repo, record, handoff, head


_SLOW_ARGV = [sys.executable, "-c", "import time;time.sleep(6)"]


@pytest.mark.parametrize("rebased", [False, True])
def test_integrate_verification_is_not_limited_by_procedure_timeout(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rebased: bool
) -> None:
    """検証argvは自身の上限で終わり、固有手順向けのprocedure_timeoutで打ち切られずに統合が進む。"""
    repo, record, handoff, head = _timed_integration(tmp_path, monkeypatch, capsys, rebased=rebased)
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {"plans": [], "wis": [{"name": "awi.md", "commands": []}], "handoff": str(handoff), "verification": [_SLOW_ARGV]}
        ),
        encoding="utf-8",
    )
    code, result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    if rebased:
        assert code == 1 and result["phase"] == "awaiting-validation", stderr
    else:
        assert code == 0 and result["phase"] == "finalized", stderr
    assert "verification-0" in result["completed"]


def test_integrate_finalization_keeps_procedure_timeout(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """自身の上限を持たない最終化argvは、従来どおりprocedure_timeoutで打ち切られる。"""
    repo, record, handoff, head = _timed_integration(tmp_path, monkeypatch, capsys, rebased=False)
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {"plans": [], "wis": [{"name": "awi.md", "commands": [_SLOW_ARGV]}], "handoff": str(handoff), "verification": []}
        ),
        encoding="utf-8",
    )
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 2 and "wis:awi.md:0が終了124" in stderr, stderr


def _selection_with(path: pathlib.Path, wis: list[str], *, integrated: bool = True) -> None:
    """選定の項目と統合状態を持つ選定YAMLを書く。"""
    path.write_text(
        yaml.safe_dump(
            {
                "選定": [{"WI": name, "レーン": "lane-01"} for name in wis],
                "レーンの所要時間": [{"レーン": "lane-01", "統合状態": "統合済み" if integrated else "未統合"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def _legacy(record: dict[str, Any]) -> None:
    """選定情報を持たない旧形式の登録を再現する（選定原本も失効させる）。"""
    path = pathlib.Path(record["record_path"])
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored.pop("selection", None)
    path.write_text(json.dumps(stored), encoding="utf-8")
    pathlib.Path(record["selection_file"]).unlink(missing_ok=True)


@pytest.mark.parametrize("integrated", [True, False])
def test_delete_uses_held_selection_after_selection_file_expires(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], integrated: bool
) -> None:
    """選定原本が失効しても、所有登録が保持した統合状態で回収と保持を判定する。"""
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


def test_integrate_finalizes_and_cleans_after_selection_file_expires(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """統合の前に選定原本が失効しても、保持したWI集合とmanifestを比べ、最終化と終了時回収まで完了する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    # 選定の確定後に作業場所を開く通常の順序に合わせ、確定した選定を所有登録へ取り込む。
    code, _reused, stderr = _invoke(capsys, *_create_args(repo, selected))
    assert code == 0, stderr
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature.txt").write_text("成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "成果")
    handoff = tmp_path / "handoff.md"
    handoff.write_text("検収済み採否", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    wrong = {"plans": [], "wis": [{"name": "other.md", "commands": []}], "verification": [], "handoff": str(handoff)}
    manifest.write_text(json.dumps(wrong), encoding="utf-8")
    selected.unlink()
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 2 and "WI集合" in stderr
    manifest.write_text(json.dumps({**wrong, "wis": [{"name": "awi.md", "commands": []}]}), encoding="utf-8")
    code, result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 0 and result["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == head
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0 and deleted["removed"] == ["lane-01"], stderr


@pytest.mark.parametrize("entity", ["present", "missing"])
def test_recover_releases_legacy_registration_and_keeps_branch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], entity: str
) -> None:
    """旧形式の登録は元branchの成果を残して作成先を解放し、別sessionが同じ固定作成先へ作成できる。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "unmerged.txt").write_text("未統合の成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "未統合の成果")
    _legacy(record)
    if entity == "missing":
        # 作業ツリーの実体だけが失われ、Gitの登録が残った状態。
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


def test_recover_keeps_registrations_with_selection_information(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """選定原本か保持した選定情報を持つ登録は回復せず、通常の回収と再利用を案内する。"""
    _repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    before = pathlib.Path(record["record_path"]).read_bytes()
    for _ in range(2):
        code, result, _stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
        assert code == 1 and "回復の対象外" in result["retained"][0]["reason"]
        assert pathlib.Path(record["record_path"]).read_bytes() == before
        assert pathlib.Path(record["worktree"]).is_dir()
        # 2回目は原本だけが失効し、所有登録の保持値が残る場合を確かめる。
        selected.unlink(missing_ok=True)


@pytest.mark.parametrize("condition", ["dirty", "active-session", "foreign-owner", "partial-failure"])
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
        session_registry.publish("child", terminal=False, cwd=str(worktree), processes=(owner,))
    elif condition == "foreign-owner":
        stored = json.loads(record_path.read_text(encoding="utf-8"))
        stored["session_id"] = "another-session"
        record_path.write_text(json.dumps(stored), encoding="utf-8")
    else:
        original = lane._git  # pylint: disable=protected-access  # noqa: SLF001

        def _fail_once(repo_path: pathlib.Path, *argv: str, **kwargs: Any) -> str:
            del kwargs
            if argv[:2] == ("worktree", "remove"):
                monkeypatch.setattr(lane, "_git", original)
                raise ValueError("git worktreeが終了1: 模擬した失敗")
            return original(repo_path, *argv)

        monkeypatch.setattr(lane, "_git", _fail_once)
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


def test_reuse_switches_held_selection_to_current_selection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """再利用では前回の選定を引き継がず、今回の選定のWI集合と統合状態を保持する。"""
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
    assert stored["selection"] == {"lane_wis": ["current.md"], "integrated": False}
    current.unlink()
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and held["retained"][0]["reason"] == "選定の統合状態が未統合"


def test_integrate_rebuilds_expired_rebase_materials(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """検収待ちの対応表と検証記録が期限付き領域から失われても、再生成して検収後の統合へ進める。"""
    repo, record, manifest, _value, original = _rebased_integration(tmp_path, monkeypatch, capsys)
    record_path = pathlib.Path(record["record_path"])
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    expired = tmp_path / "expired-managed-temp"
    # 旧状態はmanaged-tempの対応表と検証記録を指し、その保存先が失効している。
    stored["integration"]["rewrite_map"] = str(expired / "rewrite-map.json")
    stored["integration"]["results"]["verification-0"]["record_path"] = str(expired / "record.json")
    record_path.write_text(json.dumps(stored), encoding="utf-8")
    args = _integration_args(record, repo, manifest, original["old_head"])
    code, pending, stderr = _invoke(capsys, *args)
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    mapping = pathlib.Path(pending["rewrite_map"])
    assert mapping.is_file() and mapping.parent == record_path.with_name("lane-01-integration")
    assert pathlib.Path(pending["results"]["verification-0"]["record_path"]).is_file()
    assert pending["new_head"] == original["new_head"]
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", original["new_head"])
    assert code == 0 and final["phase"] == "finalized", stderr


@pytest.mark.parametrize(
    ("environment", "expected_timeout"),
    [
        ({"CLAUDECODE": "1", "CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"}, 1740.0),
        ({"CLAUDECODE": "1", "CLAUDE_CODE_PROMPT_CACHE_TTL": "1h", "AGENT_TOOLKIT_DELEGATED_SESSION": "1"}, 240.0),
    ],
    ids=["main", "delegated"],
)
def test_release_deadline_keeps_delegated_cap(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str], expected_timeout: float
) -> None:
    """資源解放の待機は、CLIの結果待機と別の関数から委譲先の短縮を含む期限を得る。

    CLIの結果待機の関数を使うと、CLIの上限の改訂で資源解放の期限が変わる。
    """
    for name in ("CLAUDECODE", "AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION", "FORCE_PROMPT_CACHING_5M"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert lane.get_wait_timeout is wait_schedule.get_wait_timeout
    assert lane.get_wait_timeout("main") == expected_timeout


_SHARE = pathlib.Path(lane.__file__).resolve().parents[2] / "share"


def _caller(monkeypatch: pytest.MonkeyPatch, *, claude: str | None = None, codex: str | None = None) -> None:
    # 実行ホストのsessionで結果が変わらないよう、呼出元の識別子を明示する。
    for name, value in (("CLAUDE_CODE_SESSION_ID", claude), ("CODEX_THREAD_ID", codex)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def _running(
    session_id: str,
    cwd: pathlib.Path,
    *,
    role: str | None = None,
    prompt: str = "自由本文の依頼",
    kind: Literal["delegate", "explore", "shell", "write"] = "delegate",
    launcher: str | None = None,
    terminal: bool = False,
) -> None:
    """agents_serverが`start`で公開するのと同じ形の担当記録を登録簿へ置く。"""
    if role is not None:
        prompt = task_documents.task_prompt_header(_SHARE / f"{role}.subagent.md") + "\n本文"
    session_registry.publish(
        session_id,
        terminal=terminal,
        cwd=str(cwd),
        launch_kind=kind,
        launch_info=session_registry.LaunchInfo(label=f"{session_id}-label", prompt=prompt),
        launcher_session_id=launcher,
    )


def _finalization_manifest(tmp_path: pathlib.Path, **extra: Any) -> pathlib.Path:
    handoff = tmp_path / "handoff.md"
    handoff.write_text("検収済み採否", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps({"plans": [], "wis": [{"name": "awi.md", "commands": []}], "handoff": str(handoff), **extra}),
        encoding="utf-8",
    )
    return manifest


@pytest.mark.parametrize("case", ["non-writer-role", "explore", "caller-self", "launcher", "two-level", "codex-caller"])
def test_integrate_does_not_count_non_writers_or_caller_chain(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """書き込まない担当と呼出元の委譲元の連鎖は、--writer-sessionなしでも統合を止めない。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    _caller(monkeypatch)
    if case == "non-writer-role":
        _running("adder", repo, role="add-wi")
    elif case == "explore":
        _running("explorer", repo, kind="explore")
    elif case == "caller-self":
        _caller(monkeypatch, claude="lane-caller")
        _running("lane-caller", worktree)
    elif case == "launcher":
        _caller(monkeypatch, claude="lane-caller")
        _running("lane-caller", worktree, role="exec", launcher="main")
        _running("main", repo)
    elif case == "two-level":
        _caller(monkeypatch, claude="caller")
        _running("caller", repo, role="lane-integration", launcher="middle")
        _running("middle", worktree, role="exec", launcher="top")
        _running("top", repo)
    else:
        _caller(monkeypatch, codex="codex-caller")
        _running("codex-caller", worktree, role="exec", launcher="main")
        _running("main", repo)

    code, result, stderr = _invoke(
        capsys, *_integration_args(record, repo, _finalization_manifest(tmp_path), record["initial_head"])
    )

    assert code == 0 and result["phase"] == "finalized", stderr
    # 回収側は書込宣言と呼出元の除外を適用せず、レーンworktreeの使用者がいれば資源を保持する。
    code, deleted, stderr = _invoke(capsys, "delete")
    assert ("lane-01" in deleted.get("removed", [])) is (case in {"non-writer-role", "explore"}), stderr


@pytest.mark.parametrize("no_merge", [False, True])
@pytest.mark.parametrize("spread", [False, True])
def test_integrate_reports_all_unowned_writers_then_resumes_with_agreement(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], spread: bool, no_merge: bool
) -> None:
    """書き込む担当を両worktreeから全件診断し、合意した担当を指定した再実行で統合へ進む。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    nested = repo / ".claude/worktrees/other"
    git_repository.run_git(repo, "worktree", "add", "-b", "other", str(nested))
    _caller(monkeypatch, claude="caller")
    _running("caller", worktree, role="exec", launcher="main")
    _running("main", repo)
    counted = {
        "exec-writer": repo,
        "free-writer": worktree if spread else repo,
        "sibling": repo,
        # 役割文書へ言及するだけの自由本文は、その役割の起動として扱わない。
        "mention": worktree if spread else repo,
    }
    _running("exec-writer", counted["exec-writer"], role="exec")
    _running("free-writer", counted["free-writer"])
    _running("sibling", counted["sibling"], role="exec", launcher="main")
    _running("mention", counted["mention"], prompt=f"次の文書を読んで比べよ: {_SHARE / 'add-wi.subagent.md'}")
    _running("agreed", repo, role="exec")
    _running("finished", repo, role="exec", terminal=True)
    _running("elsewhere", nested, role="exec")
    args = (
        *_integration_args(record, repo, _finalization_manifest(tmp_path), record["initial_head"]),
        "--writer-session",
        "agreed",
    )
    if no_merge:
        args = (*args, "--no-merge")
    target_head = git_repository.git_output(repo, "rev-parse", "HEAD")

    code, _result, stderr = _invoke(capsys, *args)

    assert code == 2
    assert f"現在の書込所有に含まれない担当が稼働中: {len(counted)}件" in stderr
    for session_id, cwd in counted.items():
        tree = worktree if cwd == worktree else repo.resolve()
        assert f"session_id={session_id}, label={session_id}-label" in stderr
        assert f"cwd={cwd}, 所属worktree={tree}" in stderr
    assert f"役割文書={_SHARE / 'exec.subagent.md'}" in stderr
    assert "--writer-sessionへ反復指定" in stderr
    for excluded in ("caller", "main", "agreed", "finished", "elsewhere"):
        assert f"session_id={excluded}," not in stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == target_head
    assert "統合済み" not in selected.read_text(encoding="utf-8")
    assert "integration" not in json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))

    agreed = [value for session_id in counted for value in ("--writer-session", session_id)]
    code, result, stderr = _invoke(capsys, *args, *agreed)

    assert code == 0 and result["phase"] == "finalized", stderr


def test_integrate_rejects_unresolvable_registration(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """解決できない担当登録は許可扱いにせず統合を拒否する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    _caller(monkeypatch)
    directory = session_registry.registry_directory()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "broken.json").write_text("{", encoding="utf-8")

    code, _result, stderr = _invoke(
        capsys, *_integration_args(record, repo, _finalization_manifest(tmp_path), record["initial_head"])
    )

    assert code == 2 and "担当登録の形式を確認できない" in stderr


def _write_args(output: pathlib.Path) -> list[str]:
    return [
        sys.executable,
        "-c",
        "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(' '.join(sys.argv[2:]),encoding='utf-8')",
        str(output),
        "{{integrated_head_short}}",
        "{{completed_at}}",
        "{{lane_seconds}}",
    ]


@pytest.mark.parametrize("no_merge", [False, True])
def test_integrate_passes_confirmed_values_to_every_consumer(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_merge: bool
) -> None:
    """計画2件の保存とAWI本文の追記へ、同じ統合後HEAD・完了時刻・稼働秒数を渡す。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    _caller(monkeypatch)
    if not no_merge:
        (worktree / "feature.txt").write_text("成果", encoding="utf-8")
        git_repository.commit_all(worktree, "成果")
    head = git_repository.git_output(worktree, "rev-parse", "HEAD")
    template = tmp_path / "record-template.md"
    template.write_text("予測補正記録: {{integrated_head_short}} {{completed_at}} {{lane_seconds}} {{other}}", encoding="utf-8")
    outputs = [tmp_path / name for name in ("plan-a", "plan-b", "awi-body")]
    copy = [sys.executable, "-c", "import shutil,sys;shutil.copy(sys.argv[1],sys.argv[2])"]
    started = datetime.datetime.now().astimezone() - datetime.timedelta(seconds=100)
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [
                    {"name": "a.md", "commands": [_write_args(outputs[0])]},
                    {"name": "b.md", "commands": [_write_args(outputs[1])]},
                ],
                "wis": [{"name": "awi.md", "commands": [[*copy, "{{expand-file:" + str(template) + "}}", str(outputs[2])]]}],
                "lane_started_at": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    args = _integration_args(record, repo, manifest, head)
    if no_merge:
        args = (*args, "--no-merge")

    code, result, stderr = _invoke(capsys, *args)

    assert code == 0 and result["phase"] == "finalized", stderr
    values = result["finalization_values"]
    short = values["integrated_head_short"]
    assert git_repository.git_output(repo, "rev-parse", short) == git_repository.git_output(repo, "rev-parse", "HEAD")
    completed = datetime.datetime.fromisoformat(values["completed_at"])
    assert completed.tzinfo is not None
    # 完了時刻は秒精度で表示するため、開始時刻からの経過との差は1秒未満の切り捨て分に限る。
    assert abs(int(values["lane_seconds"]) - (completed - started).total_seconds()) <= 1
    assert int(values["lane_seconds"]) >= 100
    expected = f"{short} {values['completed_at']} {values['lane_seconds']}"
    assert outputs[0].read_text(encoding="utf-8") == outputs[1].read_text(encoding="utf-8") == expected
    assert outputs[2].read_text(encoding="utf-8") == f"予測補正記録: {expected} {{{{other}}}}"


def test_integrate_reuses_confirmed_values_after_failed_finalization(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """途中失敗の再開では確定値を再計算せず、成功済み操作を繰り返さず、開始時刻の変更を拒否する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    _caller(monkeypatch)
    git_repository.commit_all(worktree, "成果")
    head = git_repository.git_output(worktree, "rev-parse", "HEAD")
    first, second, calls, permit = (tmp_path / name for name in ("plan-a", "plan-b", "calls", "permit"))
    count = [
        sys.executable,
        "-c",
        "import pathlib,sys;p=pathlib.Path(sys.argv[1]);p.write_text(p.read_text()+'s' if p.exists() else 's')",
        str(calls),
    ]
    gated = [
        sys.executable,
        "-c",
        "import pathlib,sys;p=pathlib.Path;a=sys.argv;sys.exit(4) if not p(a[1]).exists() else p(a[2]).write_text(a[3])",
        str(permit),
        str(second),
        "{{completed_at}} {{lane_seconds}}",
    ]
    started = (datetime.datetime.now().astimezone() - datetime.timedelta(seconds=30)).isoformat()

    def manifest(lane_started_at: str) -> pathlib.Path:
        path = tmp_path / "integration.json"
        path.write_text(
            json.dumps(
                {
                    "plans": [{"name": "a.md", "commands": [_write_args(first), count]}, {"name": "b.md", "commands": [gated]}],
                    "wis": [{"name": "awi.md", "commands": []}],
                    "lane_started_at": lane_started_at,
                }
            ),
            encoding="utf-8",
        )
        return path

    args = _integration_args(record, repo, manifest(started), head)
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2 and "終了4" in stderr
    stored = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
    assert stored["integration"]["phase"] == "git-integrated"
    assert "統合済み" not in selected.read_text(encoding="utf-8")
    values = stored["integration"]["finalization_values"]

    other = (datetime.datetime.fromisoformat(started) - datetime.timedelta(hours=1)).isoformat()
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest(other), head))
    assert code == 2 and "最初のレーン開始時刻と異なる" in stderr

    permit.touch()
    code, result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest(started), head))
    assert code == 0 and result["phase"] == "finalized", stderr
    assert result["finalization_values"] == values
    assert calls.read_text(encoding="utf-8") == "s"
    assert second.read_text(encoding="utf-8") == f"{values['completed_at']} {values['lane_seconds']}"
    assert first.read_text(encoding="utf-8").endswith(f"{values['completed_at']} {values['lane_seconds']}")


@pytest.mark.parametrize(
    ("extra", "command", "reason"),
    [
        (
            {"lane_started_at": "2026-10-19T00:00:00+09:00"},
            "{{unknown}}",
            "最終化argvの参照が不明: {{unknown}}。受理する参照は",
        ),
        (
            {"lane_started_at": "2999-01-01T00:00:00+09:00"},
            "{{lane_seconds}}",
            "lane_started_atが現在時刻より後にある: 受け取った値=2999-01-01T00:00:00+09:00",
        ),
        ({"lane_started_at": "2026-10-19T00:00:00"}, "{{lane_seconds}}", "時差付きISO 8601"),
        ({}, "{{lane_seconds}}", "lane_started_atを指定する"),
        ({"lane_started_at": "2026-10-19T00:00:00+09:00"}, "{{expand-file:relative.md}}", "実在する絶対パス"),
    ],
)
def test_integrate_rejects_invalid_finalization_references(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: dict[str, str],
    command: str,
    reason: str,
) -> None:
    """未知の参照、時差の無い開始時刻、未来の開始時刻、開始時刻の欠落、相対パスのテンプレートはGit統合前に拒否する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    _caller(monkeypatch)
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {"plans": [{"name": "a.md", "commands": [["true", command]]}], "wis": [{"name": "awi.md", "commands": []}], **extra}
        ),
        encoding="utf-8",
    )
    target_head = git_repository.git_output(repo, "rev-parse", "HEAD")

    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, record["initial_head"]))

    assert code == 2 and reason in stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == target_head
    if "現在時刻より後" in reason:
        assert "現在時刻=" in stderr and "起動時刻を時差付きISO 8601" in stderr
        assert "integration" not in json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
