from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import types

import pytest

from agent_toolkit._common import session_activity, session_family, session_review, session_state


def test_auto_start_waits_until_last_consultation_closes(monkeypatch):
    for session in ("first", "second", "closed"):
        session_state.update_state(session, lambda state: {**state, "add_awi_skill_invoked": True})
    for living in ({"first", "second"}, {"second"}, set()):
        monkeypatch.setattr(session_activity, "living_root_session_ids", lambda living=living, **kwargs: frozenset(living))
        assert session_review.auto_start_due(now=1793400000.0) is (not living)


def test_auto_start_reads_only_runs_updated_within_the_interval(monkeypatch):
    monkeypatch.setattr(session_activity, "living_root_session_ids", lambda **kwargs: frozenset())
    runs = session_review.directory() / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    old = runs / "old.json"
    old.write_text("{", encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 7 * 60 * 60, now - 7 * 60 * 60))
    assert session_review.auto_start_due(now=now) is True
    session_review.write(
        runs / "recent.json", {"started_at": datetime.datetime.fromtimestamp(now - 60, datetime.UTC).isoformat()}
    )
    assert session_review.auto_start_due(now=now) is False


@pytest.mark.parametrize("changed,unconfirmed", [(False, False), (True, False), (False, True)])
def test_cleanup_prepared_preserves_scope_and_referenced_evidence(tmp_path, changed, unconfirmed) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    disposable = bundle / "timeline.jsonl"
    disposable.write_bytes(b"original")
    for name in ("scope.json", "review-index.jsonl", "candidate-evidence.jsonl"):
        (bundle / name).write_bytes(b"evidence")
    prepared = {
        "scope_path": str(bundle / "scope.json"),
        "unconfirmed_scope": ["missing"] if unconfirmed else [],
        "disposable_bundle_files": {"timeline.jsonl": hashlib.sha256(b"original").hexdigest()},
    }
    if changed:
        disposable.write_bytes(b"new generation")
    session_review.cleanup_prepared(prepared)
    session_review.cleanup_prepared(prepared)
    assert disposable.exists() is (changed or unconfirmed)
    assert all((bundle / name).exists() for name in ("scope.json", "review-index.jsonl", "candidate-evidence.jsonl"))


def test_batch_completion_reclaims_only_after_report_sync(tmp_path, capsys, monkeypatch) -> None:
    """prepareから報告同期の成功保存まで通し、失敗時保持と成功時回収を観測する。"""
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, "要求")
    prepared = _prepare(transcript, capsys)
    writer = session_review.BatchWriter(session_review.directory() / "runs" / "cleanup.json")
    writer.create({"run_id": "cleanup", "targets": []})
    writer.prepare(prepared)
    writer.analyze()
    timeline = pathlib.Path(prepared["scope_path"]).parent / "timeline.jsonl"
    with pytest.raises(ValueError, match="report-unsynced"):
        writer.complete(report_synced=False)
    assert timeline.exists()
    resource = timeline.parent / "resource-usage.jsonl"
    unlink = pathlib.Path.unlink

    def fail_resource(path, *args, **kwargs):
        if path == resource:
            raise PermissionError("使用中")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(pathlib.Path, "unlink", fail_resource)
        assert writer.complete(report_synced=True)["status"] == "completed"
    assert session_review.read(writer.path)["status"] == "completed"
    assert not timeline.exists()
    assert pathlib.Path(prepared["scope_path"]).exists()
    assert pathlib.Path(prepared["review_index_path"]).exists()
    assert resource.exists()
    session_review.sweep(now=time.time())
    assert not resource.exists()


def test_legacy_bundle_cleanup_and_individual_failure(tmp_path, capsys, monkeypatch, caplog) -> None:
    """旧manifestの成功scopeを再確認し、個別削除失敗でも他の抽出物を回収して再試行する。"""
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, "要求")
    prepared = _prepare(transcript, capsys)
    session_review.complete(prepared)
    prepared.pop("disposable_bundle_files")
    bundle = pathlib.Path(prepared["scope_path"]).parent
    unlink = pathlib.Path.unlink

    def fail_one(path, *args, **kwargs):
        if path == bundle / "resource-usage.jsonl":
            raise PermissionError("使用中")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(pathlib.Path, "unlink", fail_one)
        assert not session_review.cleanup_prepared(prepared, completed_before=time.time() + 1)
    assert "削除権限を確認" in caplog.text
    assert "atk run-skill --args batch agent-toolkit:review-session" in caplog.text
    assert (bundle / "resource-usage.jsonl").exists()
    assert not (bundle / "comparison-materials.jsonl").exists()
    assert session_review.cleanup_prepared(prepared, completed_before=time.time() + 1)
    assert not (bundle / "resource-usage.jsonl").exists()


def test_unconfirmed_legacy_bundle_retention_does_not_warn(tmp_path, capsys, caplog) -> None:
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, "要求")
    prepared = _prepare(transcript, capsys)
    prepared.pop("disposable_bundle_files")
    assert not session_review.cleanup_prepared(prepared)
    assert (pathlib.Path(prepared["scope_path"]).parent / "timeline.jsonl").exists()
    assert not [record for record in caplog.records if record.levelname == "WARNING"]


@pytest.mark.parametrize("status", ["starting", "accepted", "settled", "notified"])
def test_launch_sweep_preserves_unreported_attempts(status: str) -> None:
    path = session_review.directory() / "launches" / ("a" * 32 + ".json")
    value = {"status": status}
    if status == "accepted":
        value["manifest_path"] = "run.json"
    session_review.write(path, value)
    path.with_suffix(".log").write_text("診断", encoding="utf-8")
    session_review.sweep(now=time.time() + 31 * 24 * 60 * 60)
    assert path.exists() is (status == "starting")
    assert path.with_suffix(".log").exists() is (status == "starting")


def test_launch_receipt_transfers_failure_ownership(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launch_id = "a" * 32
    path = session_review.directory() / "launches" / f"{launch_id}.json"
    session_review.write(path, {"status": "starting", "exited": True})
    monkeypatch.setenv(session_review.LAUNCH_ENV, launch_id)
    manifest = tmp_path / "run.json"
    session_review.launch_result(manifest=manifest)
    assert session_review.read(path)["manifest_path"] == str(manifest)
    session_review.launch_result()
    assert session_review.read(path)["status"] == "accepted"
    assert session_review.read(path)["manifest_path"] == str(manifest)
    assert session_review.launch_notice("claude") is None


@pytest.mark.parametrize("windows", [False, True])
def test_launch_owner_interruption_is_reported_once(monkeypatch: pytest.MonkeyPatch, windows: bool) -> None:
    path = session_review.directory() / "launches" / ("a" * 32 + ".json")
    session_review.write(
        path,
        {
            "status": "starting",
            "owner": {"pid": 1, "create_time": 0},
            "host": "codex",
            "event": "SessionEnd",
            "log_path": "診断.log",
        },
    )
    monkeypatch.setattr(session_review.session_registry.ProcessIdentity, "exists", lambda _self: False)
    monkeypatch.setattr(session_review, "os", types.SimpleNamespace(name="nt" if windows else "posix"))
    notice = session_review.launch_notice("codex") or ""
    assert "受付前に終了" in notice
    assert "診断.log" in notice
    assert ("Get-Content -LiteralPath" if windows else "cat --") in notice
    assert session_review.launch_notice("codex") is None


def _transcript(path: pathlib.Path, text: str, *, append: bool = False) -> None:
    with path.open("a" if append else "w", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {"type": "user", "timestamp": "2026-10-28T01:00:00Z", "message": {"role": "user", "content": text}},
                ensure_ascii=False,
            )
            + "\n"
        )


def _dispatch(script_name: str, arguments: list[str]) -> int:
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).parents[1] / "atk.py"), "run-script", script_name, "--", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def _prepare(path: pathlib.Path, capsys, *, after: pathlib.Path | None = None) -> dict:
    arguments = ["--transcript", str(path)]
    if after is not None:
        arguments.extend(["--after", str(after)])
    assert _dispatch("session-review-prepare", arguments) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_record_only_top_level_and_keep_identity(host, tmp_path, monkeypatch) -> None:
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "current-id",
        "transcript_path": str(tmp_path / ("current-id.jsonl" if host == "claude" else "old-id.jsonl")),
        "cwd": str(tmp_path),
    }
    assert session_review.record(payload, host=host)
    assert session_review.pending()[0]["session_id"] == "current-id"
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "parent")
    assert not session_review.record({**payload, "session_id": "delegate"}, host=host)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION")
    monkeypatch.setenv(session_review.BATCH_ENV, "manifest")
    batch_payload = {**payload, "session_id": "batch", "transcript_path": str(tmp_path / "batch.jsonl")}
    assert session_review.record(batch_payload, host=host)
    monkeypatch.delenv(session_review.BATCH_ENV)
    assert session_review.record(batch_payload, host=host)
    assert next(item for item in session_review.pending() if item["session_id"] == "batch")["review_batch"]
    assert len(session_review.pending()) == 2


@pytest.mark.parametrize("transcript", [None, "", 123, "digest-benchmark.jsonl"])
def test_claude_record_requires_preparable_transcript(transcript: object) -> None:
    """記録先の無いClaude入力を待ちへ追加せず、準備不能な再試行を防ぐ。"""
    assert not session_review.record(
        {"hook_event_name": "SessionStart", "session_id": "session-1", "transcript_path": transcript}, host="claude"
    )
    assert not session_review.pending()


@pytest.mark.parametrize("change", ["append", "resume", "new-input", "none"])
def test_prepare_complete_preserves_new_scope(change, tmp_path, capsys, monkeypatch) -> None:
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, "初回要求")
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "session",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
    }
    monkeypatch.setenv(session_review.BATCH_ENV, "manifest")
    session_review.record(payload, host="claude")
    monkeypatch.delenv(session_review.BATCH_ENV)
    prepared = _prepare(transcript, capsys)
    if change == "append":
        _transcript(transcript, "準備中の追記", append=True)
    elif change == "resume":
        session_review.record(payload, host="claude")
    session_review.complete(prepared)
    if change == "new-input":
        session_review.record({**payload, "hook_event_name": "UserPromptSubmit"}, host="claude")
    assert bool(session_review.pending()) == (change != "none")
    assert all(target["review_batch"] for target in session_review.pending())
    previous = session_review.previous_scope({"host": "claude", "session_id": "session"})
    assert previous is not None
    _transcript(transcript, "次の要求", append=True)
    next_prepared = _prepare(transcript, capsys, after=previous)
    body = pathlib.Path(next_prepared["conversation_path"]).read_text(encoding="utf-8")
    assert "次の要求" in body
    assert "初回要求" not in body


def test_state_script_completes_scope_and_rejects_missing_input(tmp_path, capsys) -> None:
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, "要求")
    session_review.record(
        {"hook_event_name": "SessionStart", "session_id": "session", "transcript_path": str(transcript)}, host="claude"
    )
    prepared = _prepare(transcript, capsys)
    result = tmp_path / "prepared.json"
    result.write_text(json.dumps(prepared), encoding="utf-8")
    assert _dispatch("session-review-state", ["complete", "--prepared", str(result)]) == 0
    assert json.loads(capsys.readouterr().out)["pending_count"] == 0
    assert _dispatch("session-review-state", ["complete", "--prepared", str(tmp_path / "missing.json")]) == 2


def test_after_rejects_replaced_history(tmp_path, capsys) -> None:
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, "元の要求")
    prepared = _prepare(transcript, capsys)
    _transcript(transcript, "書き換えた要求")
    assert _dispatch("session-review-prepare", ["--transcript", str(transcript), "--after", prepared["scope_path"]]) == 2
    assert "前回処理済み範囲" in capsys.readouterr().err


def test_increment_keeps_tool_input_for_result_after_checkpoint(tmp_path, capsys) -> None:
    """前回範囲にある入力と今回の失敗結果を対応付け、入力の操作内容を保持する。"""
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, "要求")
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call-one",
                                "name": "Bash",
                                "input": {"command": "test-boundary-command"},
                            }
                        ],
                    },
                }
            )
            + "\n"
        )
    prepared = _prepare(transcript, capsys)
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": "call-one", "is_error": True, "content": "boundary failure"}
                        ],
                    },
                }
            )
            + "\n"
        )
    result = _prepare(transcript, capsys, after=pathlib.Path(prepared["scope_path"]))
    timeline = pathlib.Path(result["scope_path"]).parent / "timeline.jsonl"
    failure = next(
        json.loads(line)
        for line in timeline.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("kind") == "failed-tool"
    )
    assert "test-boundary-command" in failure["operation"]
    assert failure["line"] == 3


def test_sweep_preserves_pending_and_unfinished_references(tmp_path) -> None:
    root = session_review.directory()
    target_key = session_review.key("claude", "gone")
    missing = tmp_path / "gone.jsonl"
    reviewed = root / "reviewed" / f"{target_key}.json"
    session_review.write(reviewed, {"records": {"main": {"path": str(missing)}}})
    run = root / "runs" / "unfinished.json"
    log = tmp_path / "run.log"
    session_review.write(
        run, {"run_id": "unfinished", "status": "failed", "targets": [{"key": target_key}], "log_path": str(log)}
    )
    now = time.time()
    os.utime(run, (now - 40 * 86400, now - 40 * 86400))
    session_review.sweep(now=now)
    assert reviewed.exists() and run.exists()
    assert log in session_review.protected_logs()
    value = session_review.read(run)
    value["status"] = "completed"
    session_review.write(run, value)
    os.utime(run, (now - 40 * 86400, now - 40 * 86400))
    session_review.sweep(now=now)
    assert not reviewed.exists() and not run.exists()


def test_complete_rejects_history_replaced_after_prepare(tmp_path, capsys) -> None:
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, "元の要求")
    session_review.record(
        {"hook_event_name": "SessionStart", "session_id": "session", "transcript_path": str(transcript)}, host="claude"
    )
    prepared = _prepare(transcript, capsys)
    _transcript(transcript, "置換した要求")
    with pytest.raises(ValueError, match="接頭辞"):
        session_review.complete(prepared)
    assert session_review.pending()


def test_reported_failure_becomes_collectable_only_after_pending_is_cleared(tmp_path) -> None:
    session_review.record(
        {"hook_event_name": "SessionStart", "session_id": "one", "transcript_path": str(tmp_path / "one.jsonl")},
        host="claude",
    )
    target = session_review.pending()[0]
    run = session_review.directory() / "runs" / "failed.json"
    log = tmp_path / "failed.log"
    session_review.write(
        run,
        {
            "run_id": "failed",
            "status": "failed",
            "targets": [target],
            "report_synced": True,
            "log_path": str(log),
            "failure_key": "failure",
            "error": "失敗",
        },
    )
    launch = session_review.directory() / "launch.log"
    launch.write_text("起動診断", encoding="utf-8")
    session_review.sweep(now=time.time())
    assert session_review.read(run)["status"] == "failed"
    assert log in session_review.protected_logs()
    (session_review.directory() / "pending" / f"{target['key']}.json").unlink()
    session_review.sweep(now=time.time())
    assert session_review.read(run)["status"] == "resolved"
    assert log not in session_review.protected_logs()
    session_review.sweep(now=time.time() + 40 * 86400)
    assert not run.exists() and not launch.exists()


def test_corrupt_manifest_preserves_new_and_legacy_review_logs():
    from agent_toolkit._common import state_paths  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    log_dir = state_paths.state_dir() / "run-skill"
    log_dir.mkdir(parents=True)
    old = log_dir / "old-session-review-batch.log"
    new = log_dir / "new-session-review_batch.log"
    renamed = log_dir / "new-review-session_batch.log"
    unrelated = log_dir / "other.log"
    for path in (old, new, renamed, unrelated):
        path.write_text("記録", encoding="utf-8")
    manifest = session_review.directory() / "runs" / "old.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{破損", encoding="utf-8")
    assert session_review.protected_logs() == {old, new, renamed}


def test_complete_checks_full_record_even_when_analysis_excludes_fork_context(tmp_path):
    transcript = tmp_path / "fork.jsonl"
    lines = ['{"type":"session_meta"}', '{"type":"event_msg","payload":{"type":"user_message","message":"継承"}}']
    lines.append('{"type":"event_msg","payload":{"type":"user_message","message":"分岐後"}}')
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    loaded = session_family._load_records_checked(str(transcript))  # pylint: disable=protected-access
    assert loaded.records is not None
    meta, inherited, branch = loaded.records
    collected = types.SimpleNamespace(
        records=[meta, branch], fork_context=(inherited,), record_id="codex:fork", path=transcript
    )
    scope_path = tmp_path / "scope.json"
    session_review.write(scope_path, session_review.scope_records([collected]))
    assert session_review.record({"hook_event_name": "SessionStart", "session_id": "fork"}, host="codex")
    prepared = {**session_review.prepare_identity("codex", "fork"), "scope_path": str(scope_path)}
    assert session_review.complete(prepared)["pending_count"] == 0
    transcript.write_text(("\n".join(lines) + "\n").replace("継承", "置換"), encoding="utf-8")
    with pytest.raises(ValueError, match="接頭辞"):
        session_review.complete(prepared)
