"""公開状態操作とprepareを使い、待ち登録・増分・成功確定の受渡しを検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

from agent_toolkit._common import session_review


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
    """共通層から上位層をimportせず、公開CLIで状態操作と準備を実行する。"""
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
        "transcript_path": str(tmp_path / "old-id.jsonl"),
        "cwd": str(tmp_path),
    }
    assert session_review.record(payload, host=host)
    assert session_review.pending()[0]["session_id"] == "current-id"
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "parent")
    assert not session_review.record({**payload, "session_id": "delegate"}, host=host)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION")
    monkeypatch.setenv(session_review.BATCH_ENV, "manifest")
    assert not session_review.record({**payload, "session_id": "batch"}, host=host)
    assert len(session_review.pending()) == 1


@pytest.mark.parametrize("change", ["append", "resume", "new-input", "none"])
def test_prepare_complete_preserves_new_scope(change, tmp_path, capsys) -> None:
    transcript = tmp_path / "session.jsonl"
    _transcript(transcript, "初回要求")
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "session",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
    }
    session_review.record(payload, host="claude")
    prepared = _prepare(transcript, capsys)
    if change == "append":
        _transcript(transcript, "準備中の追記", append=True)
    elif change == "resume":
        session_review.record(payload, host="claude")
    session_review.complete(prepared)
    if change == "new-input":
        session_review.record({**payload, "hook_event_name": "UserPromptSubmit"}, host="claude")
    assert bool(session_review.pending()) == (change != "none")
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
    """準備と成功確定の間の書換えを、行数が同じでも処理済みにしない。"""
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
    session_review.record({"hook_event_name": "SessionStart", "session_id": "one"}, host="claude")
    target = session_review.pending()[0]
    run = session_review.directory() / "runs" / "failed.json"
    log = tmp_path / "failed.log"
    session_review.write(
        run,
        {"run_id": "failed", "status": "failed", "targets": [target], "report_synced": True, "log_path": str(log)},
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
