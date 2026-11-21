"""欠測のあるbatchの準備から報告・世代確定までの境界を検証する。"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib

import pytest

from agent_toolkit._atk import run_script, run_skill_batch
from agent_toolkit._atk.wi import add, layout
from agent_toolkit._common import session_activity, session_review
from agent_toolkit._testing import git_repository, session_evidence_support


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


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(
    1.0,
    reason=("実Gitの状態・履歴を使う結合境界。修正前Linux4並列全件計測の所有CPU最大0.505秒。"),
)
def test_partial_batch_finishes_with_gaps_and_reclaims_relocated_bundle(tmp_path, monkeypatch):
    """実ログで観測したNUL335/2157バイトと不成立な末尾を最小化し、健全対象も同じ回で処理する。"""
    # 活動は合成した記録から判定し、実機のプロセスは入力に含めない。
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
            # NUL境界の後ろの完全JSONだけを採用し、途切れた前側は失われた断片として残す。
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

    def execute(args):
        # モデルだけを代用し、準備・報告保存・finish・同期検収・範囲確定は実装を通す。
        manifest = session_review.read(pathlib.Path(args.batch_manifest))
        manifests.append(manifest)
        assert len(manifest["prepared"]) == 2
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
        monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", args.run_id)
        monkeypatch.setenv(session_review.BATCH_ENV, args.batch_manifest)
        add.add_entries(
            notes,
            messages=[
                "---\nrun_skill_report: true\n---\n# 振り返り結果\n\n部分分析。欠測2行、失われた断片1件、網羅性は保証外。\n"
                + json.dumps([*gaps, *partial["recovered_record_lines"]], ensure_ascii=False)
            ],
            target_repo=args.target_repo,
            source="run-skill",
            now=datetime.datetime.now(datetime.UTC),
            entry_type="uwi",
            scope=session_review.BATCH_SCOPE,
            question_type="post-approval",
        )
        assert run_script.dispatch(argparse.Namespace(script_name="session-review-state", script_args=["finish"])) == 0
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
        assert (bundle / "unconfirmed.jsonl").exists() and (bundle / "recovered.jsonl").exists()
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


@pytest.mark.parametrize("change", ["none", "append", "generation", "prefix", "older"])
def test_partial_completion_preserves_new_input(tmp_path, change):
    """モデル/実Gitなしで、末尾欠測の消費と新入力・接頭辞変更の保護を区別する。"""
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
