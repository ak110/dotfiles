"""失敗AWIの削除・版変更と、ホストを越えて利用する失敗証拠を検証する。"""

from __future__ import annotations

import datetime
import json
import pathlib
import platform
import sys

import pytest

from agent_toolkit._atk import run_skill_batch, run_skill_batch_evidence, run_skill_batch_test
from agent_toolkit._common import session_review


@pytest.fixture(autouse=True)
def _manual_identity(monkeypatch):
    monkeypatch.delenv(session_review.LAUNCH_ENV, raising=False)


@pytest.mark.parametrize("state", ["adopted", "rejected"])
def test_deleted_failure_waits_for_new_version_without_duplicate_report(tmp_path, monkeypatch, state):
    notes = run_skill_batch_test._notes()  # pylint: disable=protected-access
    run_skill_batch_test._target(tmp_path, "one")  # pylint: disable=protected-access
    calls = run_skill_batch_test._fail_prepare_for(monkeypatch, {"one"})  # pylint: disable=protected-access
    version = "1.0"
    monkeypatch.setattr(run_skill_batch_evidence, "plugin_version", lambda root: version)
    monkeypatch.setattr(run_skill_batch.time, "time", lambda: datetime.datetime.now(datetime.UTC).timestamp() + 21600)

    def fail(_args, **kwargs):
        del kwargs
        raise AssertionError("準備失敗した対象ではモデルを起動しない")

    def dispatch():
        return run_skill_batch.dispatch(run_skill_batch_test._args(), fail)  # pylint: disable=protected-access

    assert dispatch() == 1
    report = run_skill_batch_test._failure_awis(notes)[0]  # pylint: disable=protected-access
    body = report.read_text(encoding="utf-8")
    key = session_review.pending()[0]["failure_key"]
    assert session_review.pending()[0]["failure_version"] == "1.0"
    version = "2.0"
    assert dispatch() == 0 and len(calls) == 1  # 未完了AWIは版にかかわらず抑止する。
    version = "1.0"
    report.unlink()
    assert dispatch() == 0 and len(calls) == 1
    session_review.sweep(now=datetime.datetime.now(datetime.UTC).timestamp() + 365 * 86400)
    assert session_review.failure_reported(key)
    version = "2.0"
    assert dispatch() == 1 and len(calls) == 2
    assert not run_skill_batch_test._failure_awis(notes)  # pylint: disable=protected-access
    assert session_review.pending()[0]["failure_version"] == "2.0"
    report.write_text(body.replace("state: inbox\n", f"state: {state}\n", 1), encoding="utf-8")
    assert dispatch() == 1 and len(calls) == 3
    reports = run_skill_batch_test._failure_awis(notes)  # pylint: disable=protected-access
    assert len(reports) == 2
    assert sum("state: inbox\n" in path.read_text(encoding="utf-8") for path in reports) == 1


def test_legacy_failure_version_is_saved_once_and_new_generation_is_preserved(tmp_path, monkeypatch):
    run_skill_batch_test._notes()  # pylint: disable=protected-access
    run_skill_batch_test._target(tmp_path, "one")  # pylint: disable=protected-access
    calls = run_skill_batch_test._fail_prepare_for(monkeypatch, {"one"})  # pylint: disable=protected-access
    target = session_review.pending()[0]
    pending = session_review.directory() / "pending" / f"{target['key']}.json"
    session_review.write(pending, {**target, "failure_key": "legacy"})
    version = "1.0"
    monkeypatch.setattr(run_skill_batch_evidence, "plugin_version", lambda root: version)
    monkeypatch.setattr(run_skill_batch.time, "time", lambda: datetime.datetime.now(datetime.UTC).timestamp() + 21600)

    def unexpected(_args, **kwargs):
        del kwargs
        raise AssertionError("抑止中または準備失敗の対象ではモデルを起動しない")

    def dispatch():
        return run_skill_batch.dispatch(run_skill_batch_test._args(), unexpected)  # pylint: disable=protected-access

    assert dispatch() == 0 and not calls
    assert session_review.pending()[0]["failure_version"] == "1.0"
    prior = session_review.pending()[0]
    version = "2.0"
    assert dispatch() == 1 and len(calls) == 1
    assert session_review.pending()[0]["failure_version"] == "2.0"
    session_review.write(pending, {**prior, "generation": "new-generation", "failure_version": "3.0"})
    assert session_review.failure_version(prior, "2.0") == "1.0"
    assert session_review.pending()[0]["failure_version"] == "3.0"


def test_missing_record_does_not_discard_reregistered_generation(tmp_path, monkeypatch):
    run_skill_batch_test._target(tmp_path, "one")  # pylint: disable=protected-access
    transcript = tmp_path / "one.jsonl"
    transcript.unlink()
    discard = session_review.discard_pending
    generations = []

    def reregister(target, **kwargs):
        del kwargs
        generations.append(target["generation"])
        session_review.record(
            {"hook_event_name": "SessionStart", "session_id": "one", "transcript_path": str(transcript)}, host="claude"
        )
        return discard(target)

    monkeypatch.setattr(session_review, "discard_pending", reregister)
    assert run_skill_batch.dispatch(run_skill_batch_test._args("manual"), lambda args: 99) == 0  # pylint: disable=protected-access
    assert len(generations) == 1
    assert session_review.pending()[0]["generation"] != generations[0]
    assert not session_review.pending()[0].get("failure_key")


@pytest.mark.parametrize("phase", ["prepare", "model"])
def test_failure_body_survives_manifest_and_log_removal(tmp_path, phase):
    notes = run_skill_batch_test._notes()  # pylint: disable=protected-access
    run_skill_batch_test._target(tmp_path, "one")  # pylint: disable=protected-access
    target = session_review.pending()[0]
    transcript = tmp_path / "one.jsonl"
    log = tmp_path / "model.log"
    if phase == "prepare":
        transcript.unlink()
        transcript.mkdir()
    else:
        log.write_text(
            "old-prefix\n" * 2000
            + '[stdout] {"type":"assistant","message":"会話本文の検出用"}\n'
            + '[stdout] {"type":"error","message":"モデル起動後の診断"}\n'
            + "[stderr] 回収失敗\n終了状態: exit code 7\n",
            encoding="utf-8",
        )

    def fail(args, **kwargs):
        del kwargs
        path = pathlib.Path(args.batch_manifest)
        session_review.write(path, {**session_review.read(path), "log_path": str(log)})
        return 7

    assert run_skill_batch.dispatch(run_skill_batch_test._args("manual"), fail) == (1 if phase == "prepare" else 7)  # pylint: disable=protected-access
    report = run_skill_batch_test._failure_awis(notes)[0]  # pylint: disable=protected-access
    for manifest in (session_review.directory() / "runs").glob("*.json"):
        manifest.unlink()
    log.unlink(missing_ok=True)
    body = report.read_text(encoding="utf-8")
    details = json.loads("\n".join(line[4:] for line in body.splitlines() if line.startswith("    ")))
    assert details["hostname"] == platform.node() and details["os"] == sys.platform
    assert details["plugin_version"]
    snapshot = details["targets"][0]
    for field in (
        "host",
        "session_id",
        "transcript_path",
        "cwd",
        "target_repo",
        "started_at",
        "updated_at",
        "generation",
        "review_batch",
    ):
        assert snapshot[field] == target[field]
    assert snapshot["record_state"]["kind"] == ("ディレクトリ" if phase == "prepare" else "ファイル")
    assert details["prepared_count"] == (0 if phase == "prepare" else 1)
    assert details["prepared_session_ids"] == ([] if phase == "prepare" else ["one"])
    if phase == "prepare":
        assert "prepareが終了2" in details["error"] and "不足: transcript_path" in details["error"]
        assert "ログなし" in details["log"]["status"]
    else:
        assert details["exit_code"] == 7 and details["log"]["truncated"]
        assert "モデル起動後の診断" in details["log"]["stdout_stderr_tail"]
        assert "[stderr] 回収失敗" in details["log"]["stdout_stderr_tail"]
        assert snapshot["record_state"]["size_bytes"] == transcript.stat().st_size
        assert snapshot["record_state"]["modified_at"]
    assert "初期要求" not in body and "会話本文の検出用" not in body


def test_unreported_failure_is_recovered_before_any_retry(tmp_path, monkeypatch):
    notes = run_skill_batch_test._notes()  # pylint: disable=protected-access
    run_skill_batch_test._target(tmp_path, "one")  # pylint: disable=protected-access
    prepared = run_skill_batch_test._fail_prepare_for(monkeypatch, {"one"})  # pylint: disable=protected-access
    original = run_skill_batch.add.add_entries

    def unavailable(*args, **kwargs):
        raise OSError("起票先を一時的に利用できない")

    def unexpected(args, **kwargs):
        raise AssertionError("未報告失敗の回復でモデルを再起動しない")

    monkeypatch.setattr(run_skill_batch.add, "add_entries", unavailable)
    assert run_skill_batch.dispatch(run_skill_batch_test._args(), unexpected) == 1  # pylint: disable=protected-access
    pending = session_review.pending()[0]
    assert pending["failure_unreported"] and not session_review.failure_reported(pending["failure_key"])
    monkeypatch.setattr(run_skill_batch.add, "add_entries", original)
    assert run_skill_batch.dispatch(run_skill_batch_test._args(), unexpected) == 1  # pylint: disable=protected-access
    assert not session_review.pending()[0]["failure_unreported"]
    assert session_review.failure_reported(pending["failure_key"])
    assert len(run_skill_batch_test._failure_awis(notes)) == 1  # pylint: disable=protected-access
    assert prepared == ["one"]


def test_dangling_record_link_remains_a_prepare_failure(tmp_path):
    notes = run_skill_batch_test._notes()  # pylint: disable=protected-access
    run_skill_batch_test._target(tmp_path, "one")  # pylint: disable=protected-access
    transcript = tmp_path / "one.jsonl"
    transcript.unlink()
    try:
        transcript.symlink_to(tmp_path / "missing.jsonl")
    except OSError as error:
        pytest.skip(f"このホストでリンク作成権限が無い: {error}")
    assert run_skill_batch.dispatch(run_skill_batch_test._args("manual"), lambda args: 99) == 1  # pylint: disable=protected-access
    assert len(session_review.pending()) == 1
    assert len(run_skill_batch_test._failure_awis(notes)) == 1  # pylint: disable=protected-access
