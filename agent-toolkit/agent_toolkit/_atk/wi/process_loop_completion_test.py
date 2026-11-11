"""延期した候補と未完了の成果を、次回の統括へ引き継ぐ契約を検証する。"""

import argparse
import datetime
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import lane
from agent_toolkit._atk.wi import listing, readiness
from agent_toolkit._atk.wi import process_loop as loop
from agent_toolkit._atk.wi import process_loop_env as env
from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing.process_loop_coordinator_support import _manifest, _start_entry, _targets


@pytest.mark.parametrize("wait_kind", ["cooldown", "uwi", "external"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_public_once_accepts_deferred_candidates_and_recovers_completion(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, wait_kind: str, interrupted: bool
) -> None:
    """正当な延期後は再起動せず終了し、期限到来・依存解消で通常の候補へ戻る。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    repository = snapshots[0].repository
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._watch, "wait_for_start_permission", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    clock = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)
    original_readiness = readiness.calculate_readiness

    def calculate(*args: Any, **kwargs: Any) -> readiness.ReadinessResult:
        kwargs["now"] = clock
        return original_readiness(*args, **kwargs)

    monkeypatch.setattr(readiness, "calculate_readiness", calculate)
    calls = 0
    result_paths: list[pathlib.Path] = []
    item = notes / "2026/10/repo-0-0.md"
    blocker: pathlib.Path | None = None

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        nonlocal calls, blocker
        calls += 1
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "deferred-coordinator"
        kwargs["on_session_started"]("deferred-coordinator")
        data = json.loads(_manifest(prompt, child_env).read_text(encoding="utf-8"))
        if wait_kind == "cooldown":
            metadata = "cooldown_until: '2030-01-02T00:00:00+00:00'\n"
        else:
            metadata = "depends_on:\n- blocker.md\n"
            blocker = _start_entry(
                notes,
                "blocker.md",
                repo=repository.repo_id if wait_kind == "uwi" else "example.test/team/external",
                kind="uwi" if wait_kind == "uwi" else "awi",
            )
        item.write_text(
            f"---\nstate: inbox\ntype: awi\ntarget_repo: {repository.repo_id}\n{metadata}---\n\n# 再開待ち\n",
            encoding="utf-8",
        )
        path = pathlib.Path(data["result_file"])
        result_paths.append(path)
        path.write_text(
            json.dumps({"repositories": {repository.repo_id: {"status": "completed", "remaining": "解除後に再開"}}}),
            encoding="utf-8",
        )
        if interrupted:
            raise KeyboardInterrupt
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    argv = ["wi", "process-loop", "--once", "--no-alerts", "--no-update"]
    with pytest.raises(SystemExit) as exited:
        atk.main(argv, home=tmp_path)
    assert exited.value.code == (1 if interrupted else 0)
    if interrupted:
        with pytest.raises(SystemExit) as recovered:
            atk.main([*argv, "--resume", "deferred-coordinator"], home=tmp_path)
        assert recovered.value.code == 0
    assert calls == 1
    records = [json.loads(path.read_text(encoding="utf-8")) for path in ownership.ownership_directory().glob("*.json")]
    assert len(records) == 1 and records[0]["released"] and records[0]["completed"]
    assert not listing.configured_repo_snapshot(notes)[0].candidates
    saved_results = list(ownership.ownership_directory().joinpath("rounds").glob("*.result.json"))
    accepted = [json.loads(path.read_text(encoding="utf-8")) for path in saved_results if path not in result_paths]
    assert any(record["status"] == "completed" for record in accepted)
    if wait_kind == "cooldown":
        clock += datetime.timedelta(days=1)
    else:
        assert blocker is not None
        if wait_kind == "uwi":
            blocker.write_text(blocker.read_text(encoding="utf-8") + "承認する\n", encoding="utf-8")
        else:
            blocker.write_text(blocker.read_text(encoding="utf-8").replace("state: inbox", "state: adopted"), encoding="utf-8")
    assert item.name in {entry[0].name for entry in listing.configured_repo_snapshot(notes)[0].candidates}


@pytest.mark.parametrize(
    "remaining_kind", ["processing", "processing-uwi", "invalid-cooldown", "missing", "cycle", "internal", "lane", "record"]
)
def test_completed_result_preserves_actionable_work_and_unintegrated_lanes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, remaining_kind: str
) -> None:
    """今回の候補外のprocessing・修復診断と、内部依存・未統合成果は残件として保持する。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    repository = snapshots[0].repository
    item = notes / "2026/10/repo-0-0.md"
    calls = 0

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        nonlocal calls
        calls += 1
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "unfinished-coordinator"
        kwargs["on_session_started"]("unfinished-coordinator")
        data = json.loads(_manifest(prompt, child_env).read_text(encoding="utf-8"))
        if remaining_kind in {"processing", "processing-uwi", "invalid-cooldown", "missing", "cycle", "lane"}:
            item.write_text(
                item.read_text(encoding="utf-8").replace(
                    "type: awi\n", "type: awi\ncooldown_until: '2999-01-01T00:00:00+00:00'\n"
                )
                if calls == 1
                else item.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        if remaining_kind in {"processing", "processing-uwi", "invalid-cooldown", "missing", "cycle", "internal"}:
            metadata = {
                "processing": "state: processing\n",
                "processing-uwi": "state: processing\n",
                "invalid-cooldown": "state: inbox\ncooldown_until: invalid\n",
                "missing": "state: inbox\ndepends_on: [absent.md]\n",
                "cycle": "state: inbox\ndepends_on: [other.md]\n",
                "internal": "state: inbox\n",
            }[remaining_kind]
            extra = notes / "2026/10/extra.md"
            kind = "uwi" if remaining_kind == "processing-uwi" else "awi"
            extra.write_text(f"---\n{metadata}type: {kind}\ntarget_repo: {repository.repo_id}\n---\n# 残件\n", encoding="utf-8")
            if remaining_kind == "internal" and calls == 1:
                item.write_text(
                    item.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [extra.md]\n"),
                    encoding="utf-8",
                )
            if remaining_kind == "cycle":
                (notes / "2026/10/other.md").write_text(
                    f"---\nstate: inbox\ntype: awi\ntarget_repo: {repository.repo_id}\ndepends_on: [extra.md]\n---\n# 循環\n",
                    encoding="utf-8",
                )
        if remaining_kind == "record" and calls == 1:
            item.write_text(item.read_text(encoding="utf-8") + "\n## 再開記録\n後日確認する。\n", encoding="utf-8")
        pathlib.Path(data["result_file"]).write_text(
            json.dumps({"repositories": {repository.repo_id: {"status": "completed"}}}), encoding="utf-8"
        )
        return True

    if remaining_kind == "lane":
        git.commit_all(repository.worktree, "検証用のレーンHEAD")
        branch = git.run_git(repository.worktree, "branch", "--show-current").stdout.strip()
        monkeypatch.setattr(
            lane,
            "list_resources",
            lambda *_args: {
                "errors": [],
                "records": [{"record": {"lane": "lane-01", "repo": str(repository.worktree), "branch": branch}}],
            },
        )
        monkeypatch.setattr(session, "cleanup_lane_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(session, "run_process_session", execute)
    result = loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)[
        repository.repo_id
    ]
    assert calls == 1 and result["status"] == "incomplete"
    assert result["observed_remaining"]["lanes" if remaining_kind == "lane" else "wi"]
    assert not json.loads(next(ownership.ownership_directory().glob("*.json")).read_text(encoding="utf-8"))["completed"]


def test_public_next_round_restores_processing_and_unintegrated_lane(tmp_path, monkeypatch, capsys):
    """新統括がprocessingと公開作成した未統合laneを再利用し、保存成果から作業を進める。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    repository = snapshots[0].repository.worktree
    git.commit_all(repository, "検証元のHEAD")
    branch = git.run_git(repository, "branch", "--show-current").stdout.strip()
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._watch, "wait_for_start_permission", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    selected = tmp_path / "pick-wi.yaml"
    selected.write_text("レーンの所要時間:\n- レーン: lane-01\n  統合状態: 未統合\n", encoding="utf-8")
    item = notes / "2026/10/repo-0-0.md"
    artifact = tmp_path / "lane" / "result.txt"
    targets = []

    def execute(args, _cwd, prompt, child_env, **kwargs):
        assert args.resume is None
        data = json.loads(_manifest(prompt, child_env).read_text(encoding="utf-8"))
        target = data["repositories"][0]
        targets.append(target)
        identifier = f"fresh-coordinator-{len(targets)}"
        kwargs["on_session_started"](identifier)
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = identifier
        if len(targets) == 1:
            capsys.readouterr()
            with pytest.raises(SystemExit) as created:
                atk.main(
                    [
                        "lane",
                        "create",
                        "--repo",
                        str(repository),
                        "--base-branch",
                        branch,
                        "--lane",
                        "lane-01",
                        "--selection-file",
                        str(selected),
                        "--session-id",
                        target["lane_session_id"],
                        "--worktree",
                        str(artifact.parent),
                    ],
                    home=tmp_path,
                )
            assert created.value.code == 0
            capsys.readouterr()
            artifact.write_text("保存済み成果", encoding="utf-8")
            item.write_text(item.read_text(encoding="utf-8").replace("state: inbox", "state: processing"), encoding="utf-8")
        else:
            assert target["previous"]["session_id"] == "fresh-coordinator-1"
            assert target["lane_session_id"] == targets[0]["lane_session_id"]
            assert target["worktree"] == targets[0]["worktree"]
            candidates = json.loads(pathlib.Path(target["candidate_file"]).read_text(encoding="utf-8"))
            assert item.name in {candidate["filename"] for candidate in candidates}
            assert "state: processing" in item.read_text(encoding="utf-8")
            resources = lane.list_resources(target["lane_session_id"])
            assert not resources["errors"]
            assert [entry["record"]["lane"] for entry in resources["records"]] == ["lane-01"]
            assert artifact.read_text(encoding="utf-8") == "保存済み成果"
            artifact.write_text("再開後の成果", encoding="utf-8")
            item.unlink()
        pathlib.Path(data["result_file"]).write_text(
            json.dumps({"repositories": {target["target_repo"]: {"status": "incomplete"}}}), encoding="utf-8"
        )
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    for count in (1, 2):
        with pytest.raises(SystemExit) as ended:
            atk.main(["wi", "process-loop", "--once", "--no-alerts", "--no-update"], home=tmp_path)
        assert ended.value.code == 1
        assert len(targets) == count
    assert artifact.read_text(encoding="utf-8") == "再開後の成果"
