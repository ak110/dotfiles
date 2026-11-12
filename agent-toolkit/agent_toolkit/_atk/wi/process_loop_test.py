"""統括の対象別所有、再開と結果の検収を検証する。"""

import argparse
import contextlib
import json
import os
import pathlib
from typing import Any

import psutil
import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import listing
from agent_toolkit._atk.wi import process_loop as loop
from agent_toolkit._atk.wi import process_loop_control as control
from agent_toolkit._atk.wi import process_loop_env as env
from agent_toolkit._atk.wi import process_loop_launch_gate as gate
from agent_toolkit._atk.wi import process_loop_log as log
from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._common import process_tree, session_registry
from agent_toolkit._testing import late_descendant
from agent_toolkit._testing.process_loop_coordinator_support import _manifest, _targets


@pytest.mark.parametrize("counts", [(1, 1), (0, 9, 10, 11, 12)])
def test_one_coordinator_receives_every_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, counts: tuple[int, ...]
) -> None:
    """1回の統括に全候補を渡し、Nとworktree・所有・候補本文を対象別に保持する。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, counts)
    observed: list[tuple[pathlib.Path, dict[str, Any]]] = []

    def execute(_args: argparse.Namespace, cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt, child_env).read_text(encoding="utf-8"))
        observed.append((cwd, data))
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "coordinator"
        kwargs["on_session_started"]("coordinator")
        records = {}
        for target in data["repositories"]:
            candidates = json.loads(pathlib.Path(target["candidate_file"]).read_text(encoding="utf-8"))
            assert len(candidates) == counts[int(target["target_repo"].rsplit("-", 1)[1])]
            assert pathlib.Path(target["worktree"]).is_dir()
            stored = json.loads(pathlib.Path(target["ownership_record"]).read_text(encoding="utf-8"))
            assert stored["session_id"] == "coordinator" and stored["token"] == target["ownership_token"]
            records[target["target_repo"]] = {"status": "completed", "writer_sessions": []}
        for path in notes.glob("2026/10/*.md"):
            path.unlink()
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": records}), encoding="utf-8")
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    results = loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)
    assert len(observed) == 1 and observed[0][0] == pathlib.Path.cwd()
    assert len(results) == len(counts)
    assert [target["lane_limit"] for target in observed[0][1]["repositories"]] == [(count + 9) // 10 for count in counts]
    assert all(result["status"] == "completed" for result in results.values())
    assert all(json.loads(path.read_text())["released"] for path in ownership.ownership_directory().glob("*.json"))


def test_next_round_starts_new_coordinator_for_unfinished_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """部分完了後の残件は次の巡回の新統括へ渡し、元の準備と再開情報を保つ。"""
    notes, snapshots = _targets(tmp_path, monkeypatch)
    rounds: list[tuple[str | None, list[str]]] = []

    def execute(args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt, child_env).read_text())
        targets = data["repositories"]
        rounds.append((args.resume, [target["target_repo"] for target in targets]))
        identifier = f"coordinator-{len(rounds)}"
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = identifier
        kwargs["on_session_started"](identifier)
        if len(rounds) == 2:
            assert targets[0]["previous"]["session_id"] == "coordinator-1"
            assert targets[0]["previous"]["prepared_worktree"] == targets[0]["worktree"]
        selected = targets[0]
        for path in notes.glob(f"2026/10/{selected['target_repo'].rsplit('/', 1)[1]}-*.md"):
            path.unlink()
        records = {target["target_repo"]: {"status": "completed" if target is selected else "incomplete"} for target in targets}
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": records}))
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)
    assert len(rounds) == 1
    loop.run_coordinator(
        argparse.Namespace(resume=None, auto_resume=False, worktree=None),
        tuple(item for item in listing.configured_repo_snapshot(notes) if item.has_work),
    )
    assert rounds == [
        (None, [item.repository.repo_id for item in snapshots]),
        (None, [snapshots[1].repository.repo_id]),
    ]


def test_explicitly_resumed_round_ignores_previous_round_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """明示再開の結果に前巡回の対象が残っても、今回の対象を結果どおり検収する。

    2026年11月1日の異常終了（結果に前ラウンドの対象が残り、常駐全体が拒否で終了した）の再現である。
    """
    notes, snapshots = _targets(tmp_path, monkeypatch)
    # 常駐のログは常駐セッションでだけ記録されるため、常駐から起動した状態にする。
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    finished: dict[str, dict[str, Any]] = {}

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt, child_env).read_text())
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "same-coordinator"
        kwargs["on_session_started"]("same-coordinator")
        selected = data["repositories"][0]["target_repo"]
        for path in notes.glob(f"2026/10/{selected.rsplit('/', 1)[1]}-*.md"):
            path.unlink()
        finished[selected] = {"status": "completed", "writer_sessions": []}
        # 同じ会話の統括は、前ラウンドで完了した対象も結果へ書く。
        records = {**finished}
        for target in data["repositories"][1:]:
            records[target["target_repo"]] = {"status": "incomplete", "writer_sessions": []}
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": records}))
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    results = loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)
    results.update(
        loop.run_coordinator(
            argparse.Namespace(resume="same-coordinator", auto_resume=False, worktree=None),
            tuple(item for item in listing.configured_repo_snapshot(notes) if item.has_work),
        )
    )
    assert {repo_id: result["status"] for repo_id, result in results.items()} == {
        snapshot.repository.repo_id: "completed" for snapshot in snapshots
    }
    output = capsys.readouterr().out
    assert "統括結果に今回の対象外のリポジトリがある" in output and snapshots[0].repository.repo_id in output
    assert any("event=result_foreign_repositories" in line for line in log.log_path().read_text(encoding="utf-8").splitlines())


@pytest.mark.parametrize("status", ["incomplete", "completed", "blocked", "pending", None])
def test_no_progress_and_blocking_are_repository_scoped(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, status: str | None
) -> None:
    _notes, snapshots = _targets(tmp_path, monkeypatch)
    calls = 0

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        nonlocal calls
        calls += 1
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "same-coordinator"
        kwargs["on_session_started"]("same-coordinator")
        data = json.loads(_manifest(prompt, child_env).read_text())
        records = {target["target_repo"]: {"status": status} for target in data["repositories"]} if status else {}
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": records}))
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)
    assert calls == 1
    for snapshot in snapshots:
        assert gate.decide(snapshot.repository.repo_id, gate.repository_input(snapshot, None)).skipped is (status == "blocked")


def test_owner_wait_keeps_other_repository_running(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _notes, snapshots = _targets(tmp_path, monkeypatch)
    first = snapshots[0].repository
    held = ownership.try_acquire(first.repo_id, worktree=first.worktree)
    assert held is not None
    targets: list[str] = []

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **_kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt, child_env).read_text())
        targets.extend(target["target_repo"] for target in data["repositories"])
        return False

    monkeypatch.setattr(session, "run_process_session", execute)
    results = loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)
    assert targets == [snapshots[1].repository.repo_id]
    assert results[first.repo_id]["status"] == "owner_wait"
    ownership.release(held)


def test_running_descendant_keeps_ownership(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _notes, snapshots = _targets(tmp_path, monkeypatch, (1,))

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, _prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "coordinator"
        kwargs["on_session_started"]("coordinator")
        identity = session_registry.ProcessIdentity.of(os.getpid())
        assert identity is not None
        session_registry.publish(
            "worker",
            state=session_registry.PublicationState.PENDING_RUNNING,
            cwd=str(snapshots[0].repository.worktree),
            launcher_session_id="coordinator",
            processes=(identity,),
        )
        return False

    monkeypatch.setattr(session, "run_process_session", execute)
    results = loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)
    assert results[snapshots[0].repository.repo_id]["status"] == "owner_wait"
    assert ownership.try_acquire(snapshots[0].repository.repo_id, worktree=snapshots[0].repository.worktree) is None


@pytest.mark.parametrize("argv", [["all-repos"], ["--target-repo", "somewhere"], ["--resume"]])
def test_removed_public_forms_are_rejected(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        atk.main(["wi", "process-loop", *argv])
    assert raised.value.code == 2


def test_explicit_resume_without_coordinator_registration(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """所有台帳に無い会話IDも公開CLIから再開へ渡し、対象の処理結果を検収する。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._watch, "wait_for_start_permission", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    observed = []

    def execute(args, _cwd, prompt, child_env, **kwargs):
        observed.append(args.resume)
        assert kwargs["resume_pending"]
        data = json.loads(_manifest(prompt, child_env).read_text(encoding="utf-8"))
        kwargs["on_session_started"](args.resume)
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = args.resume
        (notes / "2026/10/repo-0-0.md").unlink()
        pathlib.Path(data["result_file"]).write_text(
            json.dumps({"repositories": {snapshots[0].repository.repo_id: {"status": "completed"}}}), encoding="utf-8"
        )
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    with pytest.raises(SystemExit) as exited:
        atk.main(["wi", "process-loop", "--once", "--no-alerts", "--no-update", "--resume", "ordinary-session"], home=tmp_path)
    assert exited.value.code == 0
    assert observed == ["ordinary-session"]


def test_explicit_resume_uses_frozen_candidates(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """中断後の新WIは次のprocess-wiの実行へ残し、明示再開の候補原本は前回のまま使う。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    first_candidate: str | None = None

    def interrupted(
        _args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any
    ) -> bool:
        nonlocal first_candidate
        data = json.loads(_manifest(prompt, child_env).read_text())
        first_candidate = data["repositories"][0]["candidate_file"]
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "frozen-coordinator"
        kwargs["on_session_started"]("frozen-coordinator")
        return False

    args = argparse.Namespace(resume=None, auto_resume=False, worktree=None)
    monkeypatch.setattr(session, "run_process_session", interrupted)
    loop.run_coordinator(args, snapshots)
    new_item = notes / "2026/10/new.md"
    new_item.write_text(
        "---\nstate: inbox\ntype: awi\ntarget_repo: example.test/team/repo-0\n---\n\n# 新WI\n", encoding="utf-8"
    )

    def resumed(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt, child_env).read_text())
        target = data["repositories"][0]
        assert target["candidate_file"] == first_candidate
        assert len(json.loads(pathlib.Path(target["candidate_file"]).read_text())) == 1
        (notes / "2026/10/repo-0-0.md").unlink()
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "frozen-coordinator"
        kwargs["on_session_started"]("frozen-coordinator")
        pathlib.Path(data["result_file"]).write_text(
            json.dumps({"repositories": {target["target_repo"]: {"status": "completed"}}})
        )
        return True

    monkeypatch.setattr(session, "run_process_session", resumed)
    args.resume = "frozen-coordinator"
    result = loop.run_coordinator(args, listing.configured_repo_snapshot(notes))
    assert result[snapshots[0].repository.repo_id]["status"] == "completed"
    assert new_item.exists()


def test_declared_writer_holds_only_its_repository(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """対象別の申告で生存writerを区別し、独立して終了した対象は回収へ進む。"""
    notes, snapshots = _targets(tmp_path, monkeypatch)

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt, child_env).read_text())
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "coordinator"
        kwargs["on_session_started"]("coordinator")
        identity = session_registry.ProcessIdentity.of(os.getpid())
        assert identity is not None
        session_registry.publish(
            "writer-0",
            state=session_registry.PublicationState.PENDING_RUNNING,
            processes=(identity,),
            launcher_session_id="coordinator",
        )
        session_registry.publish(
            "writer-1", state=session_registry.PublicationState.RESULT_COMPLETED, launcher_session_id="coordinator"
        )
        for path in notes.glob("2026/10/*.md"):
            path.unlink()
        records = {
            target["target_repo"]: {"status": "completed", "writer_sessions": [f"writer-{index}"]}
            for index, target in enumerate(data["repositories"])
        }
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": records}))
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    result = loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)
    assert result[snapshots[0].repository.repo_id]["status"] == "owner_wait"
    assert result[snapshots[1].repository.repo_id]["status"] == "completed"
    for record in result.values():
        assert json.loads(pathlib.Path(record["result_path"]).read_text())["status"] == record["status"]


def test_resume_recovers_saved_completion_before_launch(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """終了処理前に中断しても、申告と実体の一致した完了済み対象を継続へ再配送しない。"""
    notes, snapshots = _targets(tmp_path, monkeypatch)
    first, second = [item.repository.repo_id for item in snapshots]

    def interrupted(
        _args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any
    ) -> bool:
        data = json.loads(_manifest(prompt, child_env).read_text())
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "interrupted-root"
        kwargs["on_session_started"]("interrupted-root")
        (notes / "2026/10/repo-0-0.md").unlink()
        pathlib.Path(data["result_file"]).write_text(
            json.dumps({"repositories": {first: {"status": "completed"}, second: {"status": "incomplete"}}})
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(session, "run_process_session", interrupted)
    with pytest.raises(KeyboardInterrupt):
        loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)

    def resumed(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt, child_env).read_text())
        assert [target["target_repo"] for target in data["repositories"]] == [second]
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "interrupted-root"
        kwargs["on_session_started"]("interrupted-root")
        (notes / "2026/10/repo-1-0.md").unlink()
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": {second: {"status": "completed"}}}))
        return True

    monkeypatch.setattr(session, "run_process_session", resumed)
    result = loop.run_coordinator(
        argparse.Namespace(resume="interrupted-root", auto_resume=False, worktree=None), listing.configured_repo_snapshot(notes)
    )
    assert result[first]["recovered"] is True
    assert all(item["status"] == "completed" for item in result.values())
    assert json.loads(pathlib.Path(result[first]["result_path"]).read_text())["status"] == "completed"


def test_control_storage_is_independent_of_repository() -> None:
    control.cmd_process_loop_instruct("今回だけの追加指示")
    assert log.read_instructions() == ["今回だけの追加指示"]
    control.cmd_process_loop_abort()
    assert control.consume_process_loop_abort()
    assert not control.consume_process_loop_abort()
    control.cmd_process_loop_instruct_cancel()
    assert not log.read_instructions()


@pytest.mark.parametrize(
    "record",
    [
        [],
        {"repositories": []},
        {"repositories": {"foreign": {"status": "completed"}, "repo": {"status": "done"}}},
        {"repositories": {"repo": {"status": []}}},
        {"repositories": {"repo": {"status": "incomplete", "writer_sessions": "writer"}}},
        {"repositories": {"repo": {"status": "incomplete", "writer_sessions": [1]}}},
    ],
)
def test_invalid_result_is_rejected(tmp_path: pathlib.Path, record: Any) -> None:
    """外部の返却の型・対象・状態・writer配列が不正なら、完了と扱う前に診断する。"""
    path = tmp_path / "result.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    owner = ownership.Ownership("repo", "token", tmp_path / "owner.json", None)
    with pytest.raises(ValueError):
        loop._read_results(path, {"repo": owner})  # pylint: disable=protected-access


def test_unreclaimed_descendant_holds_ownership_until_it_disappears(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, capsys: pytest.CaptureFixture[str]
) -> None:
    """統括の停止で回収できなかった子孫が生存する間は、所有記録が識別を保持して次の統括を起動しない。"""
    _notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    repository = snapshots[0].repository
    owned = contextlib.ExitStack()
    request.addfinalizer(owned.close)
    survivor = owned.enter_context(late_descendant.held_process(tmp_path))

    def reap() -> None:
        survivor.kill()
        survivor.wait()

    request.addfinalizer(reap)
    launches: list[int] = []

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, _prompt: str, _env: dict[str, str], **kwargs: Any) -> bool:
        launches.append(len(launches))
        if len(launches) == 1:
            # 統括CLIの停止結果に、回収できなかった子孫が残った状態を注入する。
            kwargs["on_residual"](
                process_tree.ReclaimError([f"{survivor.pid}: sleep"], [psutil.Process(survivor.pid)], context="test")
            )
        return False

    monkeypatch.setattr(session, "run_process_session", execute)
    # 子孫が回収を拒む状態を、再回収の操作を無効にして表す。
    monkeypatch.setattr(process_tree, "reclaim_processes", lambda _targets: [])
    arguments = argparse.Namespace(resume=None, auto_resume=False, worktree=None)
    results = loop.run_coordinator(arguments, snapshots)
    assert results[repository.repo_id]["status"] == "owner_wait"
    saved = json.loads(next(ownership.ownership_directory().glob("*.json")).read_text(encoding="utf-8"))
    assert [(entry["pid"], entry["create_time"]) for entry in saved["residual_processes"]] == [
        (survivor.pid, psutil.Process(survivor.pid).create_time())
    ]
    # 自己再起動後の巡回と同じく、保存した記録だけを読み直して判定する。
    assert ownership.try_acquire(repository.repo_id, worktree=repository.worktree) is None
    results = loop.run_coordinator(arguments, snapshots)
    assert results[repository.repo_id]["status"] == "owner_wait"
    assert launches == [0]
    assert str(survivor.pid) in capsys.readouterr().err
    reap()
    results = loop.run_coordinator(arguments, snapshots)
    assert launches == [0, 1]
    assert results[repository.repo_id]["status"] != "owner_wait"
    saved = json.loads(next(ownership.ownership_directory().glob("*.json")).read_text(encoding="utf-8"))
    assert not saved.get("residual_processes")


@pytest.mark.parametrize("failure", ["json", "writers", "directory", "residual"])
def test_unreadable_ownership_record_skips_only_that_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    """解釈できない処理所有記録は、その対象リポジトリだけを回復手段つきで見送り、他の対象の処理を続ける。"""
    _notes, snapshots = _targets(tmp_path, monkeypatch)
    first = snapshots[0].repository
    record = ownership._path(first.repo_id)  # pylint: disable=protected-access
    record.parent.mkdir(parents=True, exist_ok=True)
    if failure == "directory":
        record.mkdir()
    elif failure == "json":
        record.write_text("{", encoding="utf-8")
    else:
        stored = {
            "token": "broken",
            "completed": False,
            "released": False,
            "held": False,
            "pid": 999999999,
            "create_time": 0,
        }
        stored.update({"writer_sessions": 1} if failure == "writers" else {"residual_processes": [{"pid": 1}]})
        record.write_text(json.dumps(stored), encoding="utf-8")
    original = record.read_bytes() if record.is_file() else None
    targets: list[str] = []

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **_kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt, child_env).read_text())
        targets.extend(target["target_repo"] for target in data["repositories"])
        return False

    monkeypatch.setattr(session, "run_process_session", execute)
    results = loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)

    assert targets == [snapshots[1].repository.repo_id]
    assert results[first.repo_id]["status"] == "prepare_failed"
    stderr = capsys.readouterr().err
    assert str(record) in stderr and "atk agents show" in stderr and "退避または削除" in stderr
    assert (record.read_bytes() if record.is_file() else None) == original
    assert record.exists()


def test_unreadable_descendant_registration_holds_recovery_with_guidance(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """読めない子孫の担当登録がある回収は保留し、登録ファイルの絶対パスと回復手段を示す。"""
    _notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    repository = snapshots[0].repository
    owner = ownership.try_acquire(repository.repo_id, worktree=repository.worktree, mode="coordinator")
    assert owner is not None
    directory = session_registry.registry_directory()
    directory.mkdir(parents=True, exist_ok=True)
    broken = directory / "broken-child.json"
    broken.write_text("{", encoding="utf-8")

    assert not loop._writers_stopped(owner, writers=["broken-child"])  # pylint: disable=protected-access

    stdout = capsys.readouterr().out
    assert str(broken.resolve()) in stdout and "atk agents show broken-child" in stdout and "退避または削除" in stdout
    ownership.release(owner, completed=False)
