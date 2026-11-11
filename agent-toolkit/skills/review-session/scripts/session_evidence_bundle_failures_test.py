"""セッション振り返りの試験（session_evidence_bundle_failures_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    agy_delegation_transcript,
    agy_step,
    assistant_text,
    events_by_kind,
    read_jsonl,
    write_agy_log,
)
from agent_toolkit._testing.session_review_evidence_support import _bundle_delegate_return_texts


@pytest.mark.parametrize(
    ("commands", "diagnostics", "expected_candidates"),
    (
        ([["rg", "-F", "one"], ["rg", "-F", "two"]], ["", ""], 0),
        ([["test", "-e", "/one"], ["test", "-e", "/two"]], ["", ""], 0),
        ([["rg", "-F", "one"], ["rg", "-F", "one"]], ["", ""], 0),
        ([["tool", "one"], ["tool", "two"]], ["same diagnostic", "same diagnostic"], 2),
        ([["tool", "one"], ["tool", "two"]], ["first diagnostic", "second diagnostic"], 2),
    ),
)
def test_codex_failed_commands_group_by_executable_exit_and_diagnostic(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    commands: list[list[str]],
    diagnostics: list[str],
    expected_candidates: int,
) -> None:
    """正常な偽判定は除外し、診断を伴う失敗を既存軸で集約する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "failed",
                        "command": command,
                        "exit_code": 1,
                        "stderr": diagnostic,
                    },
                },
            }
            for command, diagnostic in zip(commands, diagnostics, strict=True)
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys, raw=True)
    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    candidates = [record for record in records if record["kind"] == "candidate"]

    assert len(candidates) == expected_candidates
    if expected_candidates:
        assert {candidate["candidate_kind"] for candidate in candidates} == {"command-failure"}
        assert sum(candidate["count"] for candidate in candidates) == 2
        assert sum(len(candidate["locators"]) for candidate in candidates) == 2


def test_bundle_excludes_transient_classifier_failure(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """auto mode classifierの一時的な判定不能は候補にせず専用区分へ数える。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "c1", "input": {"command": "true"}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c1",
                            "is_error": True,
                            "content": "The server-side auto mode classifier gave no verdict (error)",
                        }
                    ],
                },
            },
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys)
    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]

    assert not [item for item in records if item["kind"] == "candidate"]
    assert records[-1]["excluded"]["runtime-transient"] == 1


def test_codex_failed_commands_without_diagnostic_or_command_use_record_position(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """診断とcommandを欠く失敗は異なる記録位置を同一候補へ集約しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "CommandExecution", "status": "failed", "exit_code": 1},
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "CommandExecution", "status": "failed", "exit_code": 1},
                },
            },
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys, raw=True)
    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]

    assert len([record for record in records if record["kind"] == "candidate"]) == 2


def test_agy_delegate_failures_become_candidates_and_stats(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """agyの委譲先の失敗したツール、エラー報告および失敗終了が候補へ現れ、件数とトークン数が集計へ現れる。"""
    session_id = "a9244465-1577-44a9-a7b0-500b1f976b0e"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    run_command = {"CommandLine": "npx textlint README.md"}
    write_agy_log(
        tmp_path / "state",
        session_id,
        [
            {"event": "init", "conversation_id": session_id, "init": {"model": "gemini", "cwd": "/work"}},
            agy_step(
                1, "tool", "ACTIVE", tool_name="run_command", tool_info={"name": "run_command", "parameters": run_command}
            ),
            agy_step(
                1,
                "tool",
                "ERROR",
                tool_name="run_command",
                tool_info={
                    "name": "run_command",
                    "parameters": run_command,
                    "error": {"type": "TOOL_ERROR", "message": "sandbox server did not answer Run within 30s"},
                },
            ),
            agy_step(
                2,
                "tool",
                "DONE",
                tool_name="view_file",
                tool_info={"name": "view_file", "parameters": {"AbsolutePath": "/work/a.md"}, "output": "本文"},
            ),
            agy_step(3, "error_message", "DONE"),
            agy_step(
                4,
                "agent_response",
                "DONE",
                usage={"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 300, "total_tokens": 120},
            ),
            {
                "event": "result",
                "result": {
                    "conversation_id": session_id,
                    "status": "ERROR",
                    "error": "Individual quota reached.",
                    "response": "",
                },
            },
        ],
    )
    transcript = agy_delegation_transcript(tmp_path, session_id)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys, raw=True)
    candidates = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    failures = [
        item["locators"] for item in candidates if item["kind"] == "candidate" and item["candidate_kind"] == "tool-failure"
    ]
    record_id = f"agy:{session_id}"
    assert sorted(locator["line"] for locators in failures for locator in locators if locator["record"] == record_id) == [
        3,
        5,
        7,
    ]

    assert evidence.main([str(transcript), "--stats"]) == 0
    stats = read_jsonl(capsys, raw=True)
    thread = events_by_kind(stats, "stats-agent-thread")[0]
    assert (thread["engine"], thread["session_id"]) == ("agy", session_id)
    assert thread["tokens"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 300,
    }
    total = events_by_kind(stats, "stats-total")[0]
    assert total["agent_thread_counts"] == {"agy": 1}
    assert total["tokens"]["cache_read_input_tokens"] == 300
    assert not events_by_kind(stats, "unresolved-record")


@pytest.mark.parametrize(
    ("final_text", "expected"),
    [
        ("status: completed\noutput_file: /tmp/out.md", []),
        ("状態: completed\n未解決の指摘数: 0\n続行できない理由: なし", []),
        (
            "統合完了\nmerged_head: abc1234\n想定外事象: 統合後の検査が1件失敗した",
            ["統合完了\nmerged_head: abc1234\n想定外事象: 統合後の検査が1件失敗した"],
        ),
        *[
            (f"状態: completed\n未解決の指摘数: 0\n{role}: 指摘なし", [])
            for role in ("読者別探索", "一括置換後レビュー", "投稿前レビュー")
        ],
        *[
            (text, [text])
            for role in ("読者別探索", "一括置換後レビュー", "投稿前レビュー")
            for text in (
                f"状態: completed\n未解決の指摘数: 1\n{role}: 指摘あり",
                f"未解決の指摘数: 0\n続行できない理由: {role}の入力不足",
                f"状態: needs_escalation\n未解決の指摘数: 0\n続行できない理由: {role}の入力不足",
                f"状態: completed\n未解決の指摘数: 0\n{role}: 指摘なし\n想定外事象: 検証に失敗",
            )
        ],
    ],
)
def test_bundle_excludes_delegate_returns_that_only_report_success(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    final_text: str,
    expected: list[str],
) -> None:
    """`--bundle`の候補から成功の定型形式だけの返却を除き、想定外事象を持つ定型返却は残す。"""
    texts = _bundle_delegate_return_texts(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "委譲された依頼"}},
            assistant_text(final_text),
        ],
    )
    capsys.readouterr()

    assert texts == expected
