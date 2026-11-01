"""公開prepareが1エージェント向けの会話の流れを生成することを検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence
import session_review_prepare as prepare

from agent_toolkit._testing import delegated_threads
from agent_toolkit._testing import session_evidence_support as support


def test_prepare_conversation_summarizes_main_and_delegate_records(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """メインの各ユーザー発話と委譲先の目的・返却・操作量を1文書へ縮約する。"""
    parent = tmp_path / "parent.jsonl"
    support.write_jsonl(
        parent,
        [
            support.timestamped_entry("2026-10-10T00:00:00Z", "最初の要求"),
            support.assistant_text("調査を始める"),
            support.claude_call("2026-10-10T00:01:00Z", "read", "Read", {"file_path": "/repo/input.md"}),
            support.timestamped_entry("2026-10-10T00:02:00Z", "次の要求"),
            support.assistant_text("調査を終えた"),
            support.claude_call("2026-10-10T00:46:00Z", "failed", "Bash", {"command": "検査"}),
            {
                "type": "user",
                "timestamp": "2026-10-10T00:47:00Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "failed", "is_error": True, "content": "検査失敗"}],
                },
            },
        ],
    )
    support.write_subagent(
        parent.with_suffix("") / "subagents",
        "agent-child",
        [
            support.timestamped_entry("2026-10-10T00:10:00Z", "子への依頼"),
            support.claude_call("2026-10-10T00:10:01Z", "child-read", "Read", {"file_path": "/repo/child.md"}),
            support.assistant_text("子の返却"),
        ],
    )
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(parent), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    document = pathlib.Path(result["conversation_path"]).read_text(encoding="utf-8")
    assert "## メインの流れ" in document and "## 委譲先の流れ" in document
    assert "最初の要求" in document and "次の要求" in document
    assert "調査を始める" in document and "調査を終えた" in document
    assert "agent-child" in document and "子への依頼" in document and "子の返却" in document
    assert "失敗1件" in document
    assert result["conversation_utf8_bytes"] == len(document.encode("utf-8"))
    assert result["conversation_limit_bytes"] == 100_000
    assert "conversation_ranges" not in result and "reader_ranges" not in result and "main_known_scope" not in result
    comparison = pathlib.Path(result["comparison_materials_path"]).read_text(encoding="utf-8")
    assert "### claude:" in comparison and "agent-child" in comparison
    assert "/repo/child.md" in comparison


def test_prepare_lists_compactions_per_record(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """統計からCodexの各圧縮の記録位置へ到達できる。

    compactedの形は2026年10月10日のCodexの記録のtypeとtimestampを使う。
    """
    threads = delegated_threads.write_delegated_threads(tmp_path)
    monkeypatch.setenv("HOME", str(threads.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(threads.home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(threads.codex_home))
    codex = next(threads.codex_home.rglob("*.jsonl"))
    original = [json.loads(line) for line in codex.read_text(encoding="utf-8").splitlines()]
    for index in range(2):
        original.append(
            {
                "type": "compacted",
                "timestamp": f"2026-10-10T00:0{index}:00Z",
                "payload": {"compaction_response_id": f"saved-{index}"},
            }
        )
    support.write_jsonl(codex, original)
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(threads.transcript), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    stats = pathlib.Path(result["stats_path"]).read_text(encoding="utf-8")
    record = f"codex:{delegated_threads.CODEX_THREAD_ID}"
    assert f"記録{record}: 2回" in stats
    for index in range(2):
        locator = f"{record}:{len(original) - 1 + index}"
        assert locator in stats
        assert evidence.main([str(threads.transcript), "--detail", locator]) == 0
        output = capsys.readouterr().out
        assert "compacted" in output


@pytest.mark.parametrize("conversation_only", [False, True])
def test_prepare_conversation_stays_within_single_reader_limit(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], *, conversation_only: bool
) -> None:
    """多数の長い発話と委譲記録も1つの100,000バイト以下の概要になる。"""
    entries = [support.timestamped_entry(f"2026-10-19T00:{index:02}:00Z", "本文" * 5000) for index in range(40)]
    entries.insert(20, support.assistant_text("長い返却" * 40_000))
    parent = tmp_path / "parent.jsonl"
    support.write_jsonl(parent, entries)
    for index in range(120):
        support.write_subagent(
            parent.with_suffix("") / "subagents",
            f"agent-{index:03}",
            [
                support.timestamped_entry("2026-10-19T00:00:00Z", f"委譲{index}" + "入力" * 1000),
                support.claude_call("2026-10-19T00:00:01Z", f"call-{index}", "Read", {"file_path": f"/repo/{index}.md"}),
                support.assistant_text(f"返却{index}" + "結果" * 1000),
            ],
        )
    work = tmp_path / "work"
    work.mkdir()
    arguments = ["--transcript", str(parent), "--work-dir", str(work)]
    assert prepare.main([*arguments, "--conversation-only"] if conversation_only else arguments) == 0
    result = json.loads(capsys.readouterr().out)
    document = pathlib.Path(result["conversation_path"]).read_text(encoding="utf-8")
    assert len(document.encode("utf-8")) <= 100_000
    assert result["conversation_utf8_bytes"] == len(document.encode("utf-8"))
    assert result["conversation_limit_bytes"] == 100_000
    assert "reader_ranges" not in result and "conversation_ranges" not in result
    assert "agent-000" in document and "agent-119" in document
