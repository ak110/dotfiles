"""隔離したGit・キューで、登録全体の統括と対象別の所有・継続を検証する。"""

import argparse
import json
import os
import pathlib
import subprocess
import threading
import time
from typing import Any

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
from agent_toolkit._common import session_registry
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing import wi_entry_files


def _targets(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, counts: tuple[int, ...] = (1, 1)
) -> tuple[pathlib.Path, tuple[listing.RepositorySnapshot, ...]]:
    notes = tmp_path / "notes"
    directory = notes / "2026/10"
    directory.mkdir(parents=True)
    paths = []
    for index, count in enumerate(counts):
        repo_id = f"example.test/team/repo-{index}"
        paths.append(git.init_repository(tmp_path / f"repo-{index}", origin=f"https://{repo_id}.git"))
        for number in range(count):
            (directory / f"repo-{index}-{number}.md").write_text(
                f"---\nstate: inbox\ntype: awi\ntarget_repo: {repo_id}\n---\n\n# 作業\n",
                encoding="utf-8",
            )
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(path) for path in paths]))
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setattr(session, "select_available_orchestrator", lambda *_args, **_kwargs: ("claude", "model", "medium", None))
    monkeypatch.setattr(session, "resolve_orchestrator_specs", lambda: [("claude", "model", "medium")])
    return notes, listing.configured_repo_snapshot(notes)


def _manifest(prompt: str) -> pathlib.Path:
    start = prompt.index("開始時入力のmanifest ") + len("開始時入力のmanifest ")
    return pathlib.Path(prompt[start : prompt.index(" で取得済み候補", start)])


@pytest.mark.parametrize("goal_prefix", [True, False])
def test_manifest_is_read_from_current_goal(tmp_path: pathlib.Path, goal_prefix: bool) -> None:
    """共有する模擬統括も現在の公開promptから空白を含むmanifestパスを取得する。"""
    manifest = tmp_path / "round with spaces.json"
    prompt = session.build_process_loop_prompt(manifest)
    if not goal_prefix:
        prompt = prompt.removeprefix("/goal ")
    assert _manifest(prompt) == manifest


@pytest.mark.parametrize("counts", [(1, 1), (0, 9, 10, 11, 12)])
def test_one_coordinator_receives_every_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, counts: tuple[int, ...]
) -> None:
    """1回の統括に全候補を渡し、Nとworktree・所有・候補本文を対象別に保持する。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, counts)
    observed: list[tuple[pathlib.Path, dict[str, Any]]] = []

    def execute(_args: argparse.Namespace, cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt).read_text(encoding="utf-8"))
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


def test_continue_only_unfinished_repository(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """部分完了後は同一統括会話へ未完了対象だけを渡す。"""
    notes, snapshots = _targets(tmp_path, monkeypatch)
    rounds: list[tuple[str | None, list[str]]] = []

    def execute(args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt).read_text())
        targets = data["repositories"]
        rounds.append((args.resume, [target["target_repo"] for target in targets]))
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "same-coordinator"
        kwargs["on_session_started"]("same-coordinator")
        selected = targets[0]
        for path in notes.glob(f"2026/10/{selected['target_repo'].rsplit('/', 1)[1]}-*.md"):
            path.unlink()
        records = {target["target_repo"]: {"status": "completed" if target is selected else "incomplete"} for target in targets}
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": records}))
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)
    assert rounds == [
        (None, [item.repository.repo_id for item in snapshots]),
        ("same-coordinator", [snapshots[1].repository.repo_id]),
    ]


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
        data = json.loads(_manifest(prompt).read_text())
        records = {target["target_repo"]: {"status": status} for target in data["repositories"]} if status else {}
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": records}))
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)
    assert calls == (1 if status == "blocked" else 3)
    for snapshot in snapshots:
        assert gate.decide(snapshot.repository.repo_id, gate.repository_input(snapshot, None)).skipped


def test_owner_wait_keeps_other_repository_running(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _notes, snapshots = _targets(tmp_path, monkeypatch)
    first = snapshots[0].repository
    held = ownership.try_acquire(first.repo_id, worktree=first.worktree)
    assert held is not None
    targets: list[str] = []

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, _env: dict[str, str], **_kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt).read_text())
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
            terminal=False,
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


def test_explicit_resume_uses_frozen_candidates(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """中断後の新WIは次のprocess-wiの実行へ残し、明示再開の候補原本は前回のまま使う。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    first_candidate: str | None = None

    def interrupted(
        _args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any
    ) -> bool:
        nonlocal first_candidate
        data = json.loads(_manifest(prompt).read_text())
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
        data = json.loads(_manifest(prompt).read_text())
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
        data = json.loads(_manifest(prompt).read_text())
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "coordinator"
        kwargs["on_session_started"]("coordinator")
        identity = session_registry.ProcessIdentity.of(os.getpid())
        assert identity is not None
        session_registry.publish("writer-0", terminal=False, processes=(identity,), launcher_session_id="coordinator")
        session_registry.publish("writer-1", terminal=True, launcher_session_id="coordinator")
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
        data = json.loads(_manifest(prompt).read_text())
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
        data = json.loads(_manifest(prompt).read_text())
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


def test_public_loop_checks_eight_repositories_with_bounded_concurrency(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """8対象を最大3並行で確認し、結果は登録順で表示して候補を一度だけ再取得する。"""
    _notes, snapshots = _targets(tmp_path, monkeypatch, (0,) * 8)
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    lock = threading.Lock()
    group = threading.Barrier(3)
    active = maximum = 0
    seen = []
    durations = []
    timings = []
    parallel = False

    def check(_args, _notes, repo_id, _worktree, _last, **kwargs):
        nonlocal active, maximum
        assert kwargs["count_dependabot"]
        with lock:
            active += 1
            maximum = max(maximum, active)
            seen.append(repo_id)
            ordinal = len(seen)
        started = time.monotonic()
        timings.append(started)
        if parallel and ordinal <= 6:
            group.wait(timeout=5)
        # 同じAPI待機を逐次実行した場合の総待機時間と比較する。
        time.sleep(0.05)
        with lock:
            active -= 1
            durations.append(time.monotonic() - started)
            timings.append(time.monotonic())
        return 1.0, 1, frozenset()

    monkeypatch.setattr(loop._alerts, "check_process_loop_alerts", check)  # pylint: disable=protected-access
    original = listing.configured_repo_snapshot
    reads = []

    def snapshot(*args, **kwargs):
        reads.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(listing, "configured_repo_snapshot", snapshot)
    pool = loop.concurrent.futures.ThreadPoolExecutor
    elapsed = []
    for parallel in (False, True):
        seen.clear()
        timings.clear()
        durations.clear()
        reads.clear()
        maximum = 0
        with monkeypatch.context() as scoped:
            scoped.setattr(
                loop.concurrent.futures,
                "ThreadPoolExecutor",
                lambda workers=3 if parallel else 1, **_kwargs: pool(max_workers=workers),
            )
            with pytest.raises(SystemExit) as exited:
                atk.main(["wi", "process-loop", "--no-update", "--once"], home=tmp_path)
        elapsed.append(max(timings) - min(timings))
        assert exited.value.code == 0
        assert maximum == (3 if parallel else 1)
        assert set(seen) == {item.repository.repo_id for item in snapshots} and len(seen) == 8
        assert len(reads) == 2
        output = capsys.readouterr().out
        positions = [output.index(f"アラート監視により{item.repository.repo_id}") for item in snapshots]
        assert positions == sorted(positions)
    assert elapsed[1] < elapsed[0]
    print(f"同じ8対象の照会区間: 逐次={elapsed[0]:.3f}秒、最大3並行={elapsed[1]:.3f}秒")


@pytest.mark.parametrize("failure", [OSError("照会用ファイルを読めない"), ValueError("応答の形式が不正")])
@pytest.mark.parametrize("once", [False, True])
def test_public_loop_alert_failure_has_diagnostics_and_retry(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: Exception, once: bool
) -> None:
    """アラート照会例外でも常駐を継続し、対象付きの診断取得と再確認を案内する。"""
    _notes, snapshots = _targets(tmp_path, monkeypatch, (0,))
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    checks = iter((False, True))
    monkeypatch.setattr(control, "consume_process_loop_abort", lambda: next(checks))
    monkeypatch.setattr(loop, "_wait_for_changes", lambda *_args, **_kwargs: False)

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(loop._alerts, "check_process_loop_alerts", fail)  # pylint: disable=protected-access
    with pytest.raises(SystemExit) as exited:
        atk.main(["wi", "process-loop", "--no-update", *(["--once"] if once else [])], home=tmp_path)
    assert exited.value.code == 0
    output = capsys.readouterr()
    text = output.out + output.err
    assert str(failure) in text
    assert "次の操作:" in text
    assert str(log.log_path()) in text
    assert "atk wi check-alerts --target-repo" in text
    assert str(snapshots[0].repository.worktree) in text
    assert "再確認" in text
    assert ("今回処理できる対象は0件" if once else "変更検知を待機") in text
    assert "repository_alert_failed" in log.log_path().read_text(encoding="utf-8")


@pytest.mark.parametrize("status", ["completed", "incomplete", "blocked", "owner_wait", "prepare_failed"])
def test_public_once_finishes_one_collection(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    """1集合の未完了継続と所有判定を保ち、次のWI集合を起動しない。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._watch, "wait_for_start_permission", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    calls = 0

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        nonlocal calls
        calls += 1
        data = json.loads(_manifest(prompt).read_text(encoding="utf-8"))
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "once-coordinator"
        kwargs["on_session_started"]("once-coordinator")
        if status == "completed":
            (notes / "2026/10/repo-0-0.md").unlink()
            (notes / "2026/10/new.md").write_text(
                "---\nstate: inbox\ntype: awi\ntarget_repo: example.test/team/repo-0\n---\n\n# 次の集合\n", encoding="utf-8"
            )
        pathlib.Path(data["result_file"]).write_text(
            json.dumps({"repositories": {snapshots[0].repository.repo_id: {"status": status}}}), encoding="utf-8"
        )
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    if status == "prepare_failed":
        monkeypatch.setattr(loop._worktree, "prepare_session_target", lambda *_args, **_kwargs: None)  # pylint: disable=protected-access
    owner = (
        ownership.try_acquire(snapshots[0].repository.repo_id, worktree=snapshots[0].repository.worktree)
        if status == "owner_wait"
        else None
    )
    try:
        with pytest.raises(SystemExit) as exited:
            atk.main(["wi", "process-loop", "--once", "--no-alerts", "--no-update"], home=tmp_path)
        assert exited.value.code == (0 if status == "completed" else 1)
        assert calls == (0 if status in {"owner_wait", "prepare_failed"} else 3 if status == "incomplete" else 1)
    finally:
        if owner is not None:
            ownership.release(owner)


@pytest.mark.parametrize("counts", [(), (0, 0)])
def test_public_once_empty_does_not_wait(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, counts: tuple[int, ...]
) -> None:
    """登録対象の候補0件ではモデル・待機・所有を開始しない。"""
    _targets(tmp_path, monkeypatch, counts)
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access

    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("候補0件で待機または起動した")

    monkeypatch.setattr(loop, "_wait_for_changes", unexpected)
    monkeypatch.setattr(session, "run_process_session", unexpected)
    with pytest.raises(SystemExit) as exited:
        atk.main(["wi", "process-loop", "--once", "--no-alerts", "--no-update"], home=tmp_path)
    assert exited.value.code == 0


def test_public_agy_resume_delivers_current_manifest(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """明示再開でも今回の配送原本と結果保存先を使用し、対象別の完了を検収する。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    repository = snapshots[0].repository
    owner = ownership.try_acquire(repository.repo_id, worktree=repository.worktree, mode="coordinator")
    assert owner is not None
    owner = ownership.update_session(owner, "saved-agy-conversation")
    ownership.release(owner, completed=False)
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._watch, "wait_for_start_permission", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    monkeypatch.setattr(session, "select_available_orchestrator", lambda *_args, **_kwargs: ("agy", "model", "medium", None))
    manifests: list[dict[str, Any]] = []

    def execute(
        argv: list[str], _env: dict[str, str], _cwd: pathlib.Path, **_kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        assert argv[argv.index("--conversation") + 1] == "saved-agy-conversation"
        prompt = argv[argv.index("-i") + 1]
        manifest = _manifest(prompt)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        manifests.append(data)
        target = data["repositories"][0]
        stored = json.loads(pathlib.Path(target["ownership_record"]).read_text(encoding="utf-8"))
        assert stored["manifest"] == str(manifest)
        (notes / "2026/10/repo-0-0.md").unlink()
        pathlib.Path(data["result_file"]).write_text(
            json.dumps({"repositories": {repository.repo_id: {"status": "completed"}}}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(session, "_run_cli_session", execute)
    with pytest.raises(SystemExit) as exited:
        atk.main(
            ["wi", "process-loop", "--once", "--resume", "saved-agy-conversation", "--no-alerts", "--no-update"], home=tmp_path
        )
    assert exited.value.code == 0
    assert len(manifests) == 1
    assert pathlib.Path(manifests[0]["result_file"]).is_file()
    stored = json.loads(owner.path.read_text(encoding="utf-8"))
    assert stored["released"] and stored["completed"]


@pytest.mark.parametrize("failure", ["sync", "start_abort", "interrupt"])
def test_public_once_failure_does_not_wait(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    """1回処理の同期失敗・開始前中断・Ctrl+Cは成功にせず、再試行待機も始めない。"""
    _targets(tmp_path, monkeypatch, (1,))
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access

    def pull(*_args: Any, **_kwargs: Any) -> bool:
        if failure == "interrupt":
            raise KeyboardInterrupt
        return failure != "sync"

    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("失敗・中断後に待機またはモデルを起動した")

    monkeypatch.setattr(loop._watch, "pull_private_notes", pull)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._watch, "wait_for_start_permission", lambda *_args, **_kwargs: False)  # pylint: disable=protected-access
    monkeypatch.setattr(loop, "_wait_for_changes", unexpected)
    monkeypatch.setattr(session, "run_process_session", unexpected)
    with pytest.raises(SystemExit) as exited:
        atk.main(["wi", "process-loop", "--once", "--no-alerts", "--no-update"], home=tmp_path)
    assert exited.value.code == 1


@pytest.mark.parametrize(
    "record",
    [
        [],
        {"repositories": []},
        {"repositories": {"foreign": {"status": "completed"}}},
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


def _start_entry(
    notes: pathlib.Path,
    name: str,
    *,
    state: str = "inbox",
    repo: str = "github.com/example/myrepo",
    answer: str | None = None,
    timestamp: float = 1000.0,
    kind: str = "uwi",
) -> pathlib.Path:
    path = wi_entry_files.entry_path(notes, name)
    body = "\n## 回答\n<!-- ユーザーはこの行以降に回答を追記する -->\n" + (answer or "") + "\n" if kind == "uwi" else ""
    path.write_text(f"---\nstate: {state}\ntype: {kind}\ntarget_repo: {repo}\n---\n\n質問\n{body}", encoding="utf-8")
    os.utime(path, (timestamp, timestamp))
    return path
