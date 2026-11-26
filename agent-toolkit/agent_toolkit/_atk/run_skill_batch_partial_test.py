from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
from types import SimpleNamespace

import pytest

from agent_toolkit._atk import managed_temp, run_script, run_skill_batch
from agent_toolkit._atk.managed_temp import paths, sweep, sweep_lock
from agent_toolkit._atk.wi import add, layout
from agent_toolkit._common import session_activity, session_review
from agent_toolkit._testing import git_repository, session_evidence_support
from agent_toolkit._testing.run_skill_support import record_completed_batch_analysis


def _register(path: pathlib.Path) -> dict:
    target = {
        "host": "claude",
        "session_id": path.stem,
        "key": session_review.key("claude", path.stem),
        "generation": "prepared-generation",
        "transcript_path": str(path),
        "cwd": str(path.parent),
        "target_repo": None,
    }
    with session_review.locked():
        session_review.TargetGeneration("claude", path.stem).register(target)
    return target


def _save_report_and_finish(notes, monkeypatch, args, body):
    monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", args.run_id)
    monkeypatch.setenv(session_review.BATCH_ENV, args.batch_manifest)
    add.add_entries(
        notes,
        messages=[f"---\nrun_skill_report: true\n---\n# 振り返り結果\n\n{body}"],
        target_repo=args.target_repo,
        source="run-skill",
        now=datetime.datetime.now(datetime.UTC),
        entry_type="uwi",
        scope=session_review.BATCH_SCOPE,
        question_type="post-approval",
    )
    record_completed_batch_analysis(args.batch_manifest)
    assert run_script.dispatch(argparse.Namespace(script_name="session-review-state", script_args=["finish"])) == 0


def _assert_sweeps_keep(monkeypatch, work_dirs: set[pathlib.Path], now: datetime.datetime) -> None:
    with monkeypatch.context() as pressure:
        pressure.setenv(sweep.MIN_FREE_RATIO_ENV, "0.1")
        pressure.setattr(sweep, "_disk_usage", lambda _path: SimpleNamespace(total=10_000, free=5000, used=5000))
        swept = managed_temp.sweep_managed_temp(now=now, force=True)
        assert not swept.failed and work_dirs.isdisjoint(swept.deleted)
        pressure.setattr(sweep, "_disk_usage", lambda _path: SimpleNamespace(total=10_000, free=0, used=10_000))
        swept = managed_temp.sweep_managed_temp(now=now)
    assert swept.pressure is not None and work_dirs.isdisjoint(swept.deleted)


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(
    1.0,
    reason=("実Gitの状態・履歴を使う結合境界。修正前Linux4並列全件計測の所有CPU最大0.505秒。"),
)
def test_partial_batch_finishes_with_gaps_and_reclaims_relocated_bundle(tmp_path, monkeypatch):

    monkeypatch.setattr(session_activity, "host_processes", lambda: ())
    notes = git_repository.init_repository(pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"]))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    parent = tmp_path / "partial-parent.jsonl"
    rows = [session_evidence_support.timestamped_entry("2026-11-11T18:00:00Z", "主担当の要求")]
    originals = {}
    for index, padding in enumerate((335, 2157), 1):
        child = f"11111111-1111-4111-8111-{index:012d}"
        rows.extend(
            [
                session_evidence_support.codex_tool_use_entry("2026-11-11T18:00:01Z", f"call-{index}", child),
                session_evidence_support.codex_tool_result_entry("2026-11-11T18:00:02Z", f"call-{index}", child),
            ]
        )
        path = pathlib.Path(os.environ["CODEX_HOME"]) / "sessions/2026/11/11" / f"rollout-2026-11-11T18-00-00-{child}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        valid = [
            {"type": "session_meta", "payload": {"id": child}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "欠測前"}},
        ]
        original = b"".join(json.dumps(row).encode() + b"\n" for row in valid) + b"\0" * padding + b"1suffix\n"
        if index == 1:
            original += json.dumps(valid[-1]).encode() + b"\n"

            original += b'{"type":"event_msg","payload":{"message":"cut\0\0' + json.dumps(valid[-1]).encode() + b"\n"
        path.write_bytes(original)
        originals[path] = original
    session_evidence_support.write_jsonl(parent, rows)
    healthy = tmp_path / "healthy.jsonl"
    session_evidence_support.write_jsonl(healthy, [rows[0]])
    for path in (parent, healthy):
        _register(path)
    manifests = []
    monkeypatch.delenv(session_review.LAUNCH_ENV, raising=False)
    sweep_time = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=8)

    def execute(args):
        # モデルだけを代用し、準備・報告保存・finish・同期検収・範囲確定は実装を通す。
        manifest = session_review.read(pathlib.Path(args.batch_manifest))
        manifests.append(manifest)
        assert len(manifest["prepared"]) == 2
        work_dirs = {pathlib.Path(item["work_dir"]) for item in manifest["prepared"]}
        assert all(path.parent == managed_temp.temp_root_path() for path in work_dirs)
        run_skill_batch.record_executor(args, os.getpid())
        for work_dir in work_dirs:
            (work_dir / managed_temp.OWNER_FILE).unlink()
        _assert_sweeps_keep(monkeypatch, work_dirs, sweep_time)
        assert all((work_dir / "conversation.md").is_file() for work_dir in work_dirs)
        partial = next(item for item in manifest["prepared"] if item["session_id"] == parent.stem)
        assert partial["partial_analysis"] is True
        gaps = partial["unconfirmed_scope"]
        assert len(gaps) == 2 and all(item["kind"] == "record-gap" and item["line"] == 3 for item in gaps)
        assert {item["record"] for item in gaps} == {f"codex:11111111-1111-4111-8111-{index:012d}" for index in (1, 2)}
        assert [(item["line"], item["method"]) for item in partial["recovered_record_lines"]] == [(5, "joined-fragment-lost")]
        assert pathlib.Path(partial["bundle_scope_path"]) != pathlib.Path(partial["scope_path"])
        with pytest.raises(ValueError, match="未確認範囲"):
            session_review.complete(partial)
        writer = session_review.BatchWriter(pathlib.Path(args.batch_manifest))
        with pytest.raises(ValueError, match="report-unsynced"):
            writer.complete(report_synced=False)
        _save_report_and_finish(
            notes,
            monkeypatch,
            args,
            "部分分析。欠測2行、失われた断片1件、網羅性は保証外。\n"
            + json.dumps([*gaps, *partial["recovered_record_lines"]], ensure_ascii=False),
        )
        return 0

    args = argparse.Namespace(skill="agent-toolkit:review-session", args="batch", timeout=60, target_repo=None, work_dir=None)
    assert run_skill_batch.dispatch(args, execute) == 0
    assert len(manifests) == 1 and not session_review.pending()
    manifest = session_review.read(pathlib.Path(manifests[0]["manifest_path"]))
    assert manifest["status"] == "completed" and manifest["analysis_succeeded"]
    for prepared in manifest["prepared"]:
        bundle = pathlib.Path(prepared["bundle_scope_path"]).parent
        assert not (bundle / "timeline.jsonl").exists()
        assert pathlib.Path(prepared["scope_path"]).exists()
        assert (bundle / "candidate-evidence.jsonl").exists()
        assert (bundle / "unconfirmed.jsonl").exists() == bool(prepared["unconfirmed_scope"])
        assert (bundle / "recovered.jsonl").exists() == bool(prepared["recovered_record_lines"])
    reviewed_path = session_review.previous_scope({"host": "claude", "session_id": parent.stem})
    assert reviewed_path is not None
    reviewed = session_review.read(reviewed_path)
    assert reviewed["partial_analysis"] and len(reviewed["unconfirmed_scope"]) == 2
    tail = reviewed["records"]["codex:11111111-1111-4111-8111-000000000002"]
    assert tail["through_line"] == 2 and tail["observed_through_line"] == 3
    reports = list(layout.iter_entry_paths(notes))
    assert len(reports) == 1
    report = reports[0].read_text(encoding="utf-8")
    assert "部分分析" in report and all(item["record"] in report for item in reviewed["unconfirmed_scope"])
    assert all(path.read_bytes() == content for path, content in originals.items())
    work_dirs = {pathlib.Path(item["work_dir"]) for item in manifest["prepared"]}
    for work_dir in work_dirs:
        (work_dir / "bundle" / managed_temp.OWNER_FILE).unlink()
    swept = managed_temp.sweep_managed_temp(now=sweep_time, force=True)
    assert not swept.failed and work_dirs.issubset(swept.deleted)
    assert all(pathlib.Path(item["scope_path"]).is_file() for item in manifest["prepared"])


def test_resumed_legacy_work_dir_remains_visible_to_sweep_while_executor_lives(monkeypatch):
    session_id = "old-batch-target"
    root = managed_temp.resolve_session_temp("original-transcript-stem")
    work_dir = managed_temp.create_session_temp("session-review", root)
    bundle = work_dir / "bundle"
    bundle.mkdir()
    (bundle / "scope.json").write_text("{}\n", encoding="utf-8")
    path = session_review.directory() / "runs" / ("b" * 32 + ".json")
    session_review.BatchWriter(path).create(
        {
            "run_id": path.stem,
            "manifest_path": str(path),
            "status": "running",
            "targets": [],
            "prepared": [{"session_id": session_id, "work_dir": str(work_dir)}],
        }
    )
    run_skill_batch.record_executor(argparse.Namespace(batch_manifest=str(path)), os.getpid())
    now = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=8)
    _assert_sweeps_keep(monkeypatch, {root}, now)
    assert (bundle / "scope.json").is_file()


def test_resume_does_not_consume_prepared_files_during_sweep(monkeypatch):
    work_dir = managed_temp.create_managed_temp("review-batch")
    bundle = work_dir / "bundle"
    bundle.mkdir()
    scope = bundle / "scope.json"
    scope.write_text("{}\n", encoding="utf-8")
    path = session_review.directory() / "runs" / ("b" * 32 + ".json")
    writer = session_review.BatchWriter(path)
    writer.create(
        {
            "run_id": path.stem,
            "manifest_path": str(path),
            "status": "running",
            "targets": [],
            "prepared": [{"session_id": "source", "work_dir": str(work_dir)}],
        }
    )
    args = argparse.Namespace(
        run_id=path.stem, batch_manifest=str(path), timeout=1, resume_record={}, skill="review-session", args=[]
    )
    monkeypatch.delenv(session_review.LAUNCH_ENV, raising=False)
    monkeypatch.setattr(run_skill_batch, "_finish_batch", lambda _manifest, _path: 0)
    calls = []

    def execute(_args):
        calls.append(scope.read_text(encoding="utf-8"))
        return 0

    with sweep_lock.sweep_access(paths.temp_root(), paths.state_root(create=False)) as locked:
        assert locked and run_skill_batch.dispatch(args, execute) == 1
        assert not calls
    assert run_skill_batch.dispatch(args, execute) == 0
    assert calls == ["{}\n"]


@pytest.mark.parametrize("change", ["none", "append", "generation", "prefix", "older"])
def test_partial_completion_preserves_new_input(tmp_path, change):
    path = tmp_path / "partial.jsonl"
    row = json.dumps({"type": "user", "message": {"role": "user", "content": "要求"}}, ensure_ascii=False)
    path.write_text(row + "\n\0\0broken\n", encoding="utf-8")
    target = _register(path)
    scope = tmp_path / "scope.json"
    session_review.write(
        scope,
        {
            "version": 1,
            "records": {
                "claude:partial": {
                    "path": str(path),
                    "through_line": 1,
                    "observed_through_line": 2,
                    "digest": hashlib.sha256(row.encode()).hexdigest(),
                }
            },
            "partial_analysis": True,
            "unconfirmed_scope": [{"kind": "record-gap", "record": "claude:partial", "line": 2}],
        },
    )
    prepared = {**target, "scope_path": str(scope), "partial_analysis": True, "unconfirmed_scope": ["gap"]}
    if change == "generation":
        with session_review.locked():
            session_review.TargetGeneration("claude", path.stem).register({**target, "generation": "new"})
    elif change == "append":
        path.write_text(path.read_text(encoding="utf-8") + row + "\n", encoding="utf-8")
    elif change == "prefix":
        path.write_text(row.replace("要求", "変更") + "\n\0\0broken\n", encoding="utf-8")
    elif change == "older":
        path.write_text(path.read_text(encoding="utf-8") + "\0more-missing\n", encoding="utf-8")
        newer = session_review.read(scope)
        newer["records"]["claude:partial"]["observed_through_line"] = 3
        newer["unconfirmed_scope"].append({"kind": "record-gap", "record": "claude:partial", "line": 3})
        with session_review.locked():
            state = session_review.TargetGeneration("claude", path.stem)
            state.register({**target, "generation": "new"})
            state.complete(newer, "new", appended=True)
    writer = session_review.BatchWriter(session_review.directory() / "runs" / "partial.json")
    writer.create({"run_id": "partial", "targets": [target], "prepared": [prepared]})
    writer.analyze()
    if change == "prefix":
        with pytest.raises(ValueError, match="接頭辞が変化"):
            writer.complete(report_synced=True)
    else:
        assert writer.complete(report_synced=True)["status"] == "completed"
    assert bool(session_review.pending()) is (change != "none")
    if change == "older":
        reviewed_path = session_review.previous_scope(target)
        assert reviewed_path is not None
        reviewed = session_review.read(reviewed_path)
        assert reviewed["records"]["claude:partial"]["observed_through_line"] == 3
        assert [event["line"] for event in reviewed["unconfirmed_scope"]] == [2, 3]


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(1.0, reason="実Gitで報告の同期とbatchの終了を通すため。初回Linux実測は0.572秒。")
def test_batch_finishes_and_saves_report_for_codex_fork(tmp_path, monkeypatch):
    notes = git_repository.init_repository(pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"]))
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    sessions = codex_home / "sessions/2026/11/19"
    sessions.mkdir(parents=True)
    parent = "11111111-1111-4111-8111-111111111111"
    fork = "22222222-2222-4222-8222-222222222222"

    def write(thread, rows):
        path = sessions / f"rollout-2026-11-19T03-33-37-{thread}.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path

    def user(message):
        return {"type": "event_msg", "payload": {"type": "user_message", "message": message}}

    inherited = user("分岐元の依頼")
    write(parent, [{"type": "session_meta", "payload": {"id": parent}}, inherited])
    transcript = write(
        fork,
        [{"type": "session_meta", "payload": {"id": fork, "forked_from_id": parent}}, inherited, user("分岐後の依頼")],
    )
    assert session_review.record(
        {"hook_event_name": "SessionStart", "session_id": fork, "transcript_path": str(transcript), "cwd": str(tmp_path)},
        host="codex",
        target_repo="https://example.com/team/repo",
    )
    calls = []

    def execute(args):
        calls.append(session_review.read(pathlib.Path(args.batch_manifest)))
        _save_report_and_finish(notes, monkeypatch, args, "分岐後の依頼を確認した。")
        return 0

    args = argparse.Namespace(skill="agent-toolkit:review-session", args="batch", timeout=60, target_repo=None, work_dir=None)
    assert run_skill_batch.dispatch(args, execute) == 0
    assert [[row["session_id"] for row in call["prepared"]] for call in calls] == [[fork]]
    assert not session_review.pending()
    saved = session_review.read(session_review.directory() / "reviewed" / f"codex-{fork}.json")
    assert saved["records"][f"codex:{fork}"]["through_line"] == 3
    assert len(list(layout.iter_entry_paths(notes))) == 1
