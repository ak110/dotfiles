"""自動振り返りの間隔・自己記録・goal責務を公開操作で確認する。"""

import argparse
import datetime
import io
import json
import os
import pathlib
import sys
import types

import pytest

from agent_toolkit import atk, hook
from agent_toolkit._atk import run_script, run_skill
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


def _automatic_launch(monkeypatch, launch_id):
    """hookが渡す起動試行と同じ形で、自動起動のCLIを準備する。"""
    monkeypatch.setenv(session_review.LAUNCH_ENV, launch_id)
    session_review.write(session_review.directory() / "launches" / f"{launch_id}.json", {"status": "starting"})


def _run_cli():
    with pytest.raises(SystemExit) as result:
        atk.main(["run-skill", "--args", "batch", "agent-toolkit:review-session"])
    return result.value.code


def test_public_cli_concurrent_launches_create_one_run(tmp_path, monkeypatch):
    """hookの2要求をCLIへ接続し、自身を含む準備・確定と競合時の未処理保持を確認する。"""

    def record(name, *, batch=False):
        transcript = tmp_path / f"{name}.jsonl"
        transcript.write_text(
            json.dumps({"type": "user", "timestamp": "2026-10-28T00:00:00Z", "message": {"role": "user", "content": "要求"}})
            + "\n",
            encoding="utf-8",
        )
        with monkeypatch.context() as context:
            if batch:
                context.setenv(session_review.BATCH_ENV, "batch")
            else:
                context.delenv(session_review.BATCH_ENV, raising=False)
            session_review.record(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": name,
                    "transcript_path": str(transcript),
                    "cwd": str(tmp_path),
                },
                host="claude",
            )
        return transcript

    record("batch", batch=True)
    transcript = record("ordinary")
    requests = []
    executed = []
    failures = []

    def spawn(argv, **kwargs):
        requests.append((argv, kwargs["env"]))
        return types.SimpleNamespace(pid=None, wait=lambda: 0)

    def invoke(request):
        argv, environment = request
        with monkeypatch.context() as context:
            for key in (session_review.LAUNCH_ENV, session_review.BATCH_ENV):
                context.delenv(key, raising=False)
            for key, value in environment.items():
                context.setenv(key, value)
            with pytest.raises(SystemExit) as result:
                atk.main(argv[2:])
            return result.value.code

    def execute(args, **kwargs):
        del kwargs
        path = pathlib.Path(args.batch_manifest)
        executed.append(path)
        manifest = session_review.read(path)
        assert {target["session_id"] for target in manifest["targets"]} == {"batch", "ordinary"}
        assert {prepared["session_id"] for prepared in manifest["prepared"]} == {"batch", "ordinary"}
        assert all(pathlib.Path(prepared["scope_path"]).is_file() for prepared in manifest["prepared"])
        record("later")
        # 第1回の排他区間内で第2要求を受付し、終了後の間隔判定との混同を避ける。
        assert invoke(requests[1]) == 1
        with monkeypatch.context() as context:
            context.setenv(session_review.BATCH_ENV, str(path))
            assert run_script.dispatch(argparse.Namespace(script_name="session-review-state", script_args=["finish"])) == 0
        return 0

    def failure(manifest, path, reason, **kwargs):
        del manifest, path, kwargs
        failures.append(reason)

    monkeypatch.setattr("agent_toolkit._atk.run_skill_batch._failure", failure)
    monkeypatch.setattr(run_skill, "_dispatch_once", execute)
    monkeypatch.setattr("agent_toolkit._atk.wi.add.has_synced_run_report", lambda *a, **k: True)
    monkeypatch.setattr(session_activity, "review_activity", lambda *a, **k: types.SimpleNamespace(has_activity=False))
    monkeypatch.setattr(session_activity, "host_process", object)
    monkeypatch.setattr("agent_toolkit._atk.wi.repo.resolve_repo_id_or_raise", lambda *a, **k: None)
    with monkeypatch.context() as context:
        context.setattr("subprocess.Popen", spawn)
        for event, module in (("SessionStart", "rules_context"), ("SessionEnd", "session_end_cleanup")):
            payload = {
                "hook_event_name": event,
                "source": "startup",
                "session_id": "ordinary",
                "transcript_path": str(transcript),
                "cwd": str(tmp_path),
            }
            context.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
            assert hook.main([module]) == 0
    assert len(requests) == 2
    assert invoke(requests[0]) == 0
    assert len(executed) == 1
    assert len(list((session_review.directory() / "runs").glob("*.json"))) == 1
    assert session_review.read(executed[0])["status"] == "completed"
    second_launch = requests[1][1][session_review.LAUNCH_ENV]
    assert session_review.read(session_review.directory() / "launches" / f"{second_launch}.json")["status"] == "settled"
    assert not failures
    # 見送った対象は未処理として保持する。
    assert [target["session_id"] for target in session_review.pending()] == ["later"]


def test_public_failed_run_defers_automatic_retry_but_accepts_manual(tmp_path, monkeypatch):
    """失敗した実行の開始から6時間未満は自動起動せず、手動は即時に受け付ける。見送りは失敗報告を作らない。"""
    _pending("ordinary")
    scope = tmp_path / "scope.json"
    session_review.write(scope, {})
    failures = []

    def prepare(target, **kwargs):
        del kwargs
        return {"session_id": target["session_id"], "scope_path": str(scope)}

    def failure(manifest, path, reason, **kwargs):
        del kwargs
        failures.append(reason)
        session_review.write(path, {**manifest, "status": "failed", "report_synced": True})

    def failed_execution(args, **kwargs):
        del args, kwargs
        return 3

    monkeypatch.setattr("agent_toolkit._atk.run_skill_batch._prepare", prepare)
    monkeypatch.setattr("agent_toolkit._atk.run_skill_batch._failure", failure)
    monkeypatch.setattr(run_skill, "_dispatch_once", failed_execution)
    monkeypatch.setattr(session_activity, "review_activity", lambda *a, **k: types.SimpleNamespace(has_activity=False))
    _automatic_launch(monkeypatch, "a" * 32)
    assert _run_cli() == 3
    assert len(failures) == 1
    # 失敗の直後の開始イベントは、公開hookから起動しない。
    monkeypatch.delenv(session_review.LAUNCH_ENV)
    spawned = []

    def spawn(argv, **kwargs):
        del kwargs
        spawned.append(argv)
        return types.SimpleNamespace(pid=None, wait=lambda: 0)

    monkeypatch.setattr("subprocess.Popen", spawn)
    monkeypatch.setattr(session_activity, "host_process", object)
    payload = {"hook_event_name": "SessionStart", "source": "startup", "session_id": "next", "cwd": str(tmp_path)}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
    monkeypatch.setattr(
        "agent_toolkit._atk.wi.repo.resolve_repo_id_or_raise", lambda *a, **k: "https://github.com/ak110/dotfiles"
    )
    assert hook.main(["rules_context"]) == 0
    assert not spawned
    assert len(failures) == 1
    # 手動の再試行は間隔によらず準備へ進む。
    reached = []

    def manual_prepare(target, **kwargs):
        del kwargs
        reached.append(target["session_id"])
        raise LookupError("準備まで到達")

    monkeypatch.setattr("agent_toolkit._atk.run_skill_batch._prepare", manual_prepare)
    with pytest.raises(LookupError, match="準備まで到達"):
        atk.main(["run-skill", "--args", "batch", "agent-toolkit:review-session"])
    assert reached
    # 失敗した対象は未処理のまま保持し、手動の再試行の対象に残る。
    assert "ordinary" in {target["session_id"] for target in session_review.pending()}
