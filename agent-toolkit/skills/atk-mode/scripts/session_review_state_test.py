import pathlib

import filelock
import pytest
import session_review_state
from agent_toolkit._common import session_review, session_review_analysis


@pytest.mark.parametrize("condition", ["empty", "unconfirmed", "completed", "resolved", "running", "failed"])
def test_finish_validates_batch_without_committing_targets(tmp_path, monkeypatch, capsys, condition):
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
    analysis = session_review_analysis.ensure(writer)
    analysis.write_text(analysis.read_text(encoding="utf-8").replace(" | 未着手 | ", " | 完了 | "), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setenv(session_review.BATCH_ENV, str(path))
    monkeypatch.setattr(session_review_state.add_reports, "check_run_report_sync", lambda *args, **kwargs: None)
    succeeded = condition in {"running", "failed"}
    assert session_review_state.main(["finish"]) == (0 if succeeded else 2)
    assert session_review.pending() == [target]
    if succeeded:
        assert session_review.read(path)["analysis_succeeded"] is True
        assert session_review.read(path)["status"] == condition
    elif condition in {"completed", "resolved"}:
        assert path.read_bytes() == before
        assert "保存済みの実行記録で確定結果を確認する" in capsys.readouterr().err
    else:
        assert "準備と分析が終わってから" in capsys.readouterr().err


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


def test_finish_outside_batch_points_to_batch_invocation(monkeypatch, capsys):
    monkeypatch.delenv(session_review.BATCH_ENV, raising=False)
    assert session_review_state.main(["finish"]) == 2
    message = capsys.readouterr().err
    assert "atk run-skill agent-toolkit:review-session --args batch" in message
    assert "再実行" not in message


@pytest.mark.parametrize("error", [OSError("read failed"), filelock.Timeout("report.lock")])
def test_finish_report_read_failure_points_to_access_recovery(tmp_path, monkeypatch, capsys, error):
    writer = session_review.BatchWriter(tmp_path / "batch.json")
    writer.create({"run_id": "batch", "targets": [], "prepared": [{}]})
    session_review_analysis.ensure(writer)
    monkeypatch.setenv(session_review.BATCH_ENV, str(writer.path))

    def check(*args, **kwargs):
        raise error

    monkeypatch.setattr(session_review_state.add_reports, "check_run_report_sync", check)
    assert session_review_state.main(["finish"]) == 2
    message = capsys.readouterr().err
    assert type(error).__name__ in message
    assert "読み取り権限とロックの保持状況" in message
    assert not writer.load().analysis_succeeded


@pytest.mark.parametrize("missing", ["file", "row", "status"])
def test_finish_requires_the_analysis_record_and_completed_rows(tmp_path, monkeypatch, capsys, missing):
    session_review.record({"hook_event_name": "SessionStart", "session_id": "one"}, host="codex")
    writer = session_review.BatchWriter(tmp_path / "batch.json")
    writer.create({"run_id": "batch", "targets": session_review.pending(), "prepared": []})
    writer.prepare({"host": "codex", "session_id": "one", "partial_analysis": True, "unconfirmed_scope": ["欠測"]})
    monkeypatch.setenv(session_review.BATCH_ENV, str(writer.path))
    monkeypatch.setattr(session_review_state.add_reports, "check_run_report_sync", lambda *args, **kwargs: None)
    if missing != "file":
        analysis = session_review_analysis.ensure(writer)
        if missing == "row":
            text = analysis.read_text(encoding="utf-8")
            analysis.write_text(
                "\n".join(line for line in text.splitlines() if not line.startswith("| one |")), encoding="utf-8"
            )
    assert session_review_state.main(["finish"]) == 1
    assert not writer.load().analysis_succeeded
    assert ("prepare-analysis" if missing == "file" else "one") in capsys.readouterr().err
    assert session_review_state.main(["prepare-analysis"]) == 0


def test_analysis_template_preserves_existing_conclusions(tmp_path):
    session_review.record({"hook_event_name": "SessionStart", "session_id": "one"}, host="codex")
    writer = session_review.BatchWriter(tmp_path / "batch.json")
    writer.create({"run_id": "old-batch", "targets": session_review.pending(), "prepared": []})
    writer.prepare(
        {"host": "codex", "session_id": "one", "conversation_path": "/bundle/conversation", "partial_analysis": True}
    )
    path = session_review_analysis.ensure(writer)
    template = path.read_text(encoding="utf-8")
    assert "| one | 未着手 | /bundle/conversation | なし | なし | なし | true |" in template
    assert "## 横断の集計" in template
    saved = template.replace(" | 未着手 | ", " | 完了 | ").replace("採否集計: 未着手", "採否集計: 保存の値")
    path.write_text(saved, encoding="utf-8")
    assert session_review_analysis.ensure(writer) == path
    assert path.read_text(encoding="utf-8") == saved
    assert not session_review_analysis.unfinished(writer.path, writer.load())


def test_finish_distinguishes_same_session_id_in_two_hosts(tmp_path, monkeypatch, capsys):
    for host in ("claude", "codex"):
        assert session_review.record(
            {"hook_event_name": "SessionStart", "session_id": "same", "transcript_path": str(tmp_path / "same.jsonl")},
            host=host,
        )
    writer = session_review.BatchWriter(tmp_path / "batch.json")
    writer.create(
        {
            "run_id": "batch",
            "targets": session_review.pending(),
            "started_at": "2026-11-27T00:00:00Z",
            "target_repo": "github.com/example/repo",
            "prepared": [{"host": "codex", "session_id": "same", "partial_analysis": True}],
        }
    )
    monkeypatch.setenv(session_review.BATCH_ENV, str(writer.path))
    monkeypatch.setattr(session_review_state.add_reports, "check_run_report_sync", lambda *args, **kwargs: None)
    path = session_review_analysis.ensure(writer)
    text = path.read_text(encoding="utf-8")
    assert "- 開始時刻: 2026-11-27T00:00:00Z" in text
    assert "- 報告先: github.com/example/repo" in text
    text = text.replace("| claude:same | 未着手 |", "| claude:same | 完了 |")
    path.write_text(text, encoding="utf-8")
    assert session_review_state.main(["finish"]) == 1
    error = capsys.readouterr().err
    assert "codex:same" in error and "claude:same" not in error
    path.write_text(text.replace("| codex:same | 未着手 |", "| codex:same | 完了 |"), encoding="utf-8")
    assert session_review_state.main(["finish"]) == 0
    assert writer.load().analysis_succeeded
