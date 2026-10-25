"""SessionStartの実記録解決と、既存抽出CLIとの圧縮復元連携を検証する。"""

import argparse
import json
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._hooks import rules_context


@pytest.mark.parametrize("candidates", [0, 1, 2])
def test_session_start_resolves_exact_record(
    candidates: int, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """再開時の不存在入力は同一IDの実記録だけで解決し、曖昧さを通知する。"""
    config = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *args, **kwargs: tmp_path)
    found = []
    for index in range(candidates):
        path = config / "projects" / f"project-{index}" / "resume-test.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("", encoding="utf-8")
        found.append(path)
    missing = tmp_path / "missing.jsonl"
    assert (
        rules_context.main(
            json.dumps(
                {
                    "hook_event_name": "SessionStart",
                    "source": "resume",
                    "session_id": "resume-test",
                    "transcript_path": str(missing),
                }
            )
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    if candidates < 2:
        assert f"この会話のtranscript: {found[0] if found else missing}" in output
    else:
        assert "--claude-session-id" in output
        assert str(missing) not in output
        assert all(str(path) not in output for path in found)


@pytest.mark.parametrize("delegated", [False, True])
def test_compact_extracts_original_user_events(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], delegated: bool
) -> None:
    """複数の圧縮をまたぐ要求の原文と所在を保持し、生成入力を除く。"""
    path = tmp_path / "transcript.jsonl"
    entries = [
        {"type": "user", "sessionId": "compact-test", "message": {"role": "user", "content": "最初の要求を全部保持する"}},
        {"type": "system", "subtype": "compact_boundary"},
        {"type": "user", "message": {"role": "user", "content": '<atk-auto source="hook" kind="notice">通知</atk-auto>'}},
        {"type": "user", "message": {"role": "user", "content": "範囲を狭めず例も確認する"}},
    ]
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    else:
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *args, **kwargs: tmp_path)
    rules_context.main(
        json.dumps(
            {"hook_event_name": "SessionStart", "source": "compact", "session_id": "compact-test", "transcript_path": str(path)}
        )
    )
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "--conversation-only" in output
    saved = tmp_path / "compaction-user-events.jsonl"
    assert saved.exists() is not delegated
    if not delegated:
        events = [json.loads(line) for line in saved.read_text(encoding="utf-8").splitlines()]
        assert [event["text"] for event in events] == ["最初の要求を全部保持する", "範囲を狭めず例も確認する"]
        assert [event["line"] for event in events] == [1, 4]
        assert all(event["record"] for event in events)


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_extraction_keeps_resume_and_removes_stale_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cleanup_fails: bool
) -> None:
    """抽出の時間上限でも他の規範と手動の再取得コマンドを届ける。"""
    path = tmp_path / "transcript.jsonl"
    path.write_text("", encoding="utf-8")
    saved = tmp_path / "compaction-user-events.jsonl"
    saved.write_text("前の抽出", encoding="utf-8")
    original_unlink = pathlib.Path.unlink

    def unlink(path: pathlib.Path, *args: Any, **kwargs: Any) -> None:
        if cleanup_fails and path == saved:
            raise PermissionError("前回の抽出を削除できない")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", unlink)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired("extract", 15)

    monkeypatch.setattr(rules_context.process_tree, "run_with_timeout", fail)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *args, **kwargs: tmp_path)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    assert (
        rules_context.main(
            json.dumps(
                {
                    "hook_event_name": "SessionStart",
                    "source": "compact",
                    "session_id": "compact-test",
                    "transcript_path": str(path),
                }
            )
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "TimeoutExpired" in output and "--user-events" in output
    assert rules_context.RESPONSE_LANGUAGE_NOTICE in output
    assert saved.exists() is cleanup_fails
    if cleanup_fails:
        assert "前回の抽出ファイル" in output and "使用しない" in output


@pytest.mark.parametrize("source", ["startup", "resume", "clear"])
def test_noncompact_session_start_does_not_extract(
    source: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """圧縮以外の開始では、原文抽出の起動と保存を追加しない。"""
    path = tmp_path / "transcript.jsonl"
    path.write_text("{}\n", encoding="utf-8")

    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("圧縮以外の開始で抽出を起動した")

    monkeypatch.setattr(rules_context.process_tree, "run_with_timeout", unexpected)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *args, **kwargs: tmp_path)
    assert (
        rules_context.main(
            json.dumps(
                {"hook_event_name": "SessionStart", "source": source, "session_id": "noncompact", "transcript_path": str(path)}
            )
        )
        == 0
    )
    assert "--conversation-only" not in capsys.readouterr().out
    assert not (tmp_path / "compaction-user-events.jsonl").exists()


def test_resolved_resume_notice_path_is_accepted_by_prepare(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """再開通知が解決した実パスだけを、公開prepareへ渡して会話を取得する。"""
    config = tmp_path / "claude"
    path = config / "projects/old-directory/resume-test.jsonl"
    path.parent.mkdir(parents=True)
    entry = {
        "type": "user",
        "sessionId": "resume-test",
        "cwd": str(tmp_path),
        "message": {"role": "user", "content": "この会話の原文を保持する"},
    }
    path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *args, **kwargs: tmp_path)
    rules_context.main(
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "source": "resume",
                "session_id": "resume-test",
                "transcript_path": str(tmp_path / "missing.jsonl"),
            }
        )
    )
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    notified = next(
        line.removeprefix("この会話のtranscript: ")
        for line in context.splitlines()
        if line.startswith("この会話のtranscript: ")
    )
    destination = tmp_path / "conversation"
    destination.mkdir()
    assert (
        run_script.dispatch(
            argparse.Namespace(
                script_name="session-review-prepare",
                script_args=["--transcript", notified, "--work-dir", str(destination), "--conversation-only"],
            )
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    conversation = pathlib.Path(prepared["conversation_path"]).read_text(encoding="utf-8")
    assert prepared["conversation_utf8_bytes"] == len(conversation.encode("utf-8"))
    assert prepared["conversation_limit_bytes"] == 100_000
    assert "この会話の原文を保持する" in conversation
