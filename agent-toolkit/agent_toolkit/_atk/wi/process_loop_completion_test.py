"""延期した候補と未完了の成果を、次回の統括へ引き継ぐ契約を検証する。"""

import argparse
import contextlib
import datetime
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import lane
from agent_toolkit._atk.wi import listing, readiness
from agent_toolkit._atk.wi import process_loop as loop
from agent_toolkit._atk.wi import process_loop_control as control
from agent_toolkit._atk.wi import process_loop_env as env
from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._atk.wi import process_loop_scheduled as scheduled
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing.process_loop_coordinator_support import _manifest, _start_entry, _targets


@pytest.mark.parametrize("wait_kind", ["cooldown", "uwi", "external"])
@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.cpu_limit(
    1.5, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大0.648秒。"
)
def test_public_once_accepts_deferred_candidates_and_recovers_completion(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, wait_kind: str, interrupted: bool
) -> None:
    """正当な延期後は再起動せず終了し、期限到来・依存解消で通常の候補へ戻る。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    repository = snapshots[0].repository
    monkeypatch.setattr(scheduled._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(scheduled._watch, "wait_for_start_permission", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(scheduled._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
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
        if interrupted and calls == 1:
            raise KeyboardInterrupt
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    argv = ["wi", "process-loop", "--once", "--no-alerts", "--no-update"]
    with pytest.raises(SystemExit) as exited:
        atk.main(argv, home=tmp_path)
    assert exited.value.code == (1 if interrupted else 0)
    if interrupted:
        control.cmd_process_loop_resume("deferred-coordinator")
        with pytest.raises(SystemExit) as recovered:
            atk.main(argv, home=tmp_path)
        assert recovered.value.code == 0
    assert calls == (2 if interrupted else 1)
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

    if remaining_kind in {"internal", "record"}:
        # 処理範囲は選定したWIに限るため、候補をレーンの選定原本で選んだ状態にする。
        selected = tmp_path / "pick-wi.yaml"
        selected.write_text(f"選定:\n- WI: {item.name}\n  レーン: lane-01\n", encoding="utf-8")
        finalized = {"lane": "lane-01", "selection_file": str(selected), "integration": {"phase": "finalized"}}
        monkeypatch.setattr(lane, "list_resources", lambda *_args: {"errors": [], "records": [{"record": finalized}]})
        monkeypatch.setattr(session, "cleanup_lane_session", lambda *_args, **_kwargs: None)
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


@pytest.mark.cpu_limit(
    2.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大0.900秒。"
)
def test_public_next_round_restores_processing_and_unintegrated_lane(tmp_path, monkeypatch, capsys):
    """元会話がprocessingと公開作成した未統合laneを再利用し、保存成果から作業を進める。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    repository = snapshots[0].repository.worktree
    git.commit_all(repository, "検証元のHEAD")
    branch = git.run_git(repository, "branch", "--show-current").stdout.strip()
    monkeypatch.setattr(scheduled._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(scheduled._watch, "wait_for_start_permission", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(scheduled._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    selected = tmp_path / "pick-wi.yaml"
    selected.write_text("レーンの所要時間:\n- レーン: lane-01\n  統合状態: 未統合\n", encoding="utf-8")
    item = notes / "2026/10/repo-0-0.md"
    artifact = tmp_path / "lane" / "result.txt"
    targets: list[dict[str, Any]] = []

    def execute(args, _cwd, prompt, child_env, **kwargs):
        assert args.resume == ("fresh-coordinator-1" if targets else None)
        data = json.loads(_manifest(prompt, child_env).read_text(encoding="utf-8"))
        target = data["repositories"][0]
        targets.append(target)
        identifier = args.resume or "fresh-coordinator-1"
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
        if count == 2:
            # 元会話の再開は予約した場合に限り、予約のない起動は新しい会話で始める。
            with contextlib.suppress(SystemExit):
                atk.main(["wi", "process-loop", "resume", "auto"], home=tmp_path)
        with pytest.raises(SystemExit) as ended:
            atk.main(["wi", "process-loop", "--once", "--no-alerts", "--no-update"], home=tmp_path)
        assert ended.value.code == 1
        assert len(targets) == count
    assert artifact.read_text(encoding="utf-8") == "再開後の成果"


@pytest.mark.parametrize("deletion_recorded", [True, False])
def test_check_result_limits_scope_to_selected_and_separates_removed_and_deferred(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], deletion_recorded: bool
) -> None:
    """選定外の候補は残件に数えず、削除済み（選定外を含む）と先送りの選定WIを分けて示し、記録の無い欠落は失敗にする。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (5,))
    repository = snapshots[0].repository
    git.init_repository(notes)
    git.commit_all(notes, "開始時の候補")
    unselected, adopted, deferred, removed, unselected_removed = (notes / f"2026/10/repo-0-{number}.md" for number in range(5))
    selected = tmp_path / "pick-wi.yaml"
    selected.write_text(
        "選定:\n" + "".join(f"- WI: {path.name}\n  レーン: lane-01\n" for path in (adopted, deferred, removed)),
        encoding="utf-8",
    )
    finalized = {"lane": "lane-01", "selection_file": str(selected), "integration": {"phase": "finalized"}}
    monkeypatch.setattr(lane, "list_resources", lambda *_args: {"errors": [], "records": [{"record": finalized}]})
    monkeypatch.setattr(session, "cleanup_lane_session", lambda *_args, **_kwargs: None)
    observed: list[tuple[str | int | None, str]] = []

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        kwargs["on_session_started"]("checking-coordinator")
        manifest = _manifest(prompt, child_env)
        adopted.write_text(adopted.read_text(encoding="utf-8").replace("state: inbox", "state: adopted"), encoding="utf-8")
        deferred.write_text(
            deferred.read_text(encoding="utf-8") + "\n## 反映後の観測の再開記録\n\n- 再開区分: 反映後の観測だけが残る\n",
            encoding="utf-8",
        )
        removed.unlink()
        unselected_removed.unlink()
        if deletion_recorded:
            git.commit_all(notes, "chore: remove 1 entries")
        capsys.readouterr()
        with pytest.raises(SystemExit) as status:
            atk.main(["wi", "process-loop", "status", "--check-result", str(manifest)], home=tmp_path)
        observed.append((status.value.code, capsys.readouterr().out))
        data = json.loads(manifest.read_text(encoding="utf-8"))
        pathlib.Path(data["result_file"]).write_text(
            json.dumps({"repositories": {repository.repo_id: {"status": "completed"}}}), encoding="utf-8"
        )
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    # 常駐の完了検収も同じ判定を使い、記録の無い欠落を完了扱いしない。
    resident = None
    with pytest.raises(ValueError, match="現キューから取得できない") if not deletion_recorded else contextlib.nullcontext():
        resident = loop.run_coordinator(argparse.Namespace(resume=None, auto_resume=False, worktree=None), snapshots)
    code, output = observed[0]
    if not deletion_recorded:
        assert code == 1
        return
    assert code == 0
    record = json.loads(output)["repositories"][repository.repo_id]
    assert record["status"] == "ready"
    assert unselected.name not in record["observed_remaining"]["wi"]
    assert unselected_removed.name not in record["observed_remaining"]["wi"]
    assert record["removed_wi"] == sorted([removed.name, unselected_removed.name])
    # 常駐の終了処理が保存する結果も、公開statusと同じ削除済みの一覧を持つ。
    assert resident is not None
    assert resident[repository.repo_id]["removed_wi"] == record["removed_wi"]
    assert record["deferred_wi"] == [deferred.name]
