"""finishの公開操作が、batch所有者の条件を満たす場合だけ分析を記録する。"""

import pathlib

import pytest
import session_review_state

from agent_toolkit._common import session_review


@pytest.mark.parametrize("condition", ["empty", "unconfirmed", "completed", "resolved", "running", "failed"])
def test_finish_validates_batch_without_committing_targets(tmp_path, monkeypatch, condition):
    """報告同期前のfinishはpendingを変更せず、確定済みのbatchへ遅延書込しない。"""
    session_review.record({"hook_event_name": "SessionStart", "session_id": "one"}, host="codex")
    target = session_review.pending()[0]
    path = tmp_path / "batch.json"
    writer = session_review.BatchWriter(path)
    writer.create({"run_id": "batch", "targets": [target], "prepared": []})
    if condition != "empty":
        writer.prepare({"unconfirmed_scope": condition == "unconfirmed"})
    if condition == "failed":
        writer.fail("failure", "中断した")
    elif condition in {"completed", "resolved"}:
        value = session_review.read(path)
        value["status"] = condition
        session_review.write(path, value)
    before = path.read_bytes()
    monkeypatch.setenv(session_review.BATCH_ENV, str(path))
    succeeded = condition in {"running", "failed"}
    assert session_review_state.main(["finish"]) == (0 if succeeded else 2)
    assert session_review.pending() == [target]
    if succeeded:
        assert session_review.read(path)["analysis_succeeded"] is True
        assert session_review.read(path)["status"] == condition
    else:
        assert path.read_bytes() == before


def test_failure_report_does_not_mark_new_generation(tmp_path: pathlib.Path) -> None:
    """旧世代の遅延した失敗同期を、新しいSessionStartへ適用しない。"""
    payload = {"hook_event_name": "SessionStart", "session_id": "one"}
    session_review.record(payload, host="codex")
    old = session_review.pending()[0]
    writer = session_review.BatchWriter(tmp_path / "batch.json")
    writer.create({"run_id": "batch", "targets": [old], "prepared": []})
    writer.fail("failure", "中断した")
    session_review.record(payload, host="codex")
    current = session_review.pending()[0]
    assert current["generation"] != old["generation"]
    writer.report_failure(synced=False)
    assert session_review.pending() == [current]
    assert session_review.read(writer.path)["report_synced"] is False


@pytest.mark.parametrize("status", ["failed", "unknown"])
def test_finish_rejects_invalid_saved_state(tmp_path, monkeypatch, status):
    path = tmp_path / "invalid.json"
    session_review.write(path, {"run_id": "invalid", "status": status, "targets": [], "prepared": [{}]})
    before = path.read_bytes()
    monkeypatch.setenv(session_review.BATCH_ENV, str(path))
    assert session_review_state.main(["finish"]) == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize("analyzed,synced", [(False, True), (True, False)])
def test_batch_commit_requires_analysis_and_report_sync(tmp_path, analyzed, synced):
    """成功条件の片方だけでは、準備範囲と待ち世代を確定しない。"""
    session_review.record({"hook_event_name": "SessionStart", "session_id": "one"}, host="codex")
    target = session_review.pending()[0]
    writer = session_review.BatchWriter(tmp_path / "batch.json")
    writer.create({"run_id": "batch", "targets": [target], "prepared": [{}]})
    if analyzed:
        writer.analyze()
    before = writer.path.read_bytes()
    with pytest.raises(ValueError, match="analysis-incomplete|report-unsynced"):
        writer.complete(report_synced=synced)
    assert writer.path.read_bytes() == before
    assert session_review.pending() == [target]
    assert session_review.previous_scope(target) is None
