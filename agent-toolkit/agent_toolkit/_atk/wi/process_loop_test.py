"""隔離したGit・キューで、登録全体の統括と対象別の所有・継続を検証する。"""

import argparse
import json
import os
import pathlib
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
    monkeypatch.setattr(session, "select_available_orchestrator", lambda *_args, **_kwargs: ("claude", "model", "medium"))
    monkeypatch.setattr(session, "resolve_orchestrator_specs", lambda: [("claude", "model", "medium")])
    return notes, listing.configured_repo_snapshot(notes)


def _manifest(prompt: str) -> pathlib.Path:
    start = prompt.index("は ") + len("は ")
    return pathlib.Path(prompt[start : prompt.index(" を読む", start)])


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
