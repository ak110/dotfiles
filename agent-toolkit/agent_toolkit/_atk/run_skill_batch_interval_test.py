import argparse
import datetime
import io
import json
import os
import pathlib
import subprocess
import sys
import types

import filelock
import pytest

from agent_toolkit import atk, hook
from agent_toolkit._atk import run_script, run_skill, run_skill_batch, weekly_schedule
from agent_toolkit._common import (
    diagnostic_process,
    mcp_environment,
    private_notes,
    session_activity,
    session_review,
    session_state,
)


def _capture_batch_launches(monkeypatch, *, mode="batch"):
    """hook本体が行うGit照会と、非同期振り返りの起動を区別する。"""
    original_popen = subprocess.Popen
    requests = []
    expected = [
        sys.executable,
        str(pathlib.Path(run_skill.__file__).resolve().parents[1] / "atk.py"),
        "run-skill",
        "--args",
        mode,
        "agent-toolkit:review-session",
    ]

    wrapped = diagnostic_process.command(expected)
    alternate = diagnostic_process.command([*expected[:4], "meta" if mode == "batch" else "batch", expected[-1]])

    def spawn(argv, **kwargs):
        if list(argv) == wrapped:
            requests.append((expected, kwargs["env"]))
            return types.SimpleNamespace(pid=None, wait=lambda: 0)
        assert list(argv) != alternate
        return original_popen(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    if mode == "batch":
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_META_AUTO_START", "false")
    return requests


@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
@pytest.mark.parametrize("reason", [None, "meta-child", "consultation", "disabled"])
def test_public_hook_launches_initial_meta_only_when_allowed(monkeypatch, event, reason):
    monkeypatch.setattr(session_activity, "living_root_session_ids", frozenset)
    monkeypatch.setattr(session_activity, "host_process", object)
    if reason == "meta-child":
        monkeypatch.setenv(
            mcp_environment.SESSION_START_INPUT_ENV, json.dumps({"skill": "agent-toolkit:review-session", "arguments": "meta"})
        )
    elif reason == "disabled":
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START", "false")
    elif reason == "consultation":
        session_state.update_state("open", lambda state: {**state, "add_awi_skill_invoked": True})
        monkeypatch.setattr(session_activity, "living_root_session_ids", lambda: frozenset({"open"}))
    calls = _capture_batch_launches(monkeypatch, mode="meta")
    payload = {"hook_event_name": event, "source": "startup", "session_id": "meta-case"}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
    assert hook.main(["rules_context" if event == "SessionStart" else "session_end_cleanup"]) == 0
    assert len(calls) == int(reason is None)


@pytest.mark.parametrize("with_legacy_reports", [False, True])
def test_public_meta_cli_selects_report_target_before_running(monkeypatch, tmp_path, with_legacy_reports):
    notes = private_notes.default_private_notes()
    now = datetime.datetime.now(datetime.UTC)
    samples = [
        (1, "github.com/test/current", "session-review", ""),
        (3, "github.com/test/current", "run-skill", "## ユーザーコメント\n\n同じ不具合が再発した。\n"),
        (2, "github.com/test/other", "add-awi-by-user", ""),
        (4, "github.com/test/other", "run-skill", ""),
        (8, "github.com/test/other", "session-review", ""),
        (9, "github.com/test/other", "session-review", ""),
    ]
    for elapsed, target, source, body in samples:
        inserted = now - datetime.timedelta(days=elapsed)
        path = notes / inserted.strftime("%Y/%m/%Y%m%d-%H%M%S-001.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: awi\nsource: {source}\ntarget_repo: {target}\n---\n{body}", encoding="utf-8")
    if with_legacy_reports:
        inserted = now - datetime.timedelta(days=1)
        for index in range(2, 4):
            path = notes / inserted.strftime(f"%Y/%m/%Y%m%d-%H%M%S-{index:03d}.md")
            path.write_text(
                f"---\ntype: uwi\nrun_skill_report: true\nscope: {session_review.LEGACY_BATCH_SCOPE}\n"
                "target_repo: github.com/test/other\n---\n",
                encoding="utf-8",
            )
    calls = []

    def execute(args):
        calls.append((args.target_repo, pathlib.Path(args.work_dir)))
        return 0

    monkeypatch.setattr(run_skill, "_dispatch_once", execute)
    cwd_option = "--work-dir" if with_legacy_reports else "--target-repo"
    argv = ["run-skill", cwd_option, str(tmp_path), "--args", "meta", "agent-toolkit:review-session"]
    batch_lock = session_review.directory() / "batch.lock"
    batch_lock.parent.mkdir(parents=True, exist_ok=True)
    with filelock.FileLock(str(batch_lock)), pytest.raises(SystemExit) as result:
        atk.main(argv)
    assert result.value.code == 1
    assert not calls
    with pytest.raises(SystemExit) as result:
        atk.main(argv)
    assert result.value.code == 0
    expected = "other" if with_legacy_reports else "current"
    assert calls == [(f"github.com/test/{expected}", tmp_path.resolve())]


@pytest.mark.parametrize(
    ("option", "work_dir"),
    [
        (None, None),
        ("--work-dir", "missing"),
        ("--work-dir", "relative"),
        ("--work-dir", "file"),
        ("--work-dir", "~atk-no-such-user-5d2f8b614ac74"),
        ("--target-repo", "~atk-no-such-user-5d2f8b614ac74"),
    ],
)
def test_manual_meta_with_invalid_work_dir_leaves_auto_due(monkeypatch, tmp_path, option, work_dir, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setattr(run_skill, "_dispatch_once", lambda _args: pytest.fail("起動してはならない"))
    argv = ["run-skill", "--args", "meta", "agent-toolkit:review-session"]
    if work_dir == "file":
        (tmp_path / work_dir).touch()
    if work_dir == "relative":
        (tmp_path / work_dir).mkdir()
    if work_dir and work_dir.startswith("~"):
        (tmp_path / work_dir).mkdir()
    if work_dir is not None:
        argv += [option, work_dir if work_dir == "relative" or work_dir.startswith("~") else str(tmp_path / work_dir)]
    with pytest.raises(SystemExit) as result:
        atk.main(argv)
    assert result.value.code == 2
    assert "次の操作" in capsys.readouterr().err
    assert not (weekly_schedule.record_path("meta")).exists()


def test_meta_failure_keeps_auto_suppressed_after_interval(monkeypatch, tmp_path):
    """失敗を別スコープで報告し、間隔経過後も未完了の是正があればmetaだけを見送る。"""
    monkeypatch.setattr(session_activity, "living_root_session_ids", frozenset)
    saved = []

    def save_failure(key, message):
        saved.append((key, message()))
        return True

    monkeypatch.setattr(run_skill_batch, "_save_failure_report", save_failure)

    def execute(args):
        args.log_path = tmp_path / "meta-run.log"
        args.log_path.write_text("失敗した段階のログ", encoding="utf-8")
        return 3

    monkeypatch.setattr(run_skill, "_dispatch_once", execute)
    with pytest.raises(SystemExit) as result:
        atk.main(["run-skill", "--target-repo", str(tmp_path), "--args", "meta", "agent-toolkit:review-session"])
    assert result.value.code == 3
    assert len(saved) == 1
    key, report = saved[0]
    assert "review-session meta" in report
    assert "meta-run.log" in report
    assert str(tmp_path) not in report
    record = session_review.read(weekly_schedule.record_path("meta"))
    assert record["failure_key"] == key
    assert "completed" not in record
    now = datetime.datetime.fromisoformat(record["started_at"]).timestamp() + 7 * 86400

    def failure_entry(queried, states):
        assert queried == key
        return (tmp_path / "failure.md", {}) if "inbox" in states else None

    monkeypatch.setattr(run_skill_batch, "_failure_entry", failure_entry)
    assert not run_skill_batch.weekly_auto_start_due("meta", now=now)
    assert session_review.auto_start_due(now=now)


def test_auto_meta_uses_registered_worktree_from_repository_url(monkeypatch, tmp_path):
    _automatic_launch(monkeypatch, "a" * 32)
    monkeypatch.setattr(session_activity, "living_root_session_ids", frozenset)
    monkeypatch.setattr(run_skill_batch, "_plugin_repository", lambda: "https://github.com/test/package")
    monkeypatch.setattr(
        run_skill_batch.config_settings,
        "repository_settings",
        lambda **_args: {"github.com/test/package": {"path": str(tmp_path)}},
    )
    locations = []

    def dispatch(args):
        locations.append(args.work_dir)
        return 0

    monkeypatch.setattr(run_skill, "_dispatch_once", dispatch)
    with pytest.raises(SystemExit) as result:
        atk.main(["run-skill", "--args", "meta", "agent-toolkit:review-session"])
    assert result.value.code == 0
    assert locations == [str(tmp_path)]


def test_auto_meta_unregistered_worktree_reports_registration_lookup(monkeypatch):
    """モデル起動前の失敗は、手動の別作業ツリーを使わず登録元の診断を案内する。"""
    _automatic_launch(monkeypatch, "a" * 32)
    monkeypatch.setattr(session_activity, "living_root_session_ids", frozenset)
    monkeypatch.setattr(run_skill_batch.config_settings, "repository_settings", lambda **_args: {})
    monkeypatch.setattr(run_skill, "_dispatch_once", lambda _args: pytest.fail("起動してはならない"))
    reports = []

    def save_failure(_key, message):
        reports.append(message())
        return True

    monkeypatch.setattr(run_skill_batch, "_save_failure_report", save_failure)

    with pytest.raises(SystemExit) as result:
        atk.main(["run-skill", "--args", "meta", "agent-toolkit:review-session"])
    assert result.value.code == 1
    assert len(reports) == 1
    report = reports[0]
    assert "`atk config repos list`" in report and "実行ログは未作成" in report


def test_meta_failed_report_save_explains_next_action(monkeypatch, tmp_path, caplog):
    def fail_save(*_args, **_kwargs):
        raise OSError("保存先を開けない")

    monkeypatch.setattr(run_skill, "_dispatch_once", lambda _args: 3)
    monkeypatch.setattr(run_skill_batch.add, "add_entries", fail_save)
    with pytest.raises(SystemExit) as result:
        atk.main(["run-skill", "--target-repo", str(tmp_path), "--args", "meta", "agent-toolkit:review-session"])
    assert result.value.code == 3
    assert "WI保存先を直してから次回の起動で再試行する" in caplog.text
    assert session_review.read(weekly_schedule.record_path("meta"))["report_synced"] is False


@pytest.mark.parametrize("manual", [False, True])
def test_meta_retries_unsynced_failure_report_without_rerunning(monkeypatch, manual):
    """是正報告を保存できなかった回は、次の実行機会に保存だけを再試行する。"""
    start = weekly_schedule.record_path("meta")
    elapsed = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=8)
    session_review.write(
        start,
        {
            "started_at": elapsed.isoformat(),
            "failure_key": "previous",
            "failure_stage": "execution-3",
            "exit_code": 3,
            "report_synced": False,
        },
    )
    monkeypatch.setattr(session_activity, "living_root_session_ids", frozenset)
    if not manual:
        _automatic_launch(monkeypatch, "a" * 32)
    saved = []

    def save_failure(key, _message):
        saved.append(key)
        return not manual

    monkeypatch.setattr(run_skill_batch, "_save_failure_report", save_failure)
    monkeypatch.setattr(run_skill, "_dispatch_once", lambda _args: pytest.fail("起動してはならない"))
    with pytest.raises(SystemExit) as result:
        atk.main(["run-skill", "--args", "meta", "agent-toolkit:review-session"])
    assert result.value.code == int(manual)
    assert saved == ["previous"]
    assert session_review.read(start)["report_synced"] is not manual


def _pending(name, *, batch=False):
    path = session_review.directory() / "pending" / f"claude-{name}.json"

    transcript = session_review.directory() / "test-records" / f"{name}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("{}\n", encoding="utf-8")
    session_review.write(
        path,
        {
            "key": path.stem,
            "host": "claude",
            "session_id": name,
            "review_batch": batch,
            "generation": f"{name}-generation",
            "transcript_path": str(transcript),
        },
    )


def _previous(*, legacy, now, elapsed):
    path = session_review.directory() / "runs" / "previous.json"
    value = {"run_id": "previous", "status": "completed", "targets": [], "prepared": []}
    if not legacy:
        value["started_at"] = datetime.datetime.fromtimestamp(now - elapsed, datetime.UTC).isoformat()
    session_review.write(path, value)
    os.utime(path, (now - elapsed if legacy else now, now - elapsed if legacy else now))


@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
@pytest.mark.parametrize("codex", [False, True])
def test_public_hook_defers_open_consultations(tmp_path, monkeypatch, event, codex):
    now = 1793400000.0
    _previous(legacy=False, now=now, elapsed=21601)
    _pending("ordinary")
    living = {"first", "second"}
    for session in living:
        session_state.update_state(session, lambda state: {**state, "add_awi_skill_invoked": True})
    monkeypatch.setattr(session_activity, "living_root_session_ids", lambda **kwargs: frozenset(living))
    monkeypatch.setattr(session_activity, "host_process", object)
    monkeypatch.setattr("time.time", lambda: now)
    monkeypatch.setattr("agent_toolkit._atk.wi.repo.resolve_repo_id_or_raise", lambda *a, **k: None)
    if codex:
        monkeypatch.setenv("CODEX_THREAD_ID", "ordinary")
    calls = _capture_batch_launches(monkeypatch)
    for closing in ("first", "second", None):
        payload = {"hook_event_name": event, "source": "startup", "session_id": "ordinary", "cwd": str(tmp_path)}
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
        assert hook.main(["rules_context" if event == "SessionStart" else "session_end_cleanup"]) == 0
        assert len(calls) == (0 if living else 1)
        assert any(target["session_id"] == "ordinary" for target in session_review.pending())
        if closing is not None:
            living.remove(closing)


@pytest.mark.parametrize("manual", [False, True])
def test_public_cli_rechecks_open_consultation(monkeypatch, manual):
    _pending("ordinary")
    session_state.update_state("consultation", lambda state: {**state, "add_awi_skill_invoked": True})
    monkeypatch.setattr(session_activity, "living_root_session_ids", lambda **kwargs: frozenset({"consultation"}))
    monkeypatch.setattr(session_activity, "review_activity", lambda *args, **kwargs: types.SimpleNamespace(has_activity=False))
    if not manual:
        launch_id = "a" * 32
        monkeypatch.setenv(session_review.LAUNCH_ENV, launch_id)
        session_review.write(session_review.directory() / "launches" / f"{launch_id}.json", {"status": "starting"})

    def prepare(*args, **kwargs):
        raise LookupError("準備まで到達")

    monkeypatch.setattr("agent_toolkit._atk.run_skill_batch._prepare", prepare)
    if manual:
        with pytest.raises(LookupError, match="準備まで到達"):
            atk.main(["run-skill", "--args", "batch", "agent-toolkit:review-session"])
    else:
        with pytest.raises(SystemExit) as result:
            atk.main(["run-skill", "--args", "batch", "agent-toolkit:review-session"])
        assert result.value.code == 0
        assert not list((session_review.directory() / "runs").glob("*.json"))
    assert session_review.pending()


@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
@pytest.mark.parametrize("elapsed", [21599, 21600])
@pytest.mark.parametrize("legacy", [False, True])
def test_public_hook_checks_six_hour_boundary(tmp_path, monkeypatch, event, elapsed, legacy):
    now = 1793400000.0
    _previous(legacy=legacy, now=now, elapsed=elapsed)
    _pending("ordinary")
    monkeypatch.setattr(session_activity, "host_process", object)
    monkeypatch.setattr("time.time", lambda: now)
    calls = _capture_batch_launches(monkeypatch)
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
    calls = _capture_batch_launches(monkeypatch)
    monkeypatch.setattr(session_activity, "host_process", object)
    payload = {
        "hook_event_name": "SessionStart",
        "source": "startup",
        "session_id": "current",
        "cwd": str(tmp_path),
        "transcript_path": str(tmp_path / "current.jsonl"),
    }
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
            session_review.write(path, {**session_review.read(path), "failure_unreported": True, "failure_key": "failure"})
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
        del manifest, kwargs
        failures.append(reason)
        writer = session_review.BatchWriter(path)
        writer.fail("failure", reason)
        writer.report_failure(synced=True)

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
    executed = []
    failures = []

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
        requests = _capture_batch_launches(context)
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
        del manifest, kwargs
        failures.append(reason)
        writer = session_review.BatchWriter(path)
        writer.fail("failure", reason)
        writer.report_failure(synced=True)

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
    spawned = _capture_batch_launches(monkeypatch)
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
