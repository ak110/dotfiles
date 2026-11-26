"""失敗AWIの削除・版変更と、ホストを越えて利用する失敗証拠を検証する。"""

from __future__ import annotations

import datetime
import io
import json
import pathlib
import sys
import threading

import pytest

from agent_toolkit._atk import run_skill, run_skill_batch, run_skill_batch_evidence, run_skill_batch_test
from agent_toolkit._atk.run_skill_progress import Progress
from agent_toolkit._common import session_activity, session_review


@pytest.fixture(autouse=True)
def _isolate_session_inputs(monkeypatch):
    monkeypatch.delenv(session_review.LAUNCH_ENV, raising=False)
    # 失敗の保存と復旧を検証する。実ホストの会話は合成した対象の入力に含めない。
    monkeypatch.setattr(session_activity, "host_processes", lambda: ())


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(
    1.5,
    reason=("実Gitの状態・履歴を使う結合境界。修正前Linux4並列全件計測の所有CPU最大0.772秒。"),
)
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
    assert dispatch() == 0 and len(calls) == 1
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


@pytest.mark.usefixtures("leaf_git_commands")
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


@pytest.mark.usefixtures("leaf_git_commands")
def test_removed_codex_rollouts_do_not_fail_batch_preparation(tmp_path, monkeypatch):
    notes = run_skill_batch_test._notes()  # pylint: disable=protected-access
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "default-codex"))
    for session_id in ("removed-first", "removed-second"):
        transcript = tmp_path / "isolated-codex" / "sessions" / "2026" / "11" / "26" / f"rollout-date-{session_id}.jsonl"
        session_review.record(
            {"hook_event_name": "SessionStart", "session_id": session_id, "transcript_path": str(transcript)},
            host="codex",
        )
    assert len(session_review.pending()) == 2

    def dispatch_model(_args):
        pytest.fail("記録が残っていない対象でモデルを起動した")

    assert run_skill_batch.dispatch(run_skill_batch_test._args("manual"), dispatch_model) == 0  # pylint: disable=protected-access
    assert not session_review.pending()
    assert not run_skill_batch_test._failure_awis(notes)  # pylint: disable=protected-access


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(1.25, reason="実Gitと準備スクリプトで別のCodexホームから報告確定まで検証する。")
@pytest.mark.parametrize("archived", [False, True])
def test_batch_resolves_registered_rollout_in_other_codex_home(tmp_path, monkeypatch, archived):
    # pylint: disable=protected-access
    notes = run_skill_batch_test._notes()
    session_id = "01a216c7-fe50-7361-878c-f507d87b537a"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "runner-codex"))
    registered = tmp_path / "other-codex" / "sessions" / "2026" / "11" / "26" / f"rollout-date-{session_id}.jsonl"
    source = registered.parents[4] / "archived_sessions" / registered.name if archived else registered
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps({"timestamp": "2026-11-26T07:35:35Z", "type": "session_meta", "payload": {"id": session_id}})
        + "\n"
        + json.dumps(
            {
                "timestamp": "2026-11-26T07:35:36Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "別のホームに保存した要求"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session_review.record(
        {"hook_event_name": "SessionStart", "session_id": session_id, "transcript_path": str(registered)},
        host="codex",
    )
    calls: list[dict] = []
    assert (
        run_skill_batch.dispatch(
            run_skill_batch_test._args("manual"), run_skill_batch_test._report_and_finish(notes, monkeypatch, calls)
        )
        == 0
    )
    assert [item["session_id"] for item in calls[0]["prepared"]] == [session_id]
    assert not session_review.pending()
    assert not run_skill_batch_test._failure_awis(notes)


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(
    1.0,
    reason=("実Gitの状態・履歴を使う結合境界。修正前Linux4並列全件計測の所有CPU最大0.352秒。"),
)
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
    assert details["os"] == sys.platform
    assert details["plugin_version"] and details["plugin_source"]["commit"]
    assert details["prepared_count"] == (0 if phase == "prepare" else 1)
    if phase == "prepare":
        assert details["stage"] == "prepare-or-state"
        snapshot = details["targets"][0]
        for field in ("host", "session_id", "target_repo", "review_batch", "started_at", "updated_at", "generation"):
            assert snapshot.get(field) == target.get(field)
        assert snapshot["record_location"] == "hookが報告した記録の場所"
        assert snapshot["record_state"]["kind"] == "ディレクトリ"
        assert "previous_scope" in snapshot
        assert "prepareが終了2" in details["error"] and "不足: transcript_path" in details["error"]
        assert "ログなし" in details["log"]["status"]
    else:
        assert details["stage"] == "run-skill-exit-7" and "targets" not in details
        assert details["exit_code"] == 7 and details["log"]["tail_truncated"]
        assert "モデル起動後の診断" in "\n".join(details["log"]["error_events"])
        assert "[stderr] 回収失敗" in details["log"]["tail"]
    assert str(tmp_path) not in body and "実行記録: " not in body and "実行ログ: " not in body
    assert "初期要求" not in body and "会話本文の検出用" not in body


@pytest.mark.usefixtures("leaf_git_commands")
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


@pytest.mark.usefixtures("leaf_git_commands")
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


def test_model_failure_keeps_diagnostics_beyond_log_tail(tmp_path):
    log = tmp_path / "model.log"
    conversation = '[stdout] {"type":"assistant","message":"会話本文の検出用' + "x" * 200 + '"}\n'
    log.write_text(
        "開始時刻: 2026-11-18T00:00:00+09:00\n"
        "スキル: agent-toolkit:review-session\n"
        "採用した候補: claude/opus\n"
        '[stdout] {"type":"turn.failed","error":{"message":"上流が切断した"}}\n'
        '[stdout] {"type":"result","subtype":"error_max_turns","is_error":true,"num_turns":40}\n'
        + conversation * 100
        + "[stderr] 回収失敗 token=abc123secret\n"
        + "終了状態: exit code 7\n",
        encoding="utf-8",
    )
    manifest = {
        "targets": [{"session_id": "one", "transcript_path": str(tmp_path / "none.jsonl")}],
        "error": "run-skill-exit-7: 振り返りを完了できなかった",
        "exit_code": 7,
        "log_path": str(log),
    }
    body = run_skill_batch_evidence.failure_details(manifest)
    details = json.loads("\n".join(line[4:] for line in body.splitlines()[2:]))["log"]
    assert details["header"] == [
        "開始時刻: 2026-11-18T00:00:00+09:00",
        "スキル: agent-toolkit:review-session",
        "採用した候補: claude/opus",
    ]
    events = "\n".join(details["error_events"])
    assert "上流が切断した" in events and "error_max_turns" in events
    assert details["conversation_lines_omitted"] == 100
    assert "終了状態: exit code 7" in details["tail"] and "回収失敗" in details["tail"]
    assert "abc123secret" not in body and "会話本文の検出用" not in body


def test_failure_details_keep_previous_scope_and_redact_error(tmp_path):
    """準備に渡す前回の処理範囲を本文へ固定し、失敗理由の秘匿値を伏せる。"""
    run_skill_batch_test._target(tmp_path, "one")  # pylint: disable=protected-access
    target = session_review.pending()[0]
    scope = {"records": {"main": {"through_line": 12, "digest": "d" * 64}}}
    session_review.write(
        session_review.directory() / "reviewed" / f"{session_review.key(target['host'], target['session_id'])}.json", scope
    )
    manifest = {"targets": [target], "error": "prepare-or-state: prepareが終了2: password=hunter2 不足: 範囲"}
    body = run_skill_batch_evidence.failure_details(manifest)
    details = json.loads("\n".join(line[4:] for line in body.splitlines()[2:]))
    assert details["targets"][0]["previous_scope"] == scope
    assert "不足: 範囲" in details["error"] and "hunter2" not in body


def test_reissuing_failure_uses_original_plugin_source(tmp_path):
    path = tmp_path / "batch.json"
    writer = session_review.BatchWriter(path)
    writer.create({"run_id": "original", "targets": []})
    original = {"version": "2.297.0", "commit": "a" * 40, "source": "Git"}
    writer.fail("failure", "finish: report-unsynced", version="2.297.0", source=original)
    retry = writer.fail(
        "failure", "finish: report-unsynced", version="2.300.0", source={"version": "2.300.0", "commit": "b" * 40}
    )
    assert retry["failure_source"] == original
    assert retry["failure_version"] == "2.297.0"
    body = run_skill_batch_evidence.failure_details(retry)
    assert "a" * 40 in body and "b" * 40 not in body
    legacy = run_skill_batch_evidence.failure_details({"targets": [], "failure_version": "2.297.0"})
    assert '"source": "不明"' in legacy


def _log_details(tmp_path, text):
    log = tmp_path / "model.log"
    log.write_text(text, encoding="utf-8")
    manifest = {"targets": [], "error": "run-skill-exit-7", "exit_code": 7, "log_path": str(log)}
    body = run_skill_batch_evidence.failure_details(manifest)
    return body, json.loads("\n".join(line[4:] for line in body.splitlines()[2:]))["log"]


_HUGE = "y" * 200_000
_LONG_EVENT = '[stdout] {"type":"result","is_error":true,"detail":"' + "y" * 3000 + '"}\n'


@pytest.mark.parametrize(
    "text",
    [
        f"終了状態: {_HUGE}\n[stderr] 終了\n",
        f"[stderr] 開始\n[stderr] {_HUGE}\n",
        "[stderr] 開始\n" + _LONG_EVENT * 30,
        f"終了状態: {_HUGE}\n" + _LONG_EVENT * 30 + f"[stderr] {_HUGE}\n",
    ],
    ids=["header", "tail", "events", "all"],
)
def test_log_diagnostics_stay_within_previous_tail_budget(tmp_path, text):
    _body, details = _log_details(tmp_path, text)
    excerpt = [*details["header"], *details["error_events"], details["tail"]]
    assert sum(len(line.encode()) for line in excerpt) <= 8192
    assert any(_HUGE[:100] in line for line in excerpt)


def test_log_diagnostics_redact_before_truncating_lines(tmp_path):
    token = "ghp_" + "A" * 40
    prefix = '{"type":"result","is_error":true,"detail":"'
    filler = "x" * (1990 - len(prefix) - len(" https://"))
    payload = f"{prefix}{filler} https://{token}@github.com/owner/repo" + '"}'
    stderr = f"[stderr] {filler} https://{token}@github.com"
    body, details = _log_details(tmp_path, f"[stdout] {payload}\n{stderr}\n[stderr] 正常な行\n")
    assert "ghp_" not in body
    assert details["error_events"] == [f"{prefix}{filler} https://[REDACTED]"]
    assert details["tail"].endswith("\n[stderr] 正常な行")
    assert "[REDACTED]" in details["tail"]


@pytest.mark.parametrize(
    ("log_kind", "status"),
    [
        ("not-recorded", "実行ログの所在が記録されていない"),
        ("missing", "読取不能"),
        ("empty", "ログに採用できる診断がない"),
    ],
)
def test_meta_log_evidence_distinguishes_unavailable_inputs(tmp_path, log_kind, status):
    log = None if log_kind == "not-recorded" else tmp_path / "model.log"
    if log_kind == "empty":
        assert log is not None
        log.touch()
    evidence = run_skill_batch_evidence.meta_failure_evidence(
        run_skill_batch.PLUGIN_ROOT, stage="execution-3", detail=None, log_path=log, report_target=None
    )
    diagnostics = evidence["failure_diagnostics"]
    assert diagnostics["log"]["status"] == status
    assert diagnostics["error"] is None


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r"])
def test_terminal_progress_is_not_reproduced_in_batch_or_meta_failure(tmp_path, monkeypatch, engine, separator):
    terminal = io.StringIO()
    monkeypatch.setattr(terminal, "isatty", lambda: True)
    text = f"転載しない発話の先頭{separator}転載しない発話の続き"
    event = (
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
        if engine == "claude"
        else {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
    )
    error = {"type": "error", "message": "残す診断"}
    source = io.BytesIO(("\n".join(json.dumps(row, ensure_ascii=False) for row in (event, error)) + "\n").encode())
    path = tmp_path / "log.txt"
    with path.open("w", encoding="utf-8") as log:
        progress = Progress(log, terminal)
        run_skill._copy_stream(source, "stdout", log, threading.Lock(), progress, engine)  # pylint: disable=protected-access
        log.write("終了状態: exit code 3\n")
    assert text in terminal.getvalue()
    assert "assistant: 転載しない発話" not in path.read_text(encoding="utf-8")
    details = run_skill_batch_evidence.meta_failure_evidence(
        run_skill_batch.PLUGIN_ROOT, stage="execution-3", detail=None, log_path=path, report_target=None
    )
    bodies = [
        run_skill_batch_evidence.meta_failure_message({**details, "failure_key": "test", "failure_stage": "execution-3"}),
        run_skill_batch_evidence.failure_message(
            "test", "run-skill-exit-3: 失敗", {"targets": [], "log_path": str(path)}, "github.com/example/repo"
        ),
    ]
    for body in bodies:
        assert "転載しない発話" not in body
        assert "残す診断" in body and "終了状態: exit code 3" in body


@pytest.mark.usefixtures("leaf_git_commands")
def test_reissuing_legacy_batch_failure_does_not_copy_terminal_projection(tmp_path, monkeypatch):
    notes = run_skill_batch_test._notes()  # pylint: disable=protected-access
    run_skill_batch_test._target(tmp_path, "one")  # pylint: disable=protected-access
    log = tmp_path / "legacy.log"
    model_text = "旧ログの会話先頭\n[stderr] 旧ログの会話続き"
    event = {"type": "assistant", "message": {"content": [{"type": "text", "text": model_text}]}}
    log.write_text(
        f"[stdout] {json.dumps(event, ensure_ascii=False)}\nassistant: {model_text}\n"
        "[stderr] 旧版のエラー\n終了状態: exit code 3\n",
        encoding="utf-8",
    )
    version, oid = "0.0.1", "a" * 40
    monkeypatch.setattr(run_skill_batch_evidence, "plugin_version", lambda _root: version)
    monkeypatch.setattr(run_skill_batch_evidence, "plugin_source", lambda _root, _version: {"version": version, "commit": oid})
    add_entries = run_skill_batch.add.add_entries

    def unavailable(*_args, **_kwargs):
        raise OSError("起票先を一時的に利用できない")

    def fail(args):
        path = pathlib.Path(args.batch_manifest)
        session_review.write(path, {**session_review.read(path), "log_path": str(log)})
        return 3

    def unexpected(_args):
        pytest.fail("保存だけの再試行でモデルを再起動した")

    monkeypatch.setattr(run_skill_batch.add, "add_entries", unavailable)
    args = run_skill_batch_test._args("manual")  # pylint: disable=protected-access
    assert run_skill_batch.dispatch(args, fail) == 3
    assert session_review.pending()[0]["failure_unreported"]
    assert not run_skill_batch_test._failure_awis(notes)  # pylint: disable=protected-access
    monkeypatch.setattr(run_skill_batch.add, "add_entries", add_entries)
    version, oid = "0.0.2", "b" * 40
    assert run_skill_batch.dispatch(args, unexpected) == 1
    [report] = run_skill_batch_test._failure_awis(notes)  # pylint: disable=protected-access
    body = report.read_text(encoding="utf-8")
    assert "0.0.1" in body and "a" * 40 in body
    assert "旧版のエラー" in body and "終了状態: exit code 3" in body
    assert "旧ログの会話" not in body
