"""自動振り返りの間隔・自己記録・goal責務を公開操作で確認する。"""

import datetime
import io
import json
import os
import pathlib
import sys
import types

import pytest

from agent_toolkit import atk, hook
from agent_toolkit._atk import run_skill
from agent_toolkit._common import session_activity, session_review


@pytest.mark.parametrize("mode", [None, "batch"])
def test_goal_separates_child_completion_from_launcher_verification(mode):
    goal = run_skill.build_goal("agent-toolkit:review-session", mode)
    assert "終了を要求" in goal
    assert "報告同期の検収は起動元がこのセッションの終了後に行う" in goal
    assert "このセッションの完了条件に含めない" in goal
    assert ("session-review-state -- finish" in goal) is (mode == "batch")
    assert ("batchの処理済み範囲の確定も起動元が終了後に行う" in goal) is (mode == "batch")


def _pending(name, *, batch=False):
    path = session_review.directory() / "pending" / f"claude-{name}.json"
    session_review.write(path, {"key": path.stem, "host": "claude", "session_id": name, "review_batch": batch})


def _previous(*, legacy, now, elapsed):
    path = session_review.directory() / "runs" / "previous.json"
    value = {"run_id": "previous", "status": "completed", "targets": [], "prepared": []}
    if not legacy:
        value["started_at"] = datetime.datetime.fromtimestamp(now - elapsed, datetime.UTC).isoformat()
    session_review.write(path, value)
    os.utime(path, (now - elapsed if legacy else now, now - elapsed if legacy else now))


@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
@pytest.mark.parametrize("elapsed", [21599, 21600])
@pytest.mark.parametrize("legacy", [False, True])
def test_public_hook_checks_six_hour_boundary(tmp_path, monkeypatch, event, elapsed, legacy):
    now = 1793400000.0
    _previous(legacy=legacy, now=now, elapsed=elapsed)
    _pending("ordinary")
    monkeypatch.setattr(session_activity, "host_process", object)
    monkeypatch.setattr("time.time", lambda: now)
    calls = []

    def spawn(argv, **kwargs):
        del kwargs
        calls.append(argv)
        return types.SimpleNamespace(pid=None, wait=lambda: 0)

    monkeypatch.setattr("subprocess.Popen", spawn)
    payload = {"hook_event_name": event, "source": "startup", "session_id": "ordinary", "cwd": str(tmp_path)}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
    monkeypatch.setattr(
        "agent_toolkit._atk.wi.repo.resolve_repo_id_or_raise", lambda *a, **k: "https://github.com/ak110/dotfiles"
    )
    assert hook.main(["rules_context" if event == "SessionStart" else "session_end_cleanup"]) == 0
    assert bool(calls) is (elapsed == 21600)


@pytest.mark.parametrize("batch", [True, False])
def test_public_hook_records_batch_without_recursive_launch(tmp_path, monkeypatch, batch):
    if batch:
        monkeypatch.setenv(session_review.BATCH_ENV, "manifest")
    _pending("previous-batch", batch=True)
    calls = []

    def spawn(argv, **kwargs):
        del kwargs
        calls.append(argv)
        return types.SimpleNamespace(pid=None, wait=lambda: 0)

    monkeypatch.setattr("subprocess.Popen", spawn)
    monkeypatch.setattr(session_activity, "host_process", object)
    payload = {"hook_event_name": "SessionStart", "source": "startup", "session_id": "current", "cwd": str(tmp_path)}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
    monkeypatch.setattr(
        "agent_toolkit._atk.wi.repo.resolve_repo_id_or_raise", lambda *a, **k: "https://github.com/ak110/dotfiles"
    )
    assert hook.main(["rules_context"]) == 0
    current = next(item for item in session_review.pending() if item["session_id"] == "current")
    assert current["review_batch"] is batch
    assert bool(calls) is not batch


@pytest.mark.parametrize("manual", [False, True])
def test_public_cli_rechecks_interval_after_lock(monkeypatch, manual):
    now = 1793400000.0
    _previous(legacy=False, now=now, elapsed=21599)
    _pending("ordinary")
    monkeypatch.setattr("time.time", lambda: now)
    if not manual:
        launch_id = "a" * 32
        monkeypatch.setenv(session_review.LAUNCH_ENV, launch_id)
        session_review.write(session_review.directory() / "launches" / f"{launch_id}.json", {"status": "starting"})
    calls = []

    def execute(args, **kwargs):
        del kwargs
        calls.append(args)
        raise LookupError("準備まで到達")

    monkeypatch.setattr(run_skill, "_dispatch_once", execute)
    if manual:
        # 準備より前に到達を確認し、外部ホストは起動しない。
        monkeypatch.setattr(session_activity, "review_activity", lambda *a, **k: types.SimpleNamespace(has_activity=False))
        monkeypatch.setattr("agent_toolkit._atk.run_skill_batch._prepare", execute)
        with pytest.raises(LookupError, match="準備まで到達"):
            atk.main(["run-skill", "--args", "batch", "agent-toolkit:review-session"])
    else:
        with pytest.raises(SystemExit) as result:
            atk.main(["run-skill", "--args", "batch", "agent-toolkit:review-session"])
        assert result.value.code == 0
    assert bool(calls) is manual


@pytest.mark.parametrize("ordinary", ["absent", "inactive", "active", "failure-unreported"])
@pytest.mark.parametrize("manual", [False, True])
def test_public_cli_selects_batch_only_with_other_eligible_targets(monkeypatch, ordinary, manual):
    _pending("batch", batch=True)
    if ordinary != "absent":
        _pending("ordinary")
        if ordinary == "failure-unreported":
            path = session_review.directory() / "pending" / "claude-ordinary.json"
            session_review.write(path, {**session_review.read(path), "failure_unreported": True})
    if not manual:
        launch_id = "a" * 32
        monkeypatch.setenv(session_review.LAUNCH_ENV, launch_id)
        session_review.write(session_review.directory() / "launches" / f"{launch_id}.json", {"status": "starting"})

    def activity(ids, **kwargs):
        del kwargs
        return types.SimpleNamespace(has_activity=ordinary == "active" and "ordinary" in ids)

    def prepare(target, **kwargs):
        del kwargs
        raise LookupError(target["session_id"])

    monkeypatch.setattr(session_activity, "review_activity", activity)
    monkeypatch.setattr("agent_toolkit._atk.run_skill_batch._prepare", prepare)
    eligible = ordinary == "inactive" or (manual and ordinary == "failure-unreported")
    if eligible:
        with pytest.raises(LookupError, match="batch"):
            atk.main(["run-skill", "--args", "batch", "agent-toolkit:review-session"])
        manifest = session_review.read(next((session_review.directory() / "runs").glob("*.json")))
        assert {target["session_id"] for target in manifest["targets"]} == {"batch", "ordinary"}
        assert datetime.datetime.fromisoformat(manifest["started_at"]).tzinfo is not None
    else:
        with pytest.raises(SystemExit) as result:
            atk.main(["run-skill", "--args", "batch", "agent-toolkit:review-session"])
        assert result.value.code == 0
        assert not list((session_review.directory() / "runs").glob("*.json"))
    assert any(target["session_id"] == "batch" for target in session_review.pending())


@pytest.mark.parametrize("manual", [False, True])
def test_public_cli_applies_interval_to_targets_arriving_during_batch(tmp_path, monkeypatch, manual):
    _pending("initial")
    if not manual:
        launch_id = "a" * 32
        monkeypatch.setenv(session_review.LAUNCH_ENV, launch_id)
        session_review.write(session_review.directory() / "launches" / f"{launch_id}.json", {"status": "starting"})
    scope = tmp_path / "scope.json"
    session_review.write(scope, {})
    calls = []

    def prepare(target, **kwargs):
        del kwargs
        return {"session_id": target["session_id"], "scope_path": str(scope)}

    def execute(args, **kwargs):
        del kwargs
        path = pathlib.Path(args.batch_manifest)
        manifest = session_review.read(path)
        calls.append([target["session_id"] for target in manifest["targets"]])
        session_review.write(path, {**manifest, "analysis_succeeded": True})
        return 0

    def complete(prepared, **kwargs):
        del kwargs
        (session_review.directory() / "pending" / f"claude-{prepared['session_id']}.json").unlink()
        if prepared["session_id"] == "initial":
            _pending("later")

    monkeypatch.setattr("agent_toolkit._atk.run_skill_batch._prepare", prepare)
    monkeypatch.setattr(run_skill, "_dispatch_once", execute)
    monkeypatch.setattr(session_review, "complete", complete)
    monkeypatch.setattr("agent_toolkit._atk.wi.add.has_synced_run_report", lambda *a, **k: True)
    monkeypatch.setattr(session_activity, "review_activity", lambda *a, **k: types.SimpleNamespace(has_activity=False))
    with pytest.raises(SystemExit) as result:
        atk.main(["run-skill", "--args", "batch", "agent-toolkit:review-session"])
    assert result.value.code == 0
    assert calls == ([["initial"], ["later"]] if manual else [["initial"]])
    assert bool(session_review.pending()) is not manual


def test_public_cli_skips_batch_when_other_target_cannot_be_prepared(tmp_path, monkeypatch):
    _pending("batch", batch=True)
    _pending("ordinary")
    scope = tmp_path / "scope.json"
    session_review.write(scope, {})
    failures = []

    def prepare(target, **kwargs):
        del kwargs
        if target["session_id"] == "ordinary":
            raise ValueError("通常対象の記録がない")
        return {"session_id": target["session_id"], "scope_path": str(scope)}

    def failure(manifest, path, reason, **kwargs):
        del kwargs
        failures.append(reason)
        session_review.write(path, {**manifest, "status": "failed", "report_synced": True})

    def unexpected(args, **kwargs):
        del args, kwargs
        raise AssertionError("batch記録だけではモデルを起動しない")

    monkeypatch.setattr("agent_toolkit._atk.run_skill_batch._prepare", prepare)
    monkeypatch.setattr("agent_toolkit._atk.run_skill_batch._failure", failure)
    monkeypatch.setattr(run_skill, "_dispatch_once", unexpected)
    monkeypatch.setattr(session_activity, "review_activity", lambda *a, **k: types.SimpleNamespace(has_activity=False))
    with pytest.raises(SystemExit) as result:
        atk.main(["run-skill", "--args", "batch", "agent-toolkit:review-session"])
    assert result.value.code == 1
    assert len(failures) == 1 and "通常対象の記録がない" in failures[0]
    assert {target["session_id"] for target in session_review.pending()} == {"batch", "ordinary"}
